"""
Candidate profile and screening rules used by scoring.py.

Edit this file directly to tune what counts as a good fit — nothing about
your background lives in the scraper or scoring code itself.
"""

# --- Target titles -----------------------------------------------------
# Used for keyword pre-filtering on job-board listing pages before we
# bother fetching/scoring the full posting.
TARGET_TITLE_KEYWORDS = [
    "program manager",
    "principal program manager",
    "technical program manager",
    "chief of staff",
    "head of operations",
    "director of operations",
    "vp of operations",
    "vice president of operations",
    "head of pmo",
    "pmo lead",
    "pmo manager",
    "transformation lead",
    "transformation manager",
    "change management lead",
    "change management manager",
    "ai program manager",
    "enterprise ai manager",
    "portfolio manager",
    "operations manager",
]

# --- Skills / strengths used for positive scoring -----------------------
STRENGTH_KEYWORDS = [
    "program governance", "governance", "intake process", "portfolio process",
    "portfolio management", "executive reporting", "steering committee",
    "cross-functional", "cross functional", "change management",
    "training adoption", "adoption", "vendor management",
    "vendor performance", "ai adoption", "ai tools", "ai-enabled",
    "artificial intelligence", "process improvement", "operational excellence",
    "stakeholder management", "roadmap", "kpi", "okr", "dashboard",
    "automation", "pmp", "alteryx", "data-driven", "continuous improvement",
]

CERTS = ["Your Cert 1", "Your Cert 2 (in progress)"]  # example placeholders

# --- Hard exclude signals ------------------------------------------------
# Example placeholders — replace with companies you personally want to
# exclude (past employers, competitors, industries you're avoiding, etc.)
EXCLUDE_COMPANY_NAMES = [
    "example-company-1", "example-company-2", "example-company-3",
]

EXCLUDE_TITLE_KEYWORDS = [
    "implementation manager", "customer success", "onboarding specialist",
    "professional services", "solutions consultant", "account executive",
    "business development", "sales director", "sales manager",
    "quota", "electrical engineer", "mechanical engineer",
    "data center technician", "network engineer", "security clearance",
]

EXCLUDE_DESCRIPTION_KEYWORDS = [
    "security clearance required", "must possess an active clearance",
    "top secret clearance", "quota-carrying", "quota carrying",
    "consulting experience required", "prior consulting experience",
]

# Firms that are inherently formal external consulting shops.
# Example placeholders — replace with real firm names.
EXCLUDE_CONSULTING_FIRMS = ["example-consulting-firm-1", "example-consulting-firm-2"]

# --- Degree language -----------------------------------------------------
DEGREE_REQUIRED_PATTERNS = [
    r"bachelor'?s degree required",
    r"must have a bachelor'?s",
    r"requires a bachelor'?s degree",
]
DEGREE_FLEX_PATTERNS = [
    r"or equivalent experience",
    r"or equivalent practical experience",
    r"in lieu of a degree",
    r"or equivalent combination of education and experience",
]

# --- Comp target -----------------------------------------------------------
COMP_MIN = 120_000   # example — set to your target minimum comp
COMP_MAX = 160_000   # example — set to your target maximum comp

# Hard floor: if a listed salary range doesn't reach this, exclude outright
# rather than just scoring it down. Roles with no salary listed at all are
# unaffected (comp stays neutral in scoring until you look at the posting).
MIN_ACCEPTABLE_SALARY = 100_000

# --- Location preference -----------------------------------------------
PREFERRED_LOCATIONS = ["your-city", "remote", "hybrid"]  # example placeholder

# Hard requirement, not just a preference: set this if you can't relocate
# on a whim, so a listing with no matching-city office and no remote option
# isn't actually applicable no matter how well titles/skills match.
REQUIRE_SEATTLE_OR_REMOTE = True

# --- Company size preference --------------------------------------------
SIZE_SWEET_MIN = 50
SIZE_SWEET_MAX = 2000

# --- Posting freshness -----------------------------------------------------
# Some boards (Wellfound especially) leave postings up long after they've
# gone cold.
STALE_DAYS = 180          # soft penalty past this age ("may be stale")
STALE_SCORE_PENALTY = 15
DEAD_DAYS = 365           # hard exclude past this age (assume no longer open)

# --- Funding stage bonus ----------------------------------------------------
FUNDING_STAGE_BONUS_PATTERNS = [
    r"\bseed\b", r"\bseries [abc]\b", r"\brecent yc batch\b",
]
FUNDING_STAGE_BONUS = 5

# --- Digest filtering -----------------------------------------------------
# Jobs below this score are dropped from the digest even if not hard-excluded
# (they already passed a title-keyword prefilter, so this only trims weak
# overlaps rather than acting as the primary filter).
MIN_SCORE_TO_SHOW = 35
