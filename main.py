#!/usr/bin/env python3
"""
Daily small-company job search digest.

Usage:
    python main.py                 # run all sources, save to DB, email digest
    python main.py --dry-run       # score everything but don't touch DB or email
    python main.py --no-email      # run + save to DB, but skip sending email
    python main.py --sources builtin_seattle
"""

import argparse
import hashlib
import sys
from datetime import date
from pathlib import Path

import digest as digest_mod
import emailer
import profile as p
import scoring
import storage
from sources import builtin_seattle, geekwire, wellfound, workatastartup

# Windows can default stdout/stderr to a legacy codepage (e.g. cp1252) even
# when redirected to a file — exactly what the Task Scheduler wrapper
# (scripts/run_digest.ps1) does — which would crash on non-ASCII output like
# the "⚠" source-health marker below. Force UTF-8 so an interactive run, a
# scheduled run, and log redirection all behave the same regardless of the
# host's default codepage.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

SOURCE_MODULES = {
    "builtin_seattle": builtin_seattle,
    "wellfound": wellfound,
    "workatastartup": workatastartup,
    "geekwire": geekwire,
}

ARCHIVE_DIR = Path(__file__).parent / "data" / "digests"


REPOST_COOLDOWN_DAYS = 30

# Sources with a fetch_listing_cards() step whose raw (pre-title-filter)
# count is meaningful for health tracking. GeekWire is a news feed, not a
# job-listings source, so it's excluded here.
LISTING_SOURCE_NAMES = {"builtin_seattle", "wellfound", "workatastartup"}


def fingerprint_of(job) -> str:
    key = f"{job.title}|{job.salary_min}|{job.salary_max}|{job.employment_type}|{job.location_text}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def posting_key_of(job) -> str:
    """Identifies the same real-world posting across different listing IDs
    (a source reposting the same role, or cross-listing on two boards) —
    unlike job_key (source:job_id), which treats each listing ID as distinct."""
    return f"{job.title.strip().lower()}|{job.company.strip().lower()}"


def dedupe_same_posting(entries):
    """Collapse entries that are the same real-world posting under different
    listing IDs within THIS run (e.g. two Wellfound IDs for one repost).
    Keeps the higher-scoring copy."""
    best = {}
    for entry in entries:
        job, score = entry[0], entry[1]
        key = posting_key_of(job)
        if key not in best or score > best[key][1]:
            best[key] = entry
    return list(best.values())


def apply_repost_cooldown(conn, entries, today: date, dry_run: bool):
    """Suppress a posting that was already shown (under any listing ID)
    within the last REPOST_COOLDOWN_DAYS days, even if today's copy has a
    new job_id (e.g. a company reposting to bump visibility). Once the
    cooldown passes, it's shown again as if new."""
    kept = []
    for entry in entries:
        job = entry[0]
        key = posting_key_of(job)
        last_shown = storage.get_last_shown(conn, key)
        if last_shown and (today - date.fromisoformat(last_shown)).days < REPOST_COOLDOWN_DAYS:
            continue
        kept.append(entry)
        if not dry_run:
            storage.record_shown(conn, key, today.isoformat())
    return kept


def check_source_health(conn, source_card_counts: dict, today: date, dry_run: bool):
    """Flags sources whose fetch_listing_cards() has returned zero raw cards
    for 2+ runs running now — a strong signal the site's markup/API shape
    changed and the parser needs attention, distinct from a source simply
    having zero DIGEST matches today (normal, and not a health problem: a
    healthy source still turns up dozens of raw cards, just none that score
    well). This is a softer, source-specific degradation signal, separate
    from main()'s top-level crash safety net, which only covers total
    pipeline failure.

    Always computes the prospective streak so --dry-run still surfaces the
    warning; only persists it (storage.record_source_run) when not a dry
    run, matching how the rest of run() already treats --dry-run."""
    warnings = []
    for source, card_count in source_card_counts.items():
        existing = storage.get_source_health(conn, source)
        prior_consecutive = existing["consecutive_zero_runs"] if existing else 0
        prospective_consecutive = 0 if card_count > 0 else prior_consecutive + 1

        if prospective_consecutive >= 2:
            last_nonzero = existing["last_nonzero_date"] if existing else None
            warnings.append((source, last_nonzero))

        if not dry_run:
            storage.record_source_run(conn, source, card_count, today.isoformat())

    return warnings


