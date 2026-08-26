# job-search-digest

A Python automation that runs unattended once a day, pulls new postings
from four sources with entirely different data-access mechanisms, screens
each one against a weighted fit model built from my own criteria, and
emails a ranked digest. No manual searching, no duplicate postings, no
logging into anything.

**Stack:** Python, SQLite, requests, BeautifulSoup4, SMTP, Windows Task
Scheduler. Built hands-on with Claude Code, from requirements through
implementation and testing.

**What this is, and isn't.** This surfaces and ranks postings that match
my own criteria; it doesn't apply to anything. Every posting still goes
through me before I ever click Apply, and the tool's job ends at a digest
email, not a submission.

## Four sources, four different data-access problems

Rather than scrape rendered HTML text everywhere, each source module
reverse-engineers that site's actual data-delivery mechanism:

- **Built In Seattle** — server-rendered, but each posting embeds standard
  `JobPosting` JSON-LD (schema.org). Parsed directly instead of scraping
  visible text; a title-keyword prefilter runs before fetching full
  postings to keep request volume down.
- **Wellfound** — a React SPA with build-hashed CSS classes, nothing stable
  to target by name. Built a structural walk that climbs from each job
  link to its enclosing "job row" / "company group" by counting sibling
  links rather than matching class names, so it survives redeploys. Also
  found, by testing rather than assuming, that Wellfound's own
  role-taxonomy filter silently falls back to an unfiltered city page for
  slugs it doesn't recognize — the source list was adjusted once that was
  caught.
- **Work at a Startup (YC)** — the obvious lead, a client-side Algolia
  search key visible in page source, turned out to be a red herring.
  Traced actual network activity in a live browser session and found
  listings are already server-rendered as an HTML-entity-encoded JSON blob
  (Inertia.js). Parsing that directly surfaces richer data than the other
  boards, including team size and YC batch (used as a funding-recency
  signal).
- **GeekWire** — not a job board, a news signal. A separate lightweight
  pipeline classifies RSS articles as funding or hiring news using the
  site's own category tags plus a regex fallback, routing matches into a
  "companies to watch" section instead of the ranked list.

## Why these four sources, not LinkedIn or Indeed

The four sources were chosen deliberately to surface smaller companies and
startup roles — the kind of postings that get buried on the major boards —
not primarily because LinkedIn and Indeed are harder to access. Built In
Seattle, Wellfound, and Work at a Startup all skew toward exactly that
segment, and GeekWire's funding/hiring news adds an early-signal layer the
job boards don't have at all.

LinkedIn and Indeed were evaluated and declined for a second reason on top
of that: LinkedIn's `robots.txt` explicitly disallows automated access, and
Indeed throws an active CAPTCHA wall even on pages `robots.txt` permits.
Both were confirmed directly rather than assumed, so even if they'd been in
scope for the source mix, they weren't viable to build against compliantly.

## Scoring model

Weighted 0–100: skills overlap (55), degree flexibility (15), company stage
(15), location (10), comp if listed (5). Hard excludes sit underneath —
red-flag companies, formal consulting firms, clearance requirements, hard
degree gates with no equivalent-experience language — plus a couple added
after watching it run against real data:

- The job boards' own "Seattle" / "remote" filters are looser than
  advertised; added a hard location check rather than trusting the source
  filter.
- Some postings were years old and still live; added an age cutoff.

**Bug found and fixed:** early exclusion logic did substring matching and
flagged one legitimate company as a hit against an unrelated red-flag name
sharing a short substring. Caught in testing, switched to word-boundary
matching.

## Dedup: same posting, different listing ID

`seen_jobs` catches exact reappearances by source + listing ID, but that
misses a company reposting the same role under a new ID, or the same role
cross-listed on two boards. A second table, `seen_postings`, keys on
normalized title + company instead, so a repost is suppressed for a 30-day
cooldown even when its listing ID is new — and it resurfaces automatically
once the cooldown passes, or immediately if something material about the
posting changes (e.g. a salary range gets added later).

## Source health monitoring — closes a real gap

A source's parser can break silently: a site redesign changes the CSS
selectors, an API response shape shifts, and the pipeline just keeps
running, quietly returning zero results from that source forever. Without
something watching for that, the only way to notice is realizing the
digest has felt thin for a while — not something you catch quickly.

Each listing source (Built In Seattle, Wellfound, Work at a Startup — not
GeekWire, which is a news feed, not a listings source) now reports its raw
card count from `fetch_listing_cards()`, before the title-keyword prefilter
and before scoring. That distinction matters: "zero digest matches today"
is normal and happens on quiet days, but "zero raw cards fetched" is a much
stronger signal that a parser broke, since a healthy source should surface
dozens-to-hundreds of raw cards even on a day nothing scores well.

A new `source_health` table in SQLite tracks a consecutive-zero-runs streak
per source. If a source hits 2 or more zero-card runs in a row, a warning
banner is prepended to the digest email itself — non-blocking, the rest of
the digest still generates and sends normally, it's just visible at the
top of the same email rather than requiring you to notice fewer results
over time.

## Small but real: Windows encoding fix

Windows can default `stdout`/`stderr` to a legacy codepage (e.g. cp1252)
even when output is redirected to a file — exactly what the Task Scheduler
PowerShell wrapper does. That would crash on the non-ASCII "⚠" warning
marker used in the health-check banner. `main.py` now forces UTF-8 on both
streams at startup, so an interactive run, a scheduled run, and log
redirection all behave the same regardless of the host's default codepage.
Caught before it ever broke a real scheduled run, not after.

## Compliance-first by design

- `robots.txt` is checked twice: once manually during development (dated in
  each source module's docstring) and again live at runtime via
  `urllib.robotparser`, failing closed if it can't even be fetched.
- Requests are rate-limited and identify with a real, descriptive
  User-Agent string.

## Known limitations

- The GeekWire RSS approach only surfaces roughly the last day or two of
  articles per run (per-tag feeds are blocked at the edge regardless of
  `robots.txt`, so it falls back to the general feed and filters
  client-side). A quiet news day can mean nothing new; a heavy news day
  could scroll an item past the window before the next run.
- Wellfound's structural DOM walk (sibling-link counting instead of class
  names) is a bet that it survives redesigns better than class-name
  matching would. A major layout overhaul is still untested against it
  directly, but the source-health check above means a total breakage would
  now surface as a warning within 2 runs rather than going unnoticed.
- Company size isn't reliably exposed on Built In Seattle listing or detail
  pages, so that source's postings score on an "unknown, neutral" default
  for the company-size component.

## Setup

1. `pip install -r requirements.txt`
2. Copy `.env.example` to `.env` and fill in your Gmail address + an
   [App Password](https://myaccount.google.com/apppasswords) (not your
   real password), plus `DIGEST_TO_ADDRESS` if different from the sending
   account.
3. Edit `profile.py` — target titles, comp range, excluded companies,
   location requirement — to match your own criteria. Nothing about your
   background lives in the scraper or scoring code itself; it's all read
   from this one file.
4. Run manually: `python main.py --dry-run` to see scoring output without
   touching the database or sending mail.
5. Schedule it (Windows Task Scheduler via `scripts/run_digest.ps1`, or
   cron/launchd on macOS/Linux) for a daily unattended run.
