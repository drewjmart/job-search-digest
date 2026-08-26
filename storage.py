"""SQLite-backed dedup store: remembers every job we've already surfaced."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "seen_jobs.sqlite3"

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_jobs (
    job_key       TEXT PRIMARY KEY,   -- source-prefixed stable id, e.g. "builtin:10732096"
    source        TEXT NOT NULL,
    title         TEXT,
    company       TEXT,
    url           TEXT,
    fingerprint   TEXT,               -- hash of fields that matter for "material change" detection
    score         INTEGER,
    excluded      INTEGER NOT NULL DEFAULT 0,
    exclude_reason TEXT,
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL,
    times_surfaced INTEGER NOT NULL DEFAULT 1
);
"""

# Tracks the same real-world posting across different listing IDs (e.g. a
# source reposts the same role under a new job_id, or it's cross-listed on
# two boards). seen_jobs alone can't catch that — its key is source:job_id,
# so a repost under a new ID looks like a brand-new posting. This table is
# keyed by normalized title+company instead, so a repost within the cooldown
# window can be suppressed even though its job_id is new; once the cooldown
# passes it's fair game to show again.
POSTING_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_postings (
    posting_key TEXT PRIMARY KEY,   -- normalized "title|company"
    last_shown  TEXT NOT NULL       -- ISO date this posting was last shown in the digest
);
"""

# Tracks whether a source's fetch_listing_cards() is still finding raw
# listings at all, BEFORE the title-keyword prefilter and BEFORE scoring.
# A healthy source should turn up dozens-to-hundreds of raw cards on
# basically any run, even a day with zero digest matches — so a run with
# zero raw cards isn't "a quiet day," it's a strong signal the site changed
# its markup/API shape and the parser needs attention. Tracking consecutive
# zero-card runs (rather than acting on a single one) absorbs one-off
# network blips without crying wolf.
SOURCE_HEALTH_SCHEMA = """
CREATE TABLE IF NOT EXISTS source_health (
    source                TEXT PRIMARY KEY,
    consecutive_zero_runs INTEGER NOT NULL DEFAULT 0,
    last_nonzero_date     TEXT,
    last_run_date         TEXT NOT NULL
);
"""


@contextmanager
def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(SCHEMA)
        conn.execute(POSTING_SCHEMA)
        conn.execute(SOURCE_HEALTH_SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def get_existing(conn, job_key: str):
    row = conn.execute(
        "SELECT job_key, fingerprint, score, excluded FROM seen_jobs WHERE job_key = ?",
        (job_key,),
    ).fetchone()
    if row is None:
        return None
    return {"job_key": row[0], "fingerprint": row[1], "score": row[2], "excluded": bool(row[3])}


def get_last_shown(conn, posting_key: str):
    """ISO date string the given title+company was last shown in the digest,
    or None if it's never been shown (or is old enough there's no record)."""
    row = conn.execute(
        "SELECT last_shown FROM seen_postings WHERE posting_key = ?",
        (posting_key,),
    ).fetchone()
    return row[0] if row else None


def record_shown(conn, posting_key: str, today: str):
    conn.execute(
        """INSERT INTO seen_postings (posting_key, last_shown) VALUES (?, ?)
           ON CONFLICT(posting_key) DO UPDATE SET last_shown = excluded.last_shown""",
        (posting_key, today),
    )


def get_source_health(conn, source: str):
    row = conn.execute(
        "SELECT consecutive_zero_runs, last_nonzero_date, last_run_date "
        "FROM source_health WHERE source = ?",
        (source,),
    ).fetchone()
    if row is None:
        return None
    return {
        "consecutive_zero_runs": row[0],
        "last_nonzero_date": row[1],
        "last_run_date": row[2],
    }


def record_source_run(conn, source: str, card_count: int, today: str):
    """Rolls a source's zero-card streak forward. card_count == 0 extends the
    streak (parser may be broken); any nonzero count resets it to 0 and marks
    today as the last known-good fetch — that reset matters even for a source
    that's had zero DIGEST matches for weeks, since raw cards and digest
    matches are answering different questions (is the parser working vs. is
    anything a good fit)."""
    existing = get_source_health(conn, source)
    prior_consecutive = existing["consecutive_zero_runs"] if existing else 0
    prior_last_nonzero = existing["last_nonzero_date"] if existing else None

    if card_count > 0:
        consecutive = 0
        last_nonzero = today
    else:
        consecutive = prior_consecutive + 1
        last_nonzero = prior_last_nonzero

    conn.execute(
        """INSERT INTO source_health (source, consecutive_zero_runs, last_nonzero_date, last_run_date)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(source) DO UPDATE SET
               consecutive_zero_runs = excluded.consecutive_zero_runs,
               last_nonzero_date = excluded.last_nonzero_date,
               last_run_date = excluded.last_run_date""",
        (source, consecutive, last_nonzero, today),
    )


def upsert(conn, job_key, source, title, company, url, fingerprint, score,
           excluded, exclude_reason, today: str):
    existing = get_existing(conn, job_key)
    if existing is None:
        conn.execute(
            """INSERT INTO seen_jobs
               (job_key, source, title, company, url, fingerprint, score,
                excluded, exclude_reason, first_seen, last_seen, times_surfaced)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
            (job_key, source, title, company, url, fingerprint, score,
             int(excluded), exclude_reason, today, today),
        )
    else:
        conn.execute(
            """UPDATE seen_jobs
               SET fingerprint = ?, score = ?, excluded = ?, exclude_reason = ?,
                   last_seen = ?, times_surfaced = times_surfaced + 1
               WHERE job_key = ?""",
            (fingerprint, score, int(excluded), exclude_reason, today, job_key),
        )
