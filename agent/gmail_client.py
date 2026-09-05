import base64
import logging
import re
import time
from email.mime.text import MIMEText

from google.auth.exceptions import TransportError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

import config

log = logging.getLogger(__name__)

SCOPES = config.GOOGLE_SCOPES

MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 10


def _get_credentials() -> Credentials:
    # No scopes arg — use whatever the token was actually granted. Forcing the
    # full GOOGLE_SCOPES list here makes refresh fail with "Scope has changed"
    # when the token predates a scope addition (which would break Gmail too, not
    # just Sheets). gmail_auth_setup.py is where the full list is requested.
    creds = Credentials.from_authorized_user_file(config.GMAIL_TOKEN_PATH)
    if creds and creds.expired and creds.refresh_token:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                creds.refresh(Request())
                break
            except TransportError:
                if attempt == MAX_RETRIES:
                    raise
                log.warning(
                    "Gmail token refresh failed (attempt %d/%d), retrying in %ds",
                    attempt, MAX_RETRIES, RETRY_DELAY_SECONDS,
                )
                time.sleep(RETRY_DELAY_SECONDS)
        with open(config.GMAIL_TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
    return creds


def _service():
    return build("gmail", "v1", credentials=_get_credentials())


def send_digest(subject: str, html_body: str) -> None:
    message = MIMEText(html_body, "html")
    message["to"] = config.GMAIL_TO
    message["subject"] = subject
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

    service = _service()
    service.users().messages().send(userId="me", body={"raw": raw}).execute()
    log.info("Digest email sent to %s", config.GMAIL_TO)


# ATS / job-board domains that are never the employer themselves — don't use the
# sender domain as a company-name fallback for these.
_ATS_DOMAINS = {
    "linkedin.com", "myworkday.com", "smartrecruiters.com", "icims.com",
    "hire.lever.co", "lever.co", "greenhouse.io", "workablemail.com",
    "candidates.workablemail.com", "adp.com", "hireclick.com", "jobvite.com",
    "taleo.net", "successfactors.com", "avature.net", "gmail.com", "google.com",
    "jobalerts.thalesgroup.com", "myworkdayjobs.com", "oraclecloud.com",
    "eightfold.ai", "ashbyhq.com", "recruitee.com", "teamtailor.com",
}

# subject-line patterns that carry the employer name, most specific first
_COMPANY_SUBJECT_PATTERNS = [
    re.compile(r"your application was sent to\s+(.+?)\s*$", re.I),
    re.compile(r"thank you for applying to\s+(.+?)(?:\s*[-–—|#:].*)?\s*$", re.I),
    re.compile(r"thanks for applying to\s+(.+?)(?:\s*[-–—|#:].*)?\s*$", re.I),
    re.compile(r"^(.+?)\s*\|\s*(?:your application|thank you for applying)", re.I),
]

# snippet/body patterns for the employer name — used when the subject is generic
_COMPANY_SNIPPET_PATTERNS = [
    re.compile(r"your application was sent to\s+(.+?)[\.\s]*$", re.I),
    re.compile(r"(?:interest in (?:working at|joining)|opportunities with|application (?:for|to) .{1,60}? at)\s+([A-Z][\w&.\- ]{1,40}?)[\.,!]", re.I),
    re.compile(r"received your (?:application|resume) for .{1,60}? (?:at|with)\s+([A-Z][\w&.\- ]{1,40}?)[\.,!]", re.I),
]

# role-title patterns — deliberately conservative; dedup is company-level, so an
# empty role is fine and a wrong one is worse.
_ROLE_SUBJECT_PATTERNS = [
    re.compile(r"for the position of\s+(.+?)(?:\s*[-–—|#.].*)?\s*$", re.I),
    re.compile(r"for the (.+?) position", re.I),
    re.compile(r"application to\s+(.+?)\s+position", re.I),
]
_ROLE_SNIPPET_PATTERNS = [
    re.compile(r"application for the position of\s+(.+?)[\.\n]", re.I),
    re.compile(r"your (?:application|resume) for the\s+(.+?)\s+(?:position|role|job|opportunity)", re.I),
    re.compile(r"(?:received your resume for the)\s+(.+?)\s+and", re.I),
]


# extracted "company" values that are really role words — fall through to the
# snippet/domain fallback instead of trusting these
_GENERIC_COMPANY = {
    "internship", "intern", "position", "the position", "this position",
    "the role", "this role", "a position", "the internship", "job opportunity",
}


def _clean(name: str) -> str:
    name = re.sub(r"&amp;", "&", name).strip(" \t\r\n.-–—|,")
    name = re.sub(r"^the\s+", "", name, flags=re.I)
    # drop trailing corp suffixes that add noise to dedup
    name = re.sub(r"[,\s]+(Inc|LLC|Ltd|Limited|Corporation|Corp|GmbH|S\.?A\.?)\.?$", "", name, flags=re.I)
    return name.strip()


def _extract_company(subject: str, sender: str, snippet: str) -> str:
    for pat in _COMPANY_SUBJECT_PATTERNS:
        m = pat.search(subject)
        if m:
            name = _clean(m.group(1))
            if name.lower() not in _GENERIC_COMPANY:
                return name
    for pat in _COMPANY_SNIPPET_PATTERNS:
        m = pat.search(snippet)
        if m:
            name = _clean(m.group(1))
            if name.lower() not in _GENERIC_COMPANY:
                return name
    # sender-domain fallback, unless it's a generic ATS host
    dom = sender.split("@")[-1].strip(">").lower()
    root = ".".join(dom.split(".")[-2:]) if "." in dom else dom
    if root and root not in _ATS_DOMAINS and dom not in _ATS_DOMAINS:
        return root.split(".")[0].replace("-", " ").title()
    return ""


def _extract_role(subject: str, snippet: str = "") -> str:
    for pat in _ROLE_SUBJECT_PATTERNS:
        m = pat.search(subject)
        if m:
            return _clean(m.group(1))
    for pat in _ROLE_SNIPPET_PATTERNS:
        m = pat.search(snippet)
        if m:
            return _clean(m.group(1))
    return ""


def scan_applied_companies(newer_than_days: int = 180, max_results: int = 300) -> list[dict]:
    """Scan Gmail for application confirmations and return what was applied to.

    Detects LinkedIn Easy Apply ("your application was sent to X") and the common
    ATS confirmation emails ("Thank you for applying to X"). Company-level is
    reliable; role_title is best-effort (many confirmations don't name the role).
    Returns [{"company": str, "role_title": str, "source": str}] deduped on
    (lowercased company, lowercased role).
    """
    service = _service()
    query = (
        f"newer_than:{newer_than_days}d ("
        'from:jobs-noreply@linkedin.com subject:"your application was sent to" '
        'OR subject:("thank you for applying" OR "thanks for applying" '
        'OR "we have received your application" OR "we received your application" '
        'OR "your application has been received" OR "application received" '
        'OR "thank you for your application" OR "your application" OR "application to"))'
    )

    found: dict[tuple[str, str], dict] = {}
    page_token = None
    fetched = 0
    while fetched < max_results:
        resp = (
            service.users()
            .messages()
            .list(userId="me", q=query, maxResults=min(100, max_results - fetched), pageToken=page_token)
            .execute()
        )
        messages = resp.get("messages", [])
        if not messages:
            break
        for m in messages:
            fetched += 1
            msg = (
                service.users()
                .messages()
                .get(userId="me", id=m["id"], format="metadata", metadataHeaders=["Subject", "From"])
                .execute()
            )
            headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
            subject = headers.get("Subject", "")
            sender = headers.get("From", "")
            snippet = msg.get("snippet", "")

            company = _extract_company(subject, sender, snippet)
            if not company:
                continue
            role = _extract_role(subject, snippet)
            source = "linkedin" if "linkedin.com" in sender else "ats_email"
            key = (company.lower(), role.lower())
            found.setdefault(key, {"company": company, "role_title": role, "source": source})

        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    return list(found.values())


def scan_application_replies(max_results: int = 25) -> list[dict]:
    """Return recent inbox messages that look like replies to job/internship applications."""
    service = _service()
    query = (
        "newer_than:3d ("
        'subject:(application OR internship OR interview OR candidacy) '
        "OR from:(noreply OR careers OR recruiting OR talent OR hr))"
    )
    resp = (
        service.users()
        .messages()
        .list(userId="me", q=query, maxResults=max_results)
        .execute()
    )
    messages = resp.get("messages", [])

    results = []
    for m in messages:
        msg = (
            service.users()
            .messages()
            .get(userId="me", id=m["id"], format="metadata", metadataHeaders=["Subject", "From"])
            .execute()
        )
        headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
        results.append(
            {
                "id": m["id"],
                "subject": headers.get("Subject", ""),
                "from": headers.get("From", ""),
                "snippet": msg.get("snippet", ""),
            }
        )
    return results