def run(source_names, dry_run: bool, send_email: bool, log=print):
    today = date.today()

    all_jobs = []
    source_card_counts = {}  # source name -> raw pre-filter card count, for health tracking
    for name in source_names:
        mod = SOURCE_MODULES[name]
        if name in LISTING_SOURCE_NAMES:
            jobs, raw_card_count = mod.collect_jobs(log=log)
            source_card_counts[name] = raw_card_count
        else:
            jobs = mod.collect_jobs(log=log)
        all_jobs.extend(jobs)

    scoreable_jobs = [j for j in all_jobs if not j.signal_only]
    signal_jobs = [j for j in all_jobs if j.signal_only]

    log(f"\nScoring {len(scoreable_jobs)} shortlisted job(s)...")

    entries = []
    signal_entries = []
    with storage.connect() as conn:
        for job in scoreable_jobs:
            score, reason, excluded, exclude_reason = scoring.score_job(job)
            fp = fingerprint_of(job)
            existing = storage.get_existing(conn, job.job_key)

            include = False
            change_note = None
            if not excluded and score >= p.MIN_SCORE_TO_SHOW:
                if existing is None:
                    include = True
                elif existing["excluded"]:
                    include = True
                    change_note = "now passes screening"
                elif existing["fingerprint"] != fp:
                    include = True
                    change_note = "details changed"
                else:
                    include = False

            if not dry_run:
                storage.upsert(
                    conn, job.job_key, job.source, job.title, job.company, job.url,
                    fp, score, excluded, exclude_reason, today.isoformat(),
                )

            if include:
                entries.append((job, score, reason, change_note))

        # Signal-only items (e.g. GeekWire funding/hiring news) skip fit
        # scoring entirely: already filtered by their source module, just
        # deduped so the same article isn't shown twice.
        for job in signal_jobs:
            fp = fingerprint_of(job)
            existing = storage.get_existing(conn, job.job_key)
            include = existing is None

            if not dry_run:
                storage.upsert(
                    conn, job.job_key, job.source, job.title, job.company, job.url,
                    fp, 0, False, None, today.isoformat(),
                )

            if include:
                signal_entries.append(job)

        entries = dedupe_same_posting(entries)
        entries = apply_repost_cooldown(conn, entries, today, dry_run)
        health_warnings = check_source_health(conn, source_card_counts, today, dry_run)

    entries.sort(key=lambda e: e[1], reverse=True)

    text = digest_mod.format_digest(entries, signal_entries, today=today, health_warnings=health_warnings)
    log("\n" + text)

    if not dry_run:
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        archive_path = ARCHIVE_DIR / f"{today.isoformat()}.md"
        archive_path.write_text(text, encoding="utf-8")
        log(f"\n(Archived a copy to {archive_path})")

    if send_email and not dry_run:
        subject = f"Job Digest: {today.isoformat()} ({len(entries)} new, {len(signal_entries)} watch)"
        emailer.send_digest_email(subject, text)
        log("Email sent.")
    elif send_email and dry_run:
        log("(--dry-run: skipping email send)")

    return entries, signal_entries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sources", nargs="+", default=list(SOURCE_MODULES.keys()),
        choices=list(SOURCE_MODULES.keys()), help="Which sources to run.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Don't write to DB or send email.")
    parser.add_argument("--no-email", action="store_true", help="Don't send email (still saves to DB).")
    args = parser.parse_args()

    try:
        run(args.sources, dry_run=args.dry_run, send_email=not args.no_email)
    except Exception as e:
        # Individual request failures are already handled per-source (see
        # http_utils.polite_get) so a single flaky connection can't land
        # here. This is the last-resort net for anything else that still
        # goes wrong — without it, a crash here means total silence: no
        # digest, no email, no error, nothing to notice. Surface it instead.
        import traceback
        tb = traceback.format_exc()
        print(tb)
        if not args.dry_run and not args.no_email:
            try:
                emailer.send_digest_email(
                    f"Job Digest FAILED: {date.today().isoformat()}",
                    f"The job digest run crashed and produced no results today.\n\n{tb}",
                )
            except Exception as email_error:
                print(f"(also failed to send failure-alert email: {email_error})")
        raise


if __name__ == "__main__":
    main()
