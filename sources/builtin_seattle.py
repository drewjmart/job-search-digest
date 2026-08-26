"""
Built In Seattle source (https://www.builtinseattle.com).

Only fetches URLs allowed by the site's robots.txt for User-agent: * (verified
manually against https://www.builtinseattle.com/robots.txt on 2026-08-18, and
re-checked at runtime via urllib.robotparser as a safety net):
  - /jobs                (allowed)
  - /jobs?page=1|2|3     (explicitly allowed; higher pages are disallowed)
  - /jobs/remote          + same paging
  - /job/<slug>/<id>     (individual postings; not matched by any Disallow rule)

No login, no search/filter query params (those are Disallow'd), no scraping
beyond page 3 of any listing.
"""

import json
import re
import sys
import urllib.robotparser
from pathlib import Path
from urllib.parse import urljoin

sys.path.insert(0, str(Path(__file__).parent.parent))

from http_utils import html_to_text, new_session, polite_get  # noqa: E402
from models import Job  # noqa: E402
from profile import TARGET_TITLE_KEYWORDS  # noqa: E402

SOURCE = "builtin_seattle"
BASE = "https://www.builtinseattle.com"

LISTING_PATHS = ["/jobs", "/jobs/remote"]
PAGES = [1, 2, 3]

_robots = urllib.robotparser.RobotFileParser()
_robots.set_url(f"{BASE}/robots.txt")
_robots_loaded = False


def _robots_allows(url: str) -> bool:
    # Use requests (which has working cert verification via certifi) to fetch
    # robots.txt rather than RobotFileParser's own urllib opener, which can
    # fail with SSL errors on some local Python installs.
    global _robots_loaded
    if not _robots_loaded:
        try:
            import requests
            from http_utils import USER_AGENT
            resp = requests.get(f"{BASE}/robots.txt", timeout=10, headers={"User-Agent": USER_AGENT})
            resp.raise_for_status()
            _robots.parse(resp.text.splitlines())
        except Exception:
            # If robots.txt can't be fetched, fail closed (skip fetching).
            return False
        _robots_loaded = True
    return _robots.can_fetch("*", url)


def _listing_urls():
    for path in LISTING_PATHS:
        yield urljoin(BASE, path)
        for page in PAGES[1:]:
            yield urljoin(BASE, f"{path}?page={page}")


def _title_matches_target(title: str) -> bool:
    t = title.lower()
    return any(kw in t for kw in TARGET_TITLE_KEYWORDS)


def fetch_listing_cards(session, log=print):
    """Fetch listing pages and return lightweight card dicts (no full description yet)."""
    cards = {}
    for url in _listing_urls():
        if not _robots_allows(url):
            log(f"  [skip: robots.txt disallows] {url}")
            continue
        resp = polite_get(session, url, log=log)
        if resp is None:
            continue
        if resp.status_code != 200:
            log(f"  [warn: HTTP {resp.status_code}] {url}")
            continue
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        for card in soup.select('div[data-id="job-card"]'):
            a = card.select_one('a[data-id="job-card-title"]')
            if not a or not a.get("href"):
                continue
            href = a["href"]
            m = re.search(r"/job/[^/]+/(\d+)", href)
            if not m:
                continue
            job_id = m.group(1)
            title = a.get_text(strip=True)
            company_el = card.select_one('[data-id="company-title"]')
            company = company_el.get_text(strip=True) if company_el else ""
            card_text = card.get_text(" ", strip=True)
            loc_match = re.search(
                r"(Remote(?: or Hybrid)?|Hybrid|On-?site)[^.]{0,40}?(?:WA|USA|Washington)?",
                card_text,
            )
            location_text = loc_match.group(0).strip() if loc_match else ""
            cards[job_id] = {
                "job_id": job_id,
                "title": title,
                "company": company,
                "url": urljoin(BASE, href.split("?")[0]),
                "location_text": location_text,
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

    m = re.search(r'<script type="application/ld&#x2B;json">(.*?)</script>', resp.text, re.S)
    if not m:
        # Fall back to whatever we got from the listing card only.
        return Job(
            source=SOURCE,
            job_id=card["job_id"],
            title=card["title"],
            company=card["company"],
            url=url,
            location_text=card.get("location_text", ""),
        )

    try:
        data = json.loads(m.group(1))
        posting = data["@graph"][0]
    except Exception:
        posting = {}

    description_html = posting.get("description", "")
    description = html_to_text(description_html)

    salary_min = salary_max = None
    base_salary = posting.get("baseSalary") or {}
    value = base_salary.get("value") or {}
    if isinstance(value, dict):
        salary_min = value.get("minValue")
        salary_max = value.get("maxValue")

    locations = posting.get("jobLocation") or []
    locality_names = []
    if isinstance(locations, list):
        for loc in locations:
            addr = (loc or {}).get("address") or {}
            if addr.get("addressLocality"):
                locality_names.append(addr["addressLocality"])
    location_text = card.get("location_text", "") or ", ".join(locality_names)

    remote = bool(re.search(r"remote", location_text, re.I))
    seattle = "seattle" in location_text.lower() or "seattle" in ", ".join(locality_names).lower()

    return Job(
        source=SOURCE,
        job_id=card["job_id"],
        title=posting.get("title") or card["title"],
        company=(posting.get("hiringOrganization") or {}).get("name") or card["company"],
        url=url,
        location_text=location_text,
        remote=remote,
        seattle=seattle,
        description=description,
        salary_min=salary_min,
        salary_max=salary_max,
        employee_count=None,  # not reliably exposed on listing or job pages
        date_posted=posting.get("datePosted", ""),
        employment_type=posting.get("employmentType", ""),
    )


def collect_jobs(log=print) -> tuple[list[Job], int]:
    """Returns (jobs, raw_card_count). raw_card_count is the number of cards
    fetch_listing_cards() found BEFORE the title-keyword prefilter — main.py
    uses it for source-health tracking (see storage.SOURCE_HEALTH_SCHEMA):
    a healthy run should turn up dozens+ raw cards even on a day with zero
    digest matches, so a card count of zero is a parser-broke signal, not a
    quiet-day signal."""
    session = new_session()
    log("Built In Seattle: fetching listing pages...")
    cards = fetch_listing_cards(session, log=log)
    raw_card_count = len(cards)
    log(f"Built In Seattle: {raw_card_count} total listings seen across pages fetched.")

    shortlisted = [c for c in cards if _title_matches_target(c["title"])]
    log(f"Built In Seattle: {len(shortlisted)} match target-title keywords, fetching detail...")

    jobs = []
    for card in shortlisted:
        job = fetch_job_detail(session, card, log=log)
        if job:
            jobs.append(job)
    return jobs, raw_card_count
