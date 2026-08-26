"""Shared HTTP helpers: polite session, rate limiting, HTML->text."""

import re
import time

import requests
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36 "
    "(personal job-search digest script; contact: you@example.com)"
)

# Minimum delay between requests to the same site, in seconds. Be polite.
DEFAULT_DELAY_SECONDS = 1.5


def new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9",
        # Some sites (e.g. workatastartup.com) 406 a request with no Accept header.
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    })
    return s


def polite_get(session: requests.Session, url: str, delay: float = DEFAULT_DELAY_SECONDS,
                retries: int = 2, log=None, **kwargs):
    """GET with a couple of retries on network-level failures (timeouts,
    connection resets, DNS blips). Returns None — never raises — if every
    attempt fails, so one flaky request can't take down an entire run.
    Callers must handle a None return the same way they'd handle a non-200
    status code."""
    last_error = None
    for attempt in range(1, retries + 2):  # e.g. retries=2 -> 3 attempts total
        try:
            resp = session.get(url, timeout=20, **kwargs)
            time.sleep(delay)
            return resp
        except requests.exceptions.RequestException as e:
            last_error = e
            if log:
                log(f"  [warn: network error, attempt {attempt}] {url} -> {e.__class__.__name__}: {e}")
            time.sleep(delay)
    if log:
        log(f"  [skip: gave up after {retries + 1} attempts] {url} -> {last_error}")
    return None


def extract_inertia_page(html_doc: str) -> dict | None:
    """Extract the server-rendered Inertia.js `data-page="{...}"` JSON payload
    some sites (e.g. workatastartup.com) embed instead of plain HTML."""
    import html as htmlmod
    import json

    m = re.search(r'data-page="(\{.*?\})"\s', html_doc, re.S)
    if not m:
        return None
    try:
        return json.loads(htmlmod.unescape(m.group(1)))
    except Exception:
        return None


def html_to_text(html_fragment: str) -> str:
    if not html_fragment:
        return ""
    soup = BeautifulSoup(html_fragment, "html.parser")
    text = soup.get_text("\n", strip=True)
    return re.sub(r"\n{3,}", "\n\n", text)
