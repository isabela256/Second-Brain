"""Keeps a Google Sheet as the living record of internship listings.

The sheet — not the email — is the source of truth. Each run:
  * new postings are appended as rows (Status "New")
  * rows already in the sheet get their "Last seen" bumped, and their Caveats
    refreshed if they changed
  * Status is auto-advanced from Gmail: "Applied?" when a confirmation email shows
    up for that company, "Reply: <kind>" when a real reply lands
  * a column the user edits by hand (Status, Notes) is never overwritten once it
    holds anything other than "New"

Needs the spreadsheets + drive.file scopes (config.GOOGLE_SCOPES). If the token
doesn't have them yet, every call here raises and daily_job falls back to email.
"""

import datetime
import logging
import os
import time

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

import config
import gmail_client
import tracker

log = logging.getLogger(__name__)

# The Sheets/Drive backends return a transient 500/503 (and 429 on rate) fairly
# often — a bare .execute() then loses the whole run's results to the email
# fallback. Retry those a few times with backoff before giving up.
_RETRY_STATUSES = {429, 500, 503}
_MAX_TRIES = 5


def _execute(request):
    """request.execute() with backoff on transient Google API errors."""
    for attempt in range(1, _MAX_TRIES + 1):
        try:
            return request.execute()
        except HttpError as e:
            status = getattr(e, "status_code", None) or getattr(e.resp, "status", None)
            try:
                status = int(status)
            except (TypeError, ValueError):
                status = None
            if status not in _RETRY_STATUSES or attempt == _MAX_TRIES:
                raise
            wait = 2 ** attempt
            log.warning(
                "Google API %s (attempt %d/%d) — retrying in %ds", status, attempt, _MAX_TRIES, wait
            )
            time.sleep(wait)

TAB = "Listings"
COLUMNS = [
    "Date found", "Region", "Company", "Role", "Location", "Posted", "Apply link",
    "Support", "Caveats", "Status", "Last seen", "Last update", "Notes",
]
_APPLY_COL = COLUMNS.index("Apply link")
_STATUS_COL = COLUMNS.index("Status")
_LASTSEEN_COL = COLUMNS.index("Last seen")
_LASTUPDATE_COL = COLUMNS.index("Last update")
_CAVEATS_COL = COLUMNS.index("Caveats")

# Statuses the user hasn't touched yet — safe for the agent to advance.
_UNTOUCHED = {"", "new", "found"}


def _today() -> str:
    return datetime.date.today().isoformat()


def _creds():
    return gmail_client._get_credentials()


def _sheets():
    return build("sheets", "v4", credentials=_creds()).spreadsheets()


def _drive():
    return build("drive", "v3", credentials=_creds())


def _a1_col(idx: int) -> str:
    return chr(ord("A") + idx)


# ---------------------------------------------------------------- sheet resolution


def _remembered_id() -> str:
    if config.SHEET_ID:
        return config.SHEET_ID
    try:
        with open(config.SHEET_ID_PATH) as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


def _remember_id(sheet_id: str) -> None:
    os.makedirs(os.path.dirname(config.SHEET_ID_PATH), exist_ok=True)
    with open(config.SHEET_ID_PATH, "w") as f:
        f.write(sheet_id)


def _find_in_drive() -> str:
    q = (
        f"name = '{config.SHEET_NAME}' and "
        "mimeType = 'application/vnd.google-apps.spreadsheet' and trashed = false"
    )
    resp = _execute(_drive().files().list(q=q, spaces="drive", fields="files(id,name)"))
    files = resp.get("files", [])
    return files[0]["id"] if files else ""


def _create_sheet() -> str:
    sh = _execute(_sheets().create(
        body={"properties": {"title": config.SHEET_NAME}, "sheets": [{"properties": {"title": TAB}}]}
    ))
    sheet_id = sh["spreadsheetId"]
    grid_id = sh["sheets"][0]["properties"]["sheetId"]

    _execute(_sheets().values().update(
        spreadsheetId=sheet_id,
        range=f"{TAB}!A1",
        valueInputOption="RAW",
        body={"values": [COLUMNS]},
    ))
    _execute(_sheets().batchUpdate(
        spreadsheetId=sheet_id,
        body={"requests": [
            {"updateSheetProperties": {
                "properties": {"sheetId": grid_id, "gridProperties": {"frozenRowCount": 1}},
                "fields": "gridProperties.frozenRowCount",
            }},
            {"repeatCell": {
                "range": {"sheetId": grid_id, "startRowIndex": 0, "endRowIndex": 1},
                "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                "fields": "userEnteredFormat.textFormat.bold",
            }},
        ]},
    ))
    log.info("Created Google Sheet %r (%s)", config.SHEET_NAME, sheet_id)
    return sheet_id


