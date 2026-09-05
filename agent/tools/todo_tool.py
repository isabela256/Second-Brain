"""
title: Second Brain Application Tracker
description: Reads the internship application tracker so you can ask about status and follow-ups from the Open WebUI chat.
"""

import sqlite3

DB_PATH = "/app/backend/data/second_brain/tracker.db"


class Tools:
    def get_application_status(self) -> str:
        """Return a summary of all internship applications logged so far, including
        total count per region and which ones have a real reply that needs a follow-up
        (not just an automated acknowledgment)."""
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
        except Exception as e:
            return f"Could not open tracker database: {e}"

        try:
            total = conn.execute("SELECT COUNT(*) c FROM applications").fetchone()["c"]
            by_region = conn.execute(
                "SELECT region, COUNT(*) c FROM applications GROUP BY region"
            ).fetchall()
            followups = conn.execute(
                "SELECT company, role_title, last_reply_category, last_reply_snippet "
                "FROM applications WHERE needs_followup = 1 ORDER BY updated_at DESC"
            ).fetchall()
        finally:
            conn.close()

        lines = [f"Total applications: {total}"]
        lines.append("By region: " + ", ".join(f"{r['region']}={r['c']}" for r in by_region))

        if followups:
            lines.append("\nNeeds follow-up:")
            for f in followups:
                lines.append(
                    f"- {f['company']} ({f['role_title']}): "
                    f"{f['last_reply_category']} — {f['last_reply_snippet']}"
                )
        else:
            lines.append("\nNo pending follow-ups right now.")

        return "\n".join(lines)
