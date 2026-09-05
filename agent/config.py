import os

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:3b")

SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://localhost:8888")

# JSearch (RapidAPI) is the job source — real dated postings with apply links,
# not scraped career pages. Free tier is ~200 requests/month, so the per-region
# country + query lists below are deliberately small. Get a key at
# https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch and set it in .env.
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY", "")
JSEARCH_HOST = os.environ.get("JSEARCH_HOST", "jsearch.p.rapidapi.com")

# Personal — set GMAIL_TO in your local .env (see .env.example). No real
# address is committed to this repo.
GMAIL_TO = os.environ.get("GMAIL_TO", "your_email@example.com")
GMAIL_CREDENTIALS_PATH = os.environ.get("GMAIL_CREDENTIALS_PATH", "/app/secrets/credentials.json")
GMAIL_TOKEN_PATH = os.environ.get("GMAIL_TOKEN_PATH", "/app/secrets/token.json")

# All Google scopes the agent uses. If you add one, delete secrets/token.json and
# re-run `python agent/gmail_auth_setup.py` so the new consent is granted.
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]

# The Google Sheet the agent keeps up to date. It creates one named SHEET_NAME on
# first run and remembers its id in SHEET_ID_PATH; set SHEET_ID in .env to point
# at an existing sheet instead.
SHEET_NAME = os.environ.get("SHEET_NAME", "Second Brain — Internships")
SHEET_ID = os.environ.get("SHEET_ID", "")
SHEET_ID_PATH = os.environ.get("SHEET_ID_PATH", "/app/data/sheet_id.txt")

DIGEST_HOUR = int(os.environ.get("DIGEST_HOUR", "7"))
DIGEST_MINUTE = int(os.environ.get("DIGEST_MINUTE", "0"))
TIMEZONE = os.environ.get("TIMEZONE", "America/Guayaquil")

DB_PATH = os.environ.get("DB_PATH", "/app/data/tracker.db")

TARGET_SEASON = "Summer 2027 (May-August 2027)"
# 90-day window: Summer 2027 programs at big tech / Europe / Japan open as early
# as Sept-Nov 2026, well ahead of the season. A 30-day window was dropping those
# early postings before they could be surfaced.
POSTED_WITHIN_DAYS = 90

# Employers known to sponsor work visas / support international students for their
# internships (from the user's own research). A posting from one of these passes
# `meets_support_requirements` automatically — the model doesn't have to find the
# sponsorship language in the posting text, which it usually can't. This global
# set applies everywhere; each region can add region-specific ones via "sponsors".
# Keys are normalised: lowercase, alphanumeric only (see tracker.company_key).
KNOWN_SPONSOR_COMPANIES = {
    # finance / trading — sponsor interns as a matter of course
    "revolut", "quantco", "optiver", "janestreet", "imctrading", "imc",
    "drw", "sig", "susquehanna", "susquehannainternationalgroup", "citadel",
    "citadelsecurities", "bankofamerica", "bofa", "blackrock",
    # big tech — established international internship programs
    "amazon", "google", "alphabet", "microsoft", "meta", "metaplatforms",
    "uber", "ubertechnologies", "booking", "bookingcom", "bookingholdings",
    "palantir", "palantirtechnologies",
    # research / other
    "cern", "asml", "hennge",
}

# When matching, a sponsor entry of 6+ chars also matches as a substring of the
# company key ("amazon" matches "amazondevelopmentcenter"). Shorter entries
# (drw, sig, imc, bofa, cern) must match exactly to avoid false positives.
SPONSOR_SUBSTR_MINLEN = 6


def is_known_sponsor(company: str, extra: set | frozenset = frozenset()) -> bool:
    key = "".join(c for c in (company or "").lower() if c.isalnum())
    if not key:
        return False
    pool = KNOWN_SPONSOR_COMPANIES | set(extra)
    if key in pool:
        return True
    return any(len(s) >= SPONSOR_SUBSTR_MINLEN and s in key for s in pool)

# Personal — the real CV/profile text lives in secrets/cv_profile.txt (gitignored,
# never committed). See secrets/cv_profile.example.txt for the expected format.
# Falls back to a generic placeholder so the repo still runs without personal data.
CV_PROFILE_PATH = os.environ.get("CV_PROFILE_PATH", "/app/secrets/cv_profile.txt")
_CV_PROFILE_PLACEHOLDER = """
A college student looking for internships. Replace secrets/cv_profile.txt with your
own CV summary — education, skills, experience, languages, home country — so the
classifier can judge listings against your actual profile instead of this placeholder.
""".strip()


