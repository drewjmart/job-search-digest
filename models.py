"""Shared data shape every source module produces."""

from dataclasses import dataclass, field


@dataclass
class Job:
    source: str            # e.g. "builtin_seattle"
    job_id: str            # stable id from the source (used for dedup key)
    title: str
    company: str
    url: str
    location_text: str = ""
    remote: bool = False
    seattle: bool = False
    description: str = ""       # plain text, HTML stripped
    salary_min: int | None = None
    salary_max: int | None = None
    employee_count: int | None = None
    funding_stage: str = ""     # best-effort, e.g. "Series B", "Seed" (Wellfound only)
    date_posted: str = ""       # ISO date string, if known
    employment_type: str = ""   # FULL_TIME, CONTRACTOR, etc.

    # Set for "company to watch" items (e.g. GeekWire funding/hiring news)
    # that aren't an actual job posting: skip the fit-scoring pipeline
    # entirely and show these in their own digest section instead.
    signal_only: bool = False
    signal_reason: str = ""

    @property
    def job_key(self) -> str:
        return f"{self.source}:{self.job_id}"
