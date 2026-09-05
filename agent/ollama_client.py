import json
import logging

import requests

import config

log = logging.getLogger(__name__)

CLASSIFY_SYSTEM_PROMPT = """You screen job postings for a college student's internship search.
Whether it is an internship, whether it is in the right field, and whether the timing fits have
ALREADY been checked — do not second-guess them. Judge only these two, and mind the defaults:

- meets_support_requirements: TRUE if the text mentions ANY of: visa sponsorship, visa support,
  work permit help, relocation, housing, accommodation, a stipend/subsidy, flights, or
  "international students welcome". FALSE if none of that appears.
- candidate_is_qualified: TRUE by DEFAULT for a rising-junior undergraduate. FALSE only when the
  posting states a bar she clearly fails — a required Master's/PhD, several years of professional
  experience, or an active security clearance. Do NOT use graduation year here.

Don't invent facts. Respond with ONLY a JSON object, no other text.
"""


def pull_model() -> None:
    """Make sure the configured model is present locally before first use."""
    try:
        resp = requests.post(
            f"{config.OLLAMA_BASE_URL}/api/pull",
            json={"model": config.OLLAMA_MODEL, "stream": False},
            timeout=1800,
        )
        resp.raise_for_status()
        log.info("Ollama model ready: %s", config.OLLAMA_MODEL)
    except requests.RequestException:
        log.exception("Failed to pull Ollama model %s", config.OLLAMA_MODEL)


def _generate_json(prompt: str) -> dict | None:
    try:
        resp = requests.post(
            f"{config.OLLAMA_BASE_URL}/api/generate",
            json={
                "model": config.OLLAMA_MODEL,
                "prompt": prompt,
                "format": "json",
                "stream": False,
                "keep_alive": "30m",
                "options": {"temperature": 0.0},
            },
            timeout=240,
        )
        resp.raise_for_status()
    except requests.RequestException:
        log.exception("Ollama generate call failed")
        return None

    raw = resp.json().get("response", "")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        log.warning("Ollama returned non-JSON response: %s", raw[:300])
        return None


def classify_listing(
    listing_text: str,
    region_label: str,
    requires_full_support: bool,
) -> dict:
    """Ask the local model the two remaining fuzzy questions about a posting.

    Internship / relevance / season are screened in Python (screen.py). This
    returns meets_support_requirements (hard gate) and candidate_is_qualified
    (soft caveat), plus company/role_title/reason. On a model failure support
    comes back False so the listing drops; candidate_is_qualified defaults True.
    """
    requirement_line = (
        "This region needs visa support."
        if requires_full_support
        else "Home country — set meets_support_requirements to true regardless."
    )

    prompt = f"""{CLASSIFY_SYSTEM_PROMPT}

STUDENT PROFILE:
{config.CV_PROFILE}

REGION: {region_label}
SUPPORT: {requirement_line}

JOB POSTING (from a job board):
\"\"\"{listing_text}\"\"\"

Return a JSON object with exactly these keys:
{{
  "meets_support_requirements": true/false,
  "candidate_is_qualified": true/false,
  "company": "string or empty",
  "role_title": "string or empty",
  "reason": "one short sentence"
}}
"""

    result = _generate_json(prompt)
    if not result:
        return {
            "meets_support_requirements": False,
            "candidate_is_qualified": True,
            "company": "", "role_title": "", "reason": "model call failed",
        }
    result["meets_support_requirements"] = result.get("meets_support_requirements") is True
    result["candidate_is_qualified"] = result.get("candidate_is_qualified") is not False
    return result


def classify_email_reply(subject: str, snippet: str) -> dict:
    """Classify whether a Gmail reply is a generic ack or something needing attention."""
    prompt = f"""{CLASSIFY_SYSTEM_PROMPT}

Classify this email reply to a job/internship application. "needs_followup" should be true
only if it is something more than a generic "we received your application" acknowledgment
(e.g., an interview request, a rejection, a request for more info, next steps).

SUBJECT: {subject}
BODY SNIPPET: {snippet}

Return a JSON object with exactly these keys:
{{
  "needs_followup": true/false,
  "category": "acknowledgment" | "interview_request" | "rejection" | "info_request" | "other",
  "reason": "one short sentence"
}}
"""
    result = _generate_json(prompt)
    if not result:
        return {"needs_followup": False, "category": "other", "reason": "model call failed"}
    return result