def resolve_sheet_id() -> str:
    sheet_id = _remembered_id()
    if sheet_id:
        try:
            _execute(_sheets().get(spreadsheetId=sheet_id, fields="spreadsheetId"))
            return sheet_id
        except Exception:
            log.warning("Remembered sheet %s not reachable — looking again", sheet_id)
    sheet_id = _find_in_drive() or _create_sheet()
    _remember_id(sheet_id)
    return sheet_id


def sheet_url(sheet_id: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit"


# ---------------------------------------------------------------------- row access


def _read_rows(sheet_id: str) -> list[list[str]]:
    resp = _execute(_sheets().values().get(
        spreadsheetId=sheet_id, range=f"{TAB}!A2:{_a1_col(len(COLUMNS) - 1)}"
    ))
    return resp.get("values", [])


def _support_label(item: dict) -> str:
    caveats = item.get("caveats") or []
    if any("known visa sponsor" in c for c in caveats):
        return "known sponsor"
    if any("visa support not stated" in c for c in caveats):
        return "unconfirmed"
    return "stated"


# --------------------------------------------------------------------- public sync

def sync_listings(listings: list[dict]) -> tuple[int, int, str]:
    """Append new listings, refresh existing ones. Returns (added, updated, url)."""
    sheet_id = resolve_sheet_id()
    rows = _read_rows(sheet_id)
    by_url = {
        r[_APPLY_COL]: (i + 2, r)
        for i, r in enumerate(rows)
        if len(r) > _APPLY_COL and r[_APPLY_COL]
    }

    today = _today()
    appends: list[list[str]] = []
    value_updates: list[dict] = []

    for it in listings:
        url = it["url"]
        caveats = "; ".join(it.get("caveats") or [])
        if url in by_url:
            rownum, existing = by_url[url]
            existing += [""] * (len(COLUMNS) - len(existing))
            changed = False
            if existing[_LASTSEEN_COL] != today:
                existing[_LASTSEEN_COL] = today
                changed = True
            if caveats and existing[_CAVEATS_COL] != caveats:
                existing[_CAVEATS_COL] = caveats
                existing[_LASTUPDATE_COL] = today
                changed = True
            if changed:
                value_updates.append({
                    "range": f"{TAB}!A{rownum}:{_a1_col(len(COLUMNS) - 1)}{rownum}",
                    "values": [existing[:len(COLUMNS)]],
                })
        else:
            appends.append([
                today, it.get("region", ""), it.get("company", ""),
                it.get("role_title", ""), it.get("location", ""), it.get("posted_at", ""),
                url, _support_label(it), caveats, "New", today, today, "",
            ])

    if value_updates:
        _execute(_sheets().values().batchUpdate(
            spreadsheetId=sheet_id,
            body={"valueInputOption": "RAW", "data": value_updates},
        ))
    if appends:
        _execute(_sheets().values().append(
            spreadsheetId=sheet_id,
            range=f"{TAB}!A2",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": appends},
        ))

    return len(appends), len(value_updates), sheet_url(sheet_id)


def apply_status(company_to_status: dict[str, str]) -> int:
    """Advance Status for rows whose company matches, but only if the user hasn't
    already set a status. company_to_status maps a normalized company key to the
    new status text. Returns the number of rows changed."""
    if not company_to_status:
        return 0
    sheet_id = resolve_sheet_id()
    rows = _read_rows(sheet_id)
    today = _today()
    updates = []
    for i, r in enumerate(rows):
        r += [""] * (len(COLUMNS) - len(r))
        key = tracker.company_key(r[COLUMNS.index("Company")])
        new_status = company_to_status.get(key)
        if not new_status:
            continue
        if r[_STATUS_COL].strip().lower() not in _UNTOUCHED:
            continue
        if r[_STATUS_COL] == new_status:
            continue
        r[_STATUS_COL] = new_status
        r[_LASTUPDATE_COL] = today
        updates.append({
            "range": f"{TAB}!A{i + 2}:{_a1_col(len(COLUMNS) - 1)}{i + 2}",
            "values": [r[:len(COLUMNS)]],
        })
    if updates:
        _execute(_sheets().values().batchUpdate(
            spreadsheetId=sheet_id,
            body={"valueInputOption": "RAW", "data": updates},
        ))
    return len(updates)
