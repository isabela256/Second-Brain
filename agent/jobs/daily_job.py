import logging
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import digest
import gmail_client
import jobs_client
import ollama_client
import screen
import sheets_client
import tracker

log = logging.getLogger(__name__)


def find_listings_for_region(
    region_key: str, region_cfg: dict, applied_keys: set[str] | None = None
) -> list[dict]:
    quota = region_cfg["quota"]
    posted_within_days = region_cfg["posted_within_days"]
    cutoff_ts = time.time() - posted_within_days * 86400
    applied_keys = applied_keys or set()
    found: list[dict] = []
    seen_urls: set[str] = set()

    for country in region_cfg["countries"]:
        for query in region_cfg["queries"]:
            if len(found) >= quota:
                break
            jobs = jobs_client.search_jobs(
                query,
                country=country,
                posted_within_days=posted_within_days,
            )
            for job in jobs:
                if len(found) >= quota:
                    break
                url = job["url"]
                if not url or url in seen_urls or tracker.already_seen(url):
                    continue
                seen_urls.add(url)

                # Deterministic recency check against the posting's real timestamp.
                ts = job.get("posted_timestamp")
                if ts is not None and ts < cutoff_ts:
                    log.info(
                        "Rejected %s: failed=posted_recently (posted %s, window %dd)",
                        url, job.get("posted_at") or "?", posted_within_days,
                    )
                    continue

                # Python screening — the model gets these wrong too often.
                if not screen.is_internship(job):
                    log.info("Rejected %s: failed=is_internship (title=%r)", url, job["title"])
                    continue
                if not screen.relevant_to_major(job):
                    log.info("Rejected %s: failed=relevant_to_major (title=%r)", url, job["title"])
                    continue
                if screen.season_conflict(job):
                    log.info("Rejected %s: failed=season_conflict (title=%r)", url, job["title"])
                    continue

                verdict = ollama_client.classify_listing(
                    listing_text=jobs_client.format_for_classification(job),
                    region_label=region_cfg["label"],
                    requires_full_support=region_cfg["requires_full_support"],
                )

                final_company = verdict.get("company") or job["company"]
                company_k = tracker.company_key(final_company) if final_company else ""
                known_sponsor = config.is_known_sponsor(
                    final_company, region_cfg.get("sponsors", set())
                )

                # Applications differ by location/role, so a prior application to
                # this company is NOT a reason to drop a posting — surfaced as a
                # caveat instead (see below).
                applied_before = bool(company_k) and company_k in applied_keys

                # Hard gate: support is required unless it's the home country or a
                # known sponsor. (Internship / relevance / season were screened above.)
                support_ok = (
                    verdict.get("meets_support_requirements")
                    or not region_cfg["requires_full_support"]
                    or known_sponsor
                )
                if not support_ok:
                    log.info(
                        "Rejected %s: failed=meets_support_requirements | model_reason=%s",
                        url, verdict.get("reason"),
                    )
                    continue

                # Soft signals — surfaced as caveats in the digest, never a reject.
                caveats = []
                if applied_before:
                    prior = tracker.detected_role_for(company_k)
                    caveats.append(
                        f"already applied to {final_company}"
                        + (f' ("{prior}")' if prior else "")
                        + " — confirm this is a different role/location"
                    )
                if verdict.get("candidate_is_qualified") is not True:
                    caveats.append("check you meet the requirements")
                if region_cfg["requires_full_support"] and not verdict.get("meets_support_requirements"):
                    caveats.append(
                        "known visa sponsor" if known_sponsor
                        else "visa support not stated — verify before applying"
                    )

                found.append(
                    {
                        "url": url,
                        "region": region_cfg["label"],
                        "company": final_company,
                        "role_title": verdict.get("role_title") or job["title"],
                        "location": job["location"],
                        "posted_at": job.get("posted_at") or "",
                        "reason": verdict.get("reason", ""),
                        "caveats": caveats,
                    }
                )
                tracker.record_listing_sent(
                    url,
                    region_key,
                    final_company,
                    verdict.get("role_title") or job["title"],
                )

    return found


