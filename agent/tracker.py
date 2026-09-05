import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    url TEXT PRIMARY KEY,
    region TEXT NOT NULL,
    company TEXT,
    role_title TEXT,
    first_seen TEXT NOT NULL,
    last_sent TEXT
);

CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT,
    region TEXT NOT NULL,
    company TEXT,
    role_title TEXT,
    applied_on TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'applied',
    last_reply_category TEXT,
    last_reply_snippet TEXT,
    needs_followup INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual'
);

-- Applications detected from Gmail confirmation emails (LinkedIn / ATS). Used to
-- keep the daily search from re-surfacing something already applied to.
CREATE TABLE IF NOT EXISTS detected_applications (
    company_key TEXT PRIMARY KEY,
    company TEXT NOT NULL,
    role_title TEXT,
    source TEXT NOT NULL,
    first_seen TEXT NOT NULL
);
"""


@contextmanager
def _db():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _db() as conn:
        conn.executescript(SCHEMA)
        # Migrate DBs created before the `source` column existed.
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(applications)")}
        if "source" not in cols:
            conn.execute("ALTER TABLE applications ADD COLUMN source TEXT NOT NULL DEFAULT 'manual'")


def already_seen(url: str) -> bool:
    with _db() as conn:
        row = conn.execute("SELECT 1 FROM listings WHERE url = ?", (url,)).fetchone()
        return row is not None


def record_listing_sent(url: str, region: str, company: str, role_title: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _db() as conn:
        conn.execute(
            """
            INSERT INTO listings (url, region, company, role_title, first_seen, last_sent)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET last_sent = excluded.last_sent
            """,
            (url, region, company, role_title, now, now),
        )


def add_application(url: str, region: str, company: str, role_title: str, source: str = "manual") -> int:
    now = datetime.now(timezone.utc).isoformat()
    with _db() as conn:
        cur = conn.execute(
            """
            INSERT INTO applications (url, region, company, role_title, applied_on, status, updated_at, source)
            VALUES (?, ?, ?, ?, ?, 'applied', ?, ?)
            """,
            (url, region, company, role_title, now, now, source),
        )
        return cur.lastrowid


def company_key(company: str) -> str:
    return "".join(ch for ch in company.lower() if ch.isalnum())

def record_detected_applications(items: list[dict]) -> int:
    """Upsert applications detected from Gmail. Also logs genuinely new ones into
    the applications table (source='gmail') so the tracker count reflects reality.
    Returns the number of newly-seen companies."""
    now = datetime.now(timezone.utc).isoformat()
    new_count = 0
    with _db() as conn:
        for it in items:
            company = (it.get("company") or "").strip()
            if not company:
                continue
            key = company_key(company)
            if not key:
                continue
            role = (it.get("role_title") or "").strip()
            source = it.get("source") or "gmail"
            existing = conn.execute(
                "SELECT 1 FROM detected_applications WHERE company_key = ?", (key,)
            ).fetchone()
            conn.execute(
                """
                INSERT INTO detected_applications (company_key, company, role_title, source, first_seen)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(company_key) DO UPDATE SET
                    role_title = COALESCE(NULLIF(excluded.role_title, ''), detected_applications.role_title)
                """,
                (key, company, role, source, now),
            )
            if existing:
                continue
            new_count += 1
            already_logged = conn.execute(
                "SELECT 1 FROM applications WHERE lower(company) = ? LIMIT 1", (company.lower(),)
            ).fetchone()
            if not already_logged:
                conn.execute(
                    """
                    INSERT INTO applications (url, region, company, role_title, applied_on, status, updated_at, source)
                    VALUES (NULL, 'unknown', ?, ?, ?, 'applied', ?, 'gmail')
                    """,
                    (company, role, now, now),
                )
    return new_count


def applied_company_keys() -> set[str]:
    """Normalized keys of every company applied to — detected-from-Gmail plus
    manually logged — for flagging repeats in the daily search."""
    with _db() as conn:
        keys = {r["company_key"] for r in conn.execute("SELECT company_key FROM detected_applications")}
        for r in conn.execute("SELECT company FROM applications WHERE company IS NOT NULL AND company != ''"):
            keys.add(company_key(r["company"]))
    keys.discard("")
    return keys


def detected_role_for(company_key_val: str) -> str:
    """The role we recorded for a prior application to this company, or "" —
    used to phrase the 'already applied' caveat (applications differ by role/location)."""
    with _db() as conn:
        row = conn.execute(
            "SELECT role_title FROM detected_applications WHERE company_key = ?",
            (company_key_val,),
        ).fetchone()
        return (row["role_title"] if row else "") or ""


def update_application_reply(app_id: int, category: str, snippet: str, needs_followup: bool) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _db() as conn:
        conn.execute(
            """
            UPDATE applications
            SET last_reply_category = ?, last_reply_snippet = ?, needs_followup = ?,
                status = ?, updated_at = ?
            WHERE id = ?
            """,
            (category, snippet, int(needs_followup), category, now, app_id),
        )


def get_summary() -> dict:
    with _db() as conn:
        total = conn.execute("SELECT COUNT(*) c FROM applications").fetchone()["c"]
        by_region = conn.execute(
            "SELECT region, COUNT(*) c FROM applications GROUP BY region"
        ).fetchall()
        needs_followup = conn.execute(
            "SELECT * FROM applications WHERE needs_followup = 1 ORDER BY updated_at DESC"
        ).fetchall()
        return {
            "total_applications": total,
            "by_region": {r["region"]: r["c"] for r in by_region},
            "needs_followup": [dict(r) for r in needs_followup],
        }
