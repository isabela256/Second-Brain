"""Deterministic pre-screening that doesn't need an LLM.

qwen2.5:3b is unreliable on the mechanical checks — it rejected "Software Dev
Engineer Internship" as not software engineering, and read the student's 2028
graduation as a conflict with a Summer 2027 posting. Those are keyword/metadata
checks, so we do them here. The model is left with only visa support and
candidate fit.
"""

import re

_INTERNSHIP_WORDS = (
    "intern", "internship", "co-op", "coop", "working student", "werkstudent",
    "praktikum", "praktikant", "stage", "stagiair", "pasant", "becario", "beca",
    "trainee", "graduate program", "graduate programme", "early career",
    "student program", "student programme",
)

_RELEVANT_WORDS = (
    "software", "developer", "development", "engineer", "engineering", "swe", "sde",
    "backend", "back-end", "back end", "frontend", "front-end", "front end",
    "full stack", "fullstack", "full-stack", "web", "mobile", "ios", "android",
    "app ", "application", "programming", "programmer", "python", "java", "javascript",
    "typescript", "golang", " go ", "rust", "c++", "computer science",
    "security", "cyber", "cybersecurity", "infosec", "information security",
    "appsec", "application security", "soc analyst", "penetration", "pentest",
    "vulnerability", "cryptography", "cloud", "devops", "site reliability", "sre",
    "platform engineer", "data engineer", "machine learning", "ml engineer", "ai engineer",
    "it ", "information technology", "systems", "network",
)

# titles that are clearly a different field even if a relevant word appears somewhere
_HARD_NEGATIVE_TITLE = (
    "mechanical", "civil", "chemical", "electrical engineer", "industrial engineer",
    "sales", "marketing", "recruit", "human resources", " hr ", "talent acquisition",
    "accounting", "accountant", "finance intern", "legal", "paralegal", "nurse",
    "teacher", "translator", "editorial", "content writer", "graphic design",
    "supply chain", "logistics", "procurement", "quality assurance - ra/qa",
)


def _norm(s: str) -> str:
    return f" {(s or '').lower()} "


def is_internship(job: dict) -> bool:
    types = [t.upper() for t in (job.get("job_employment_types") or [])]
    if "INTERN" in types or job.get("employment_type", "").upper().find("INTERN") >= 0:
        return True
    hay = _norm(job.get("title", "")) + _norm(job.get("description", "")[:600])
    return any(w in hay for w in _INTERNSHIP_WORDS)


def relevant_to_major(job: dict) -> bool:
    title = _norm(job.get("title", ""))
    if any(neg in title for neg in _HARD_NEGATIVE_TITLE):
        # unless the title also clearly names a software/security role
        if not any(w in title for w in ("software", "security", "cyber", "developer", " swe", " sde")):
            return False
    hay = title + _norm(job.get("description", "")[:1200])
    return any(w in hay for w in _RELEVANT_WORDS)


# Explicit signals that a posting targets an intake that is NOT Summer 2027.
# Deliberately narrow: recency is already enforced deterministically against the
# posting's real timestamp in daily_job, so this only needs to catch the case
# where a still-live posting explicitly names an earlier season. We flag ONLY a
# season word next to a wrong year, or a wrong year next to an intake word.
# Everything vaguer — a stray "2025" in an "about us" blurb, "immediate start",
# a past cohort mentioned in passing, a "start date September 2026" line — is NOT
# treated as a conflict; scanning free text for any 4-digit year produced too
# many false positives on valid postings.
_OK_YEARS = ("2027", "2028")  # target season, or the student's graduation year
_WRONG_YEAR = r"20(?:1\d|2[0-6])"  # 2010–2026
_INTAKE_WORDS = r"intake|cohort|programme|program|class|batch|internship"
_SEASONS = r"summer|spring|fall|autumn|winter"
_CONFLICT_PATTERNS = [
    re.compile(rf"\b(?:{_SEASONS})\s+{_WRONG_YEAR}\b", re.I),
    re.compile(rf"\b{_WRONG_YEAR}\s+(?:{_SEASONS})\b", re.I),
    re.compile(rf"\b{_WRONG_YEAR}\s*(?:{_INTAKE_WORDS})\b", re.I),
    re.compile(rf"\b(?:{_INTAKE_WORDS})\s*(?:of\s+)?{_WRONG_YEAR}\b", re.I),
]


def season_conflict(job: dict) -> bool:
    # Full text, untruncated: a "Summer 2027" mention anywhere — even deep in the
    # description — must be able to clear an earlier stray wrong-year phrase.
    text = f"{job.get('title', '')}\n{job.get('description', '')}"
    if any(y in text for y in _OK_YEARS):
        return False
    return any(p.search(text) for p in _CONFLICT_PATTERNS)