def scan_and_log_applications() -> set[str]:
    """Scan Gmail for application confirmations, store them, and return the set of
    normalized company keys already applied to."""
    try:
        detected = gmail_client.scan_applied_companies()
    except Exception:
        log.exception("Applied-companies scan failed (check secrets/token.json)")
        return tracker.applied_company_keys()

    new_count = tracker.record_detected_applications(detected)
    log.info(
        "Applied-companies scan: %d companies detected in Gmail (%d new)",
        len(detected), new_count,
    )
    return tracker.applied_company_keys()


def scan_gmail_followups() -> list[dict]:
    followups = []
    try:
        replies = gmail_client.scan_application_replies()
    except Exception:
        log.exception("Gmail scan failed (check secrets/token.json)")
        return followups

    for reply in replies:
        verdict = ollama_client.classify_email_reply(reply["subject"], reply["snippet"])
        if verdict.get("needs_followup"):
            company = gmail_client._extract_company(
                reply.get("subject", ""), reply.get("from", ""), reply.get("snippet", "")
            )
            followups.append(
                {
                    "company": company or reply.get("from", ""),
                    "category": verdict.get("category", ""),
                    "last_reply_snippet": reply.get("snippet", ""),
                }
            )
    return followups


def _status_updates_from_gmail(applied_keys: set[str], followups: list[dict]) -> dict[str, str]:
    """Map normalized company key -> a Status string for the sheet. A real reply
    wins over a plain 'applied' confirmation."""
    updates: dict[str, str] = {k: "Applied?" for k in applied_keys if k}
    for f in followups:
        key = tracker.company_key(f.get("company", ""))
        if not key:
            continue
        cat = (f.get("category") or "other").replace("_", " ")
        updates[key] = f"Reply: {cat}"
    return updates


def run() -> None:
    log.info("Starting daily run")
    tracker.init_db()

    applied_keys = scan_and_log_applications()

    listings_by_region = {}
    for key, region_cfg in config.REGIONS.items():
        log.info("Searching region: %s", region_cfg["label"])
        listings_by_region[region_cfg["label"]] = find_listings_for_region(
            key, region_cfg, applied_keys
        )

    followups = scan_gmail_followups()
    summary = tracker.get_summary()

    all_listings = [it for items in listings_by_region.values() for it in items]

    # The Google Sheet is the record. Email is only a nudge.
    try:
        added, updated, sheet_link = sheets_client.sync_listings(all_listings)
        status_changed = sheets_client.apply_status(
            _status_updates_from_gmail(applied_keys, followups)
        )
        log.info(
            "Sheet synced: +%d new, %d refreshed, %d status changes — %s",
            added, updated, status_changed, sheet_link,
        )
        _notify(added, updated, status_changed, followups, sheet_link)
    except Exception:
        log.exception("Sheet sync failed — falling back to the full email digest. "
                      "If this is a scope error, delete secrets/token.json and re-run "
                      "agent/gmail_auth_setup.py.")
        subject, html_body = digest.build_digest_email(listings_by_region, followups, summary)
        try:
            gmail_client.send_digest(subject, html_body)
        except Exception:
            log.exception("Fallback digest email also failed (check secrets/token.json)")

    log.info("Daily run complete")


def _notify(added: int, updated: int, status_changed: int, followups: list[dict], sheet_link: str) -> None:
    """Short 'something changed' email, sent only when something did."""
    if not (added or status_changed or followups):
        return
    bits = []
    if added:
        bits.append(f"{added} new listing{'s' if added != 1 else ''}")
    if status_changed:
        bits.append(f"{status_changed} status change{'s' if status_changed != 1 else ''}")
    if followups:
        bits.append(f"{len(followups)} reply/replies needing follow-up")
    summary_line = ", ".join(bits)

    followup_html = ""
    if followups:
        rows = "".join(
            f"<li><b>{f.get('company', '?')}</b> — {f.get('category', '')}: "
            f"{f.get('last_reply_snippet', '')}</li>"
            for f in followups
        )
        followup_html = f"<p>Replies to look at:</p><ul>{rows}</ul>"

    subject = f"Second Brain — {summary_line}"
    body = (
        f"<p>{summary_line}. Everything is in the sheet:</p>"
        f'<p><a href="{sheet_link}">{sheet_link}</a></p>{followup_html}'
    )
    try:
        gmail_client.send_digest(subject, body)
    except Exception:
        log.exception("Notification email failed (check secrets/token.json)")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
