import logging
import time

import requests

import config

log = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 5

# JSearch (RapidAPI) aggregates Google for Jobs — LinkedIn, Indeed, Glassdoor,
# ZipRecruiter and company career sites — and returns each posting with a real
# apply link and a real posted-at timestamp. That is the whole point of moving
# off SearXNG scraping: we get individual dated job postings instead of career
# landing pages with no date on them.
#   Docs: https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch
#
# The classic /search endpoint was retired; /search-v2 returns the same job
# fields nested under data.jobs and paginates with an opaque `cursor`.
SEARCH_PATH = "/search-v2"


def _date_posted_param(posted_within_days: int) -> str:
    if posted_within_days <= 1:
        return "today"
    if posted_within_days <= 3:
        return "3days"
    if posted_within_days <= 7:
        return "week"
    if posted_within_days <= 31:
        return "month"
    return "all"


def _normalize(job: dict) -> dict:
    location = ", ".join(
        p for p in (job.get("job_city"), job.get("job_state"), job.get("job_country")) if p
    )
    return {
        "url": job.get("job_apply_link") or job.get("job_google_link") or "",
        "title": job.get("job_title") or "",
        "company": job.get("employer_name") or "",
        "location": location or ("Remote" if job.get("job_is_remote") else ""),
        "is_remote": bool(job.get("job_is_remote")),
        # epoch seconds (int) or None — used for the deterministic recency check
        "posted_timestamp": job.get("job_posted_at_timestamp"),
        "posted_at": job.get("job_posted_at_datetime_utc") or "",
        "description": job.get("job_description") or "",
        "highlights": job.get("job_highlights") or {},
        "employment_type": job.get("job_employment_type") or "",
    }


def search_jobs(
    query: str,
    country: str,
    posted_within_days: int,
) -> list[dict]:
    """Query JSearch for internship postings and return normalized listing dicts.

    One request = one page (~10 results). Returns [] on a missing API key,
    quota exhaustion, or repeated network failure — the caller treats an empty
    list as "no results for this query", never as an error that aborts the run.
    """
    if not config.RAPIDAPI_KEY:
        log.error(
            "RAPIDAPI_KEY is not set — cannot query JSearch. "
            "Get a free key at https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch "
            "and put it in your .env (see .env.example)."
        )
        return []

    # NOTE: do NOT send `language` — JSearch filters results to that language, and
    # `language=en` against a non-US country (e.g. Germany) returns zero postings.
    params = {
        "query": query,
        "country": country.lower(),
        "date_posted": _date_posted_param(posted_within_days),
        "employment_types": "INTERN",
    }
    headers = {
        "X-RapidAPI-Key": config.RAPIDAPI_KEY,
        "X-RapidAPI-Host": config.JSEARCH_HOST,
    }

    resp = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(
                f"https://{config.JSEARCH_HOST}{SEARCH_PATH}",
                params=params,
                headers=headers,
                timeout=25,
            )
            if resp.status_code in (429, 403):
                # RapidAPI monthly/rate quota hit — retrying won't help this run.
                log.error(
                    "JSearch quota/rate limit hit (HTTP %s) for query: %s",
                    resp.status_code, query,
                )
                return []
            resp.raise_for_status()
            break
        except requests.RequestException:
            if attempt == MAX_RETRIES:
                log.exception("JSearch query failed after %d attempts: %s", MAX_RETRIES, query)
                return []
            log.warning(
                "JSearch query failed (attempt %d/%d), retrying in %ds: %s",
                attempt, MAX_RETRIES, RETRY_DELAY_SECONDS, query,
            )
            time.sleep(RETRY_DELAY_SECONDS)

    payload = resp.json().get("data") or {}
    # /search-v2 nests jobs under data.jobs; tolerate a bare list too.
    jobs = payload.get("jobs", []) if isinstance(payload, dict) else payload
    return [
        _normalize(j) for j in jobs
        if j.get("job_apply_link") or j.get("job_google_link")
    ]


def format_for_classification(job: dict, max_chars: int = 6000) -> str:
    """Flatten a normalized job dict into the text block the classifier reads."""
    lines = [
        f"TITLE: {job['title']}",
        f"COMPANY: {job['company']}",
        f"LOCATION: {job['location']}" + ("  (remote)" if job["is_remote"] else ""),
        f"EMPLOYMENT TYPE: {job['employment_type']}",
        f"POSTED: {job['posted_at'] or 'unknown'}",
        "",
    ]
    highlights = job.get("highlights") or {}
    for section in ("Qualifications", "Responsibilities", "Benefits"):
        items = highlights.get(section)
        if items:
            lines.append(f"{section.upper()}:")
            lines.extend(f"- {item}" for item in items)
            lines.append("")
    if job.get("description"):
        lines.append("DESCRIPTION:")
        lines.append(job["description"])

    return "\n".join(lines)[:max_chars]
