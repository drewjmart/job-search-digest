"""
Y Combinator's Work at a Startup source (https://www.workatastartup.com).

robots.txt for this host is fully permissive (`Disallow:` with no path), so
nothing is skipped there — but we still check it at runtime for consistency
and in case that ever changes (verified manually 2026-08-18).

No login required to browse: category and individual job pages are publicly
served, server-rendered as an Inertia.js `data-page="{...}"` JSON payload
(HTML-entity-encoded) rather than plain HTML — see http_utils.extract_inertia_page.
A plain requests.get() without a real Accept header 406s here, which is why
http_utils.new_session() sets one.

YC's own category taxonomy is coarse (engineering-focused); there's no
dedicated "Chief of Staff" or "Program Manager" bucket. We fetch the two
closest categories (operations, product-manager) and let our own
title-keyword prefilter (profile.TARGET_TITLE_KEYWORDS) do the real
filtering, same approach as the other two sources.
"""

import re
import sys
import urllib.robotparser
from datetime import date
from pathlib import Path
from urllib.parse import urljoin

sys.path.insert(0, str(Path(__file__).parent.parent))

from http_utils import extract_inertia_page, html_to_text, new_session, polite_get  # noqa: E402
from models import Job  # noqa: E402
from profile import TARGET_TITLE_KEYWORDS  # noqa: E402

SOURCE = "workatastartup"
BASE = "https://www.workatastartup.com"

CATEGORY_SLUGS = ["operations", "product-manager"]

# Batches within this many years of today count as "recently funded" for the
# scoring bonus (YC doesn't expose funding round stage directly, so batch
# recency is the best available proxy for "early-stage / recently funded").
RECENT_BATCH_YEARS = 3

_robots = urllib.robotparser.RobotFileParser()
_robots.set_url(f"{BASE}/robots.txt")
_robots_loaded = False


def _robots_allows(url: str) -> bool:
    global _robots_loaded
    if not _robots_loaded:
        try:
            import requests
            from http_utils import USER_AGENT
            resp = requests.get(f"{BASE}/robots.txt", timeout=10, headers={"User-Agent": USER_AGENT})
            resp.raise_for_status()
            _robots.parse(resp.text.splitlines())
        except Exception:
            return False
        _robots_loaded = True
    return _robots.can_fetch("*", url)


def _title_matches_target(title: str) -> bool:
    t = (title or "").lower()
    return any(kw in t for kw in TARGET_TITLE_KEYWORDS)


def _batch_is_recent(batch: str) -> bool:
    """YC batch codes look like 'S15', 'W26', 'F24', 'X25'. Approximate each
    season to a month and compare against today."""
    if not batch:
        return False
    m = re.match(r"^([WSFX])(\d{2})$", batch.strip())
    if not m:
        return False
    season, yy = m.groups()
    year = 2000 + int(yy)
    month = {"W": 1, "S": 6, "F": 10, "X": 6}.get(season, 6)
    approx = date(year, month, 1)
    return (date.today() - approx).days < RECENT_BATCH_YEARS * 365


_SALARY_RE = re.compile(r"([$€£₹])\s*([\d.]+)\s*([KM])", re.I)


def _parse_salary_range(text: str):
    """Parse strings like '$180K - $225K' into (min, max) USD ints. Returns
    (None, None) if unparseable or not USD (we don't want to compare a
    non-USD range against a USD floor)."""
    if not text:
        return None, None
    matches = _SALARY_RE.findall(text)
    if not matches or matches[0][0] != "$":
        return None, None

    def to_num(amount, unit):
        n = float(amount)
        return n * 1_000_000 if unit.upper() == "M" else n * 1_000

    values = [to_num(amount, unit) for _, amount, unit in matches]
    if len(values) == 1:
        return int(values[0]), int(values[0])
    return int(min(values)), int(max(values))