def _load_cv_profile() -> str:
    try:
        with open(CV_PROFILE_PATH) as f:
            text = f.read().strip()
        return text or _CV_PROFILE_PLACEHOLDER
    except FileNotFoundError:
        return _CV_PROFILE_PLACEHOLDER


CV_PROFILE = _load_cv_profile()

# Each region:
#   quota                - how many listings the digest wants for this region
#   countries            - ISO country codes to run each query against (JSearch
#                          takes one country per call, so this multiplies request
#                          count: keep it small for the free RapidAPI tier)
#   queries              - JSearch query strings (role-focused; JSearch handles
#                          the location via `country`)
#   requires_full_support- listing must offer visa sponsorship + relocation/
#                          housing/stipend to count (false for the home country)
#   posted_within_days   - recency window, enforced deterministically against the
#                          posting's real timestamp (no longer an LLM guess)
REGIONS = {
    # NOTE on request budget: each (country x query) pair is one JSearch call.
    # Free RapidAPI tier is ~200 calls/month; the agent runs every other day
    # (~15 runs/month). Below: europe 8 + japan 2 + australia 1 + argentina 1 +
    # ecuador 1 = 13/run → ~195/month. Europe is weighted heavily on purpose.
    #
    # Keep queries SHORT — "software engineer intern", "cybersecurity intern".
    # JSearch does literal keyword matching against a sparse non-US index:
    # "cybersecurity internship international student visa" returns ZERO, while
    # "cybersecurity intern" returns ~10. The visa / international-student intent
    # is enforced by requires_full_support + the model reading the posting text,
    # NOT by query wording.
    "europe": {
        "label": "Europe",
        "quota": 20,
        "countries": ["de", "nl", "gb", "fr"],
        "requires_full_support": True,
        "posted_within_days": POSTED_WITHIN_DAYS,
        "queries": ["software engineer intern", "cybersecurity intern"],
    },
    "japan": {
        "label": "Japan",
        "quota": 10,
        "countries": ["jp"],
        "requires_full_support": True,
        "posted_within_days": POSTED_WITHIN_DAYS,
        "queries": ["software engineer intern", "cloud security intern"],
        # Amazon Japan (SDE Intern Summer 2027), HENNGE (cloud security, no Japanese
        # required, visa + round-trip flights + monthly subsidy), Microsoft Japan
        # (STEM 3rd-year) all sponsor / support international interns.
        "sponsors": {"amazon", "hennge", "microsoft"},
    },
    "australia": {
        "label": "Australia",
        "quota": 10,
        "countries": ["au"],
        "requires_full_support": True,
        "posted_within_days": POSTED_WITHIN_DAYS,
        "queries": ["software engineer intern"],
        # Australia is a weak region for this search: its internship season is
        # Nov-Feb (not May-Aug), and most listings require existing Australian work
        # rights (Mastercard AU explicitly won't sponsor interns; BHP needs work
        # rights; Palantir AU needs clearance eligibility + 2027 grads). Kept at one
        # query — expect little.
    },
    "argentina": {
        "label": "Argentina (Buenos Aires)",
        "quota": 5,
        "countries": ["ar"],
        "requires_full_support": True,
        "posted_within_days": POSTED_WITHIN_DAYS,
        "queries": ["software engineer intern"],
        # Not a priority — only surface genuinely suitable finds. The strong local
        # names (Globant, Mercado Libre, Accenture AR, IBM AR, BairesDev, PwC/EY AR)
        # are all "verify sponsorship" — none are auto-passed; they'll appear with
        # the "visa support not stated" caveat if the search turns them up.
    },
    "ecuador": {
        "label": "Ecuador",
        "quota": 10,
        "countries": ["ec"],
        # Home country: no work visa or relocation/housing/stipend requirement.
        "requires_full_support": False,
        "posted_within_days": POSTED_WITHIN_DAYS,
        # JSearch has almost no Ecuador inventory — one query is enough.
        "queries": ["software developer intern"],
    },
}
