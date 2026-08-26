"""
Fit scoring for a Job against profile.py.

Returns (score 0-100, reason string, excluded bool, exclude_reason str|None).
Excluded jobs are dropped from the digest entirely rather than shown with a
low score, per the screening rules.

Weighting (roughly matches how a human would triage):
  - real skills/requirements overlap   : up to 55 pts
  - degree flexibility                 : up to 15 pts
  - company size / stage fit           : up to 15 pts
  - location fit                       : up to 10 pts
  - comp fit (if listed)               : up to 5 pts  (absence is neutral, not penalized)
"""

import re
from datetime import date, datetime

import profile as p


def _contains_any(text: str, needles: list[str]) -> list[str]:
    text_l = text.lower()
    return [n for n in needles if n.lower() in text_l]


def _word_match_any(text: str, needles: list[str]) -> list[str]:
    """Whole-word/phrase match, to avoid short tokens (e.g. 'sei') matching
    as a substring of an unrelated word (e.g. 'Seismic')."""
    return [n for n in needles if re.search(rf"\b{re.escape(n)}\b", text, re.I)]


def check_exclusions(job) -> tuple[bool, str | None]:
    haystack_title = job.title or ""
    haystack_company = job.company or ""
    haystack_desc = job.description or ""

    if _word_match_any(haystack_company, p.EXCLUDE_COMPANY_NAMES):
        return True, f"Red-flag company: {job.company}"

    if _word_match_any(haystack_company, p.EXCLUDE_CONSULTING_FIRMS):
        return True, f"Formal consulting firm: {job.company}"

    hit = _contains_any(haystack_title, p.EXCLUDE_TITLE_KEYWORDS)
    if hit:
        return True, f"Excluded title pattern: {hit[0]}"

    hit = _contains_any(haystack_desc, p.EXCLUDE_DESCRIPTION_KEYWORDS)
    if hit:
        return True, f"Excluded requirement: {hit[0]}"

    # Hard degree requirement with no equivalent-experience language.
    full_text = f"{haystack_title} {haystack_desc}"
    has_hard_req = any(re.search(pat, full_text, re.I) for pat in p.DEGREE_REQUIRED_PATTERNS)
    has_flex = any(re.search(pat, full_text, re.I) for pat in p.DEGREE_FLEX_PATTERNS)
    if has_hard_req and not has_flex:
        return True, "Hard degree requirement, no equivalent-experience language"

    # Listed salary range doesn't reach the acceptable floor. If only one of
    # min/max is given, judge on whichever is present; unlisted salary is
    # left alone (handled as neutral in scoring, not excluded here).
    top_of_range = job.salary_max if job.salary_max is not None else job.salary_min
    if top_of_range is not None and top_of_range < p.MIN_ACCEPTABLE_SALARY:
        return True, f"Salary tops out at ${top_of_range:,.0f}, below your ${p.MIN_ACCEPTABLE_SALARY:,.0f} floor"

    # Not Seattle, not remote, no Seattle office among multi-location listings.
    if p.REQUIRE_SEATTLE_OR_REMOTE:
        loc_text = (job.location_text or "").lower()
        if not (job.remote or job.seattle or "seattle" in loc_text):
            where = job.location_text or "no location listed"
            return True, f"Not Seattle-based or remote (location: {where})"

    # Listing old enough it's probably no longer actually open.
    if job.date_posted:
        try:
            posted = datetime.fromisoformat(job.date_posted.replace("Z", "+00:00")).date()
            age_days = (date.today() - posted).days
            if age_days > p.DEAD_DAYS:
                return True, f"Posting is {age_days // 30} months old, likely no longer open"
        except ValueError:
            pass

    return False, None


def score_job(job) -> tuple[int, str, bool, str | None]:
    excluded, exclude_reason = check_exclusions(job)
    if excluded:
        return 0, "", True, exclude_reason

    reasons = []
    score = 0.0

    full_text = f"{job.title} {job.description}"

    # --- Skills overlap (0-55) ---
    matched_strengths = _contains_any(full_text, p.STRENGTH_KEYWORDS)
    matched_titles = _contains_any(job.title, p.TARGET_TITLE_KEYWORDS)
    skill_score = 0
    if matched_titles:
        skill_score += 25
        reasons.append(f"title matches target role ('{matched_titles[0]}')")
    skill_score += min(30, len(matched_strengths) * 4)
    if matched_strengths:
        top = ", ".join(matched_strengths[:4])
        reasons.append(f"overlaps on: {top}")
    score += min(55, skill_score)

    # --- Degree flexibility (0-15) ---
    has_flex = any(re.search(pat, full_text, re.I) for pat in p.DEGREE_FLEX_PATTERNS)
    has_degree_mention = re.search(r"bachelor'?s degree", full_text, re.I) is not None
    if has_flex:
        score += 15
        reasons.append("has 'equivalent experience' language")
    elif not has_degree_mention:
        score += 10  # no degree gate mentioned at all
        reasons.append("no degree requirement mentioned")
    else:
        reasons.append("degree preferred but not a hard gate")
        score += 5

    # --- Company size (0-15) ---
    if job.employee_count is not None:
        if p.SIZE_SWEET_MIN <= job.employee_count <= p.SIZE_SWEET_MAX:
            score += 15
            reasons.append(f"company size {job.employee_count} is in target range")
        elif job.employee_count < p.SIZE_SWEET_MIN:
            score += 8
            reasons.append(f"small company ({job.employee_count} employees)")
        else:
            score += 3  # large enterprise, deprioritized but not excluded
    else:
        score += 6  # unknown, neutral-ish

    # --- Location (0-10) ---
    loc_text = (job.location_text or "").lower()
    if job.remote:
        score += 10
        reasons.append("remote")
    elif job.seattle or "seattle" in loc_text:
        score += 10
        reasons.append("Seattle-based")
    elif "hybrid" in loc_text:
        score += 7
        reasons.append("hybrid")
    else:
        score += 2

    # --- Comp (0-5, neutral if unlisted) ---
    if job.salary_max is not None:
        if job.salary_max >= p.COMP_MIN:
            score += 5
            reasons.append(f"salary range reaches ${job.salary_max:,.0f}")
        elif job.salary_max >= p.COMP_MIN * 0.8:
            score += 2
        # else: below range, no points, no penalty beyond that

    # --- Funding stage bonus (best-effort, Wellfound-style listings) ---
    if job.funding_stage and any(
        re.search(pat, job.funding_stage, re.I) for pat in p.FUNDING_STAGE_BONUS_PATTERNS
    ):
        score += p.FUNDING_STAGE_BONUS
        reasons.append(f"recently funded ({job.funding_stage})")

    # --- Staleness penalty ---
    if job.date_posted:
        try:
            posted = datetime.fromisoformat(job.date_posted.replace("Z", "+00:00")).date()
            age_days = (date.today() - posted).days
            if age_days > p.STALE_DAYS:
                score -= p.STALE_SCORE_PENALTY
                reasons.append(f"posting is {age_days // 30} months old, may be stale")
        except ValueError:
            pass

    score = max(0, min(100, round(score)))
    reason = "; ".join(reasons) if reasons else "Limited signal in posting text"
    return score, reason, False, None
