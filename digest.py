"""Formats scored job matches and signal-only items into the digest text."""

from datetime import date


def _location_label(job) -> str:
    if job.remote:
        return "Remote"
    if job.location_text:
        return job.location_text
    if job.seattle:
        return "Seattle"
    return "Location not specified"


def format_digest(entries: list[tuple], signal_entries: list | None = None,
                   today: date | None = None, health_warnings: list | None = None) -> str:
    """entries: list of (job, score, reason, change_note) sorted desc by score.
    signal_entries: list of Job (signal_only=True), e.g. GeekWire funding/hiring news.
    health_warnings: list of (source, last_nonzero_date) for sources whose
    parser looks broken (2+ consecutive zero-raw-card runs — see
    main.check_source_health). Rendered as a banner at the top so it's seen
    in the inbox, but it never blocks the rest of the digest below it."""
    today = today or date.today()
    signal_entries = signal_entries or []
    health_warnings = health_warnings or []
    lines = []

    if health_warnings:
        lines.append("⚠ SOURCE HEALTH WARNING ⚠")
        lines.append(
            "The following source(s) returned zero listings for 2+ consecutive "
            "runs, which usually means the site changed and the parser needs "
            "attention:"
        )
        for source, last_nonzero in health_warnings:
            when = last_nonzero or "never"
            lines.append(f"  * {source} (last successful fetch: {when})")
        lines.append("")

    lines.append(f"=== Job Digest: {today.isoformat()} ===")
    lines.append("")

    if not entries:
        lines.append("No new matching postings today.")
    else:
        for i, (job, score, reason, change_note) in enumerate(entries, start=1):
            tag = f" ({change_note})" if change_note else ""
            lines.append(f"{i}. [{score}%] {job.title}, {job.company} ({_location_label(job)}){tag}")
            lines.append(f"   Why: {reason}")
            if job.salary_min or job.salary_max:
                lo = f"${job.salary_min:,.0f}" if job.salary_min else "?"
                hi = f"${job.salary_max:,.0f}" if job.salary_max else "?"
                lines.append(f"   Salary: {lo} - {hi}")
            lines.append(f"   Link: {job.url}")
            lines.append("")

    if signal_entries:
        lines.append("=== Companies to Watch (funding/hiring news, not job postings) ===")
        lines.append("")
        for job in signal_entries:
            lines.append(f"- {job.title}")
            lines.append(f"   Signal: {job.signal_reason}")
            lines.append(f"   Link: {job.url}")
            lines.append("")

    return "\n".join(lines)
