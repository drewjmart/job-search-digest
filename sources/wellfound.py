"""
Wellfound source (https://wellfound.com).

Only fetches URLs allowed by robots.txt for User-agent: * (verified manually
against https://wellfound.com/robots.txt on 2026-08-18, and re-checked at
runtime via urllib.robotparser as a safety net):
  - /role/l/<role-slug>/<location-slug>   (role+location taxonomy pages)
  - /location/<location-slug>             (generic city-wide listing)
  - /jobs/<id>-<slug>                     (individual postings)

No login, no /search (explicitly Disallow'd), no query-param filters.

Wellfound's "role" filter only narrows results for slugs in its own fixed
taxonomy; an unrecognized slug silently falls back to the generic city page
instead of 404ing. So we use a small curated list of role slugs confirmed to
actually narrow results, plus one generic city-wide page as a catch-all for
titles (VP of Operations, PMO Lead, etc.) that don't map to a known slug.
Our own title-keyword prefilter (profile.TARGET_TITLE_KEYWORDS) does the
real filtering either way.

Pagination via ?page=N was tested and does not return meaningfully different
server-rendered content on this site (the first load already includes the
bulk of results), so we only fetch page 1 of each listing URL.
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

SOURCE = "wellfound"
BASE = "https://wellfound.com"

# Confirmed against the live taxonomy to actually narrow results (not just
# fall back to the generic city page).
CURATED_ROLE_SLUGS = [
    "chief-of-staff",
    "technical-program-manager",
    "program-manager",
    "operations-manager",
    "director-of-operations",
]
LOCATION_SLUGS = ["seattle", "remote"]
GENERIC_FALLBACK_PATH = "/location/seattle"

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


def _listing_urls():
    for slug in CURATED_ROLE_SLUGS:
        for loc in LOCATION_SLUGS:
            yield urljoin(BASE, f"/role/l/{slug}/{loc}")
    yield urljoin(BASE, GENERIC_FALLBACK_PATH)


def _title_matches_target(title: str) -> bool:
    t = title.lower()
    return any(kw in t for kw in TARGET_TITLE_KEYWORDS)


def _find_scopes(a):
    """Walk up from a job-title <a> to find (job_row, company_group) containers.

    job_row: smallest ancestor containing exactly this one job link.
    company_group: smallest ancestor containing exactly one unique company link
    (i.e. as far as we can climb before merging into a wider, multi-company
    container).
    """
    node = a
    job_row = a
    while node is not None:
        if len(node.select('a[href^="/jobs/"]')) == 1:
            job_row = node
            node = node.parent
        else:
            break

    node = job_row
    company_group = job_row
    while node is not None:
        hrefs = {x.get("href") for x in node.select('a[href^="/company/"]')}
        if len(hrefs) <= 1:
            company_group = node
            node = node.parent
        else:
            break
    return job_row, company_group


def fetch_listing_cards(session, log=print):
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
        for a in soup.select('a[href^="/jobs/"]'):
            href = a.get("href", "")
            m = re.match(r"^/jobs/(\d+)-", href)
            if not m or not a.get_text(strip=True):
                continue
            job_id = m.group(1)
            if job_id in cards:
                continue
            title = a.get_text(strip=True)

            job_row, company_group = _find_scopes(a)

            company_links = [
                c for c in company_group.select('a[href^="/company/"]')
                if c.get_text(strip=True)
            ]
            company = company_links[0].get_text(strip=True) if company_links else ""

            grp_text = company_group.get_text(" ", strip=True)
            emp_match = re.search(r"([\d,]+)\+?\s*Employees", grp_text)
            employee_count = None
            if emp_match:
                try:
                    employee_count = int(emp_match.group(1).replace(",", ""))
                except ValueError:
                    pass
            stage_match = re.search(
                r"\b(Seed|Series [A-J]|Public|Bootstrapped|Growth Stage|Early Stage|Scale Stage)\b",
                grp_text,
            )
            funding_stage = stage_match.group(0) if stage_match else ""

            cards[job_id] = {
                "job_id": job_id,
                "title": title,
                "company": company,
                "url": urljoin(BASE, href),
                "employee_count": employee_count,
                "funding_stage": funding_stage,
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

    m = re.search(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', resp.text, re.S)
    if not m:
        return Job(
            source=SOURCE,
            job_id=card["job_id"],
            title=card["title"],
            company=card["company"],
            url=url,
            employee_count=card.get("employee_count"),
            funding_stage=card.get("funding_stage", ""),
        )

    try:
        posting = json.loads(m.group(1))
    except Exception:
        posting = {}

    description = html_to_text(posting.get("description", ""))

    salary_min = salary_max = None
    base_salary = posting.get("baseSalary") or {}
    value = base_salary.get("value") or {}
    if isinstance(value, dict):
        salary_min = value.get("minValue")
        salary_max = value.get("maxValue")

    org = posting.get("hiringOrganization") or {}
    locations = org.get("location") or []
    locality_names = []
    if isinstance(locations, list):
        for loc in locations:
            addr = (loc or {}).get("address") or {}
            if addr.get("addressLocality"):
                locality_names.append(addr["addressLocality"])
    location_text = ", ".join(locality_names)

    remote = bool(re.search(r"\bremote\b", f"{card['title']} {description[:500]}", re.I))
    seattle = "seattle" in location_text.lower()

    date_posted = posting.get("datePosted", "")
    if date_posted:
        date_posted = date_posted.split("T")[0]

    return Job(
        source=SOURCE,
        job_id=card["job_id"],
        title=posting.get("title") or card["title"],
        company=org.get("name") or card["company"],
        url=url,
        location_text=location_text,
        remote=remote,
        seattle=seattle,
        description=description,
        salary_min=salary_min,
        salary_max=salary_max,
        employee_count=card.get("employee_count"),
        funding_stage=card.get("funding_stage", ""),
        date_posted=date_posted,
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
    log("Wellfound: fetching listing pages...")
    cards = fetch_listing_cards(session, log=log)
    raw_card_count = len(cards)
    log(f"Wellfound: {raw_card_count} total listings seen across pages fetched.")

    shortlisted = [c for c in cards if _title_matches_target(c["title"])]
    log(f"Wellfound: {len(shortlisted)} match target-title keywords, fetching detail...")

    jobs = []
    for card in shortlisted:
        job = fetch_job_detail(session, card, log=log)
        if job:
            jobs.append(job)
    return jobs, raw_card_count
