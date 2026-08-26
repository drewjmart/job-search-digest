"""
GeekWire source (https://www.geekwire.com) — funding/hiring-news signal, not
a job board. The idea (per the original brief): catch companies that just
raised money or are visibly growing their team *before* they even post a
role, so you can reach out proactively.

This produces `Job` records with `signal_only=True` — main.py routes these
around the normal fit-scoring pipeline (there's no job description or salary
to score against your profile) and digest.py renders them in their own
"Companies to Watch" section instead of the ranked match list.

Only the site's root RSS feed (`/feed/`) is used — verified against
https://www.geekwire.com/robots.txt (not disallowed) on 2026-08-18. Per-tag
feeds (`/tag/funding/feed/`, `/startups/feed/`, etc.) all 403, seemingly
blocked at the edge regardless of robots.txt, so we pull the general feed
(most recent ~35 articles) and filter client-side using GeekWire's own
per-article category tags plus a title-keyword fallback. This means only
roughly the last day or two of articles are visible per run — acceptable
for a daily digest since dedup carries state forward, but a genuinely quiet
news day can mean nothing new here, and a big blitz of news could scroll an
item past the window before the next run. Not a substitute for the job
boards, just an early-warning supplement.
"""

import html as htmlmod
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin

sys.path.insert(0, str(Path(__file__).parent.parent))

from http_utils import html_to_text, new_session, polite_get  # noqa: E402
from models import Job  # noqa: E402
from profile import EXCLUDE_COMPANY_NAMES  # noqa: E402

SOURCE = "geekwire"
BASE = "https://www.geekwire.com"
FEED_URL = urljoin(BASE, "/feed/")

FUNDING_CATEGORY_TAGS = {"funding"}
HIRING_CATEGORY_TAGS = {"tech moves", "people moves"}

FUNDING_TITLE_PATTERNS = [
    r"raises? \$", r"raised \$", r"closes \$[\d.]+\s*[mb]\b", r"secures \$",
    r"funding round", r"series [a-e]\b", r"seed round", r"lands \$",
]
HIRING_TITLE_PATTERNS = [
    r"\btech moves\b", r"grows team to", r"names .* as", r"appoints .* as",
    r"joins .* as (ceo|cfo|coo|cto|president|chief)", r"hires .* as",
]

_ATOM_CONTENT_NS = "{http://purl.org/rss/1.0/modules/content/}encoded"


def _is_excluded(title: str, categories: list[str]) -> bool:
    haystack = f"{title} {' '.join(categories)}"
    return any(re.search(rf"\b{re.escape(name)}\b", haystack, re.I) for name in EXCLUDE_COMPANY_NAMES)


def _classify(title: str, categories: list[str]) -> str:
    """Returns a human-readable reason string if this article looks like a
    funding or hiring signal, else ''."""
    cats_lower = {c.lower() for c in categories}
    reasons = []

    if cats_lower & FUNDING_CATEGORY_TAGS or any(re.search(p, title, re.I) for p in FUNDING_TITLE_PATTERNS):
        reasons.append("funding announcement")
    if cats_lower & HIRING_CATEGORY_TAGS or any(re.search(p, title, re.I) for p in HIRING_TITLE_PATTERNS):
        reasons.append("hiring/personnel news (may signal open roles)")

    return "; ".join(reasons)


def _parse_pubdate(text: str) -> str:
    if not text:
        return ""
    try:
        dt = parsedate_to_datetime(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.date().isoformat()
    except Exception:
        return ""


def collect_jobs(log=print) -> list[Job]:
    session = new_session()
    log("GeekWire: fetching RSS feed...")
    resp = polite_get(session, FEED_URL, log=log)
    if resp is None:
        return []
    if resp.status_code != 200:
        log(f"  [warn: HTTP {resp.status_code}] {FEED_URL}")
        return []

    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError as e:
        log(f"  [warn: couldn't parse feed XML] {e}")
        return []

    items = root.findall("./channel/item")
    log(f"GeekWire: {len(items)} articles in feed.")

    signals = []
    for item in items:
        title_el = item.find("title")
        link_el = item.find("link")
        guid_el = item.find("guid")
        if title_el is None or link_el is None:
            continue
        title = htmlmod.unescape(title_el.text or "")
        link = link_el.text or ""
        categories = [htmlmod.unescape(c.text) for c in item.findall("category") if c.text]

        if _is_excluded(title, categories):
            continue

        reason = _classify(title, categories)
        if not reason:
            continue

        content_el = item.find(_ATOM_CONTENT_NS)
        desc_el = item.find("description")
        raw_desc = (content_el.text if content_el is not None else None) or \
                   (desc_el.text if desc_el is not None else "") or ""
        description = html_to_text(raw_desc)

        pubdate_el = item.find("pubDate")
        date_posted = _parse_pubdate(pubdate_el.text if pubdate_el is not None else "")

        job_id = (guid_el.text if guid_el is not None and guid_el.text else link).strip()
        job_id = re.sub(r"\D", "", job_id) or str(abs(hash(link)))

        signals.append(Job(
            source=SOURCE,
            job_id=job_id,
            title=title,
            company="",
            url=link,
            description=description,
            date_posted=date_posted,
            signal_only=True,
            signal_reason=reason,
        ))

    log(f"GeekWire: {len(signals)} funding/hiring signal(s) found.")
    return signals
