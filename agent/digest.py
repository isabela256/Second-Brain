from datetime import date


def build_digest_email(listings_by_region: dict, followups: list[dict], summary: dict) -> tuple[str, str]:
    """Build (subject, html_body) for the daily digest."""
    today = date.today().isoformat()
    total_found = sum(len(v) for v in listings_by_region.values())
    subject = f"Second Brain Digest {today} — {total_found} new internships, {len(followups)} follow-ups"

    parts = [f"<h2>Daily Internship Digest — {today}</h2>"]

    parts.append("<h3>Application tracker</h3>")
    parts.append(f"<p><b>Total applications logged:</b> {summary['total_applications']}</p>")
    if summary["by_region"]:
        parts.append("<ul>")
        for region, count in summary["by_region"].items():
            label = "auto-detected from Gmail" if region == "unknown" else region
            parts.append(f"<li>{label}: {count}</li>")
        parts.append("</ul>")

    if followups:
        parts.append("<h3 style='color:#b00'>Needs your attention (real replies, not auto-acks)</h3>")
        parts.append("<ul>")
        for f in followups:
            parts.append(
                f"<li><b>{f.get('company', 'Unknown')}</b> — {f.get('category', '')}: "
                f"{f.get('last_reply_snippet', '')}</li>"
            )
        parts.append("</ul>")
    else:
        parts.append("<p>No new substantive replies since yesterday.</p>")

    for region, listings in listings_by_region.items():
        parts.append(f"<h3>{region} ({len(listings)} found)</h3>")
        if not listings:
            parts.append(
                "<p><i>No postings today were an internship for the target season, "
                "relevant to the major, and — where required — showing visa support "
                "(or from a known-sponsor employer).</i></p>"
            )
            continue
        parts.append("<ol>")
        for item in listings:
            meta_bits = [b for b in (item.get("location"), item.get("posted_at")) if b]
            meta = f" <small>({' · '.join(meta_bits)})</small>" if meta_bits else ""
            caveats = item.get("caveats") or []
            caveat_html = (
                f"<br><small style='color:#b36b00'>&#9888; {'; '.join(caveats)}</small>"
                if caveats else ""
            )
            parts.append(
                f"<li><a href='{item['url']}'>{item.get('role_title') or item['url']}</a>"
                f" — {item.get('company', '')}{meta}"
                f"<br><small>{item.get('reason', '')}</small>{caveat_html}</li>"
            )
        parts.append("</ol>")

    return subject, "\n".join(parts)