def fetch_listing_cards(session, log=print):
    cards = {}
    for slug in CATEGORY_SLUGS:
        url = urljoin(BASE, f"/jobs/l/{slug}")
        if not _robots_allows(url):
            log(f"  [skip: robots.txt disallows] {url}")
            continue
        resp = polite_get(session, url, log=log)
        if resp is None:
            continue
        if resp.status_code != 200:
            log(f"  [warn: HTTP {resp.status_code}] {url}")
            continue

        page = extract_inertia_page(resp.text)
        if not page:
            log(f"  [warn: couldn't parse page payload] {url}")
            continue
        jobs = (page.get("props") or {}).get("jobs") or []
        for j in jobs:
            job_id = str(j.get("id"))
            if not job_id or job_id in cards:
                continue
            title = j.get("title") or ""
            cards[job_id] = {
                "job_id": job_id,
                "title": title,
                "company": j.get("companyName") or "",
                "url": urljoin(BASE, f"/jobs/{job_id}"),
                "location_text": j.get("location") or "",
            }
    return list(cards.values())


def fetch_job_detail(session, card: dict, log=print) -> Job | None:
    url = card["url"]
    if not _robots_allows(url):
        log(f"  [skip: robots.txt disallows] {url}")
        return None
    resp = polite_get(session, url, log=log)
    if resp is None:
        return None
    if resp.status_code != 200:
        log(f"  [warn: HTTP {resp.status_code}] {url}")
        return None

    page = extract_inertia_page(resp.text)
    if not page:
        return Job(
            source=SOURCE, job_id=card["job_id"], title=card["title"],
            company=card["company"], url=url, location_text=card.get("location_text", ""),
        )

    props = page.get("props") or {}
    job_data = props.get("job") or {}
    company_data = props.get("company") or {}

    location_text = job_data.get("location") or card.get("location_text", "")
    remote = bool(re.search(r"\bremote\b", location_text, re.I))
    seattle = "seattle" in location_text.lower()

    salary_min, salary_max = _parse_salary_range(job_data.get("salaryRange", ""))

    description_parts = [html_to_text(job_data.get("descriptionHtml", ""))]
    min_experience = job_data.get("minExperience")
    if min_experience:
        description_parts.append(f"Minimum experience: {min_experience}")
    description = "\n\n".join(p for p in description_parts if p)

    batch = company_data.get("batch", "")
    funding_stage = f"Recent YC batch ({batch})" if _batch_is_recent(batch) else ""

    employee_count = company_data.get("teamSize")

    return Job(
        source=SOURCE,
        job_id=card["job_id"],
        title=job_data.get("title") or card["title"],
        company=company_data.get("name") or card["company"],
        url=url,
        location_text=location_text,
        remote=remote,
        seattle=seattle,
        description=description,
        salary_min=salary_min,
        salary_max=salary_max,
        employee_count=employee_count,
        funding_stage=funding_stage,
        date_posted="",  # not exposed by this source
        employment_type=job_data.get("jobType", ""),
    )


def collect_jobs(log=print) -> tuple[list[Job], int]:
    """Returns (jobs, raw_card_count). raw_card_count is the number of cards
    fetch_listing_cards() found BEFORE the title-keyword prefilter — main.py
    uses it for source-health tracking (see storage.SOURCE_HEALTH_SCHEMA):
    a healthy run should turn up dozens+ raw cards even on a day with zero
    digest matches, so a card count of zero is a parser-broke signal, not a
    quiet-day signal."""
    session = new_session()
    log("Work at a Startup: fetching listing pages...")
    cards = fetch_listing_cards(session, log=log)
    raw_card_count = len(cards)
    log(f"Work at a Startup: {raw_card_count} total listings seen across pages fetched.")

    shortlisted = [c for c in cards if _title_matches_target(c["title"])]
    log(f"Work at a Startup: {len(shortlisted)} match target-title keywords, fetching detail...")

    jobs = []
    for card in shortlisted:
        job = fetch_job_detail(session, card, log=log)
        if job:
            jobs.append(job)
    return jobs, raw_card_count
