import logging
import time

import requests

import config

log = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 5


def search(query: str, num_results: int = 10) -> list[dict]:
    """Query the local SearXNG instance and return raw result dicts.

    Retries a few times on transient network/DNS failures (common on flaky
    campus/VPN networks) before giving up on this query.
    """
    resp = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(
                f"{config.SEARXNG_URL}/search",
                params={"q": query, "format": "json", "language": "en"},
                timeout=20,
            )
            resp.raise_for_status()
            break
        except requests.RequestException:
            if attempt == MAX_RETRIES:
                log.exception("SearXNG query failed after %d attempts: %s", MAX_RETRIES, query)
                return []
            log.warning(
                "SearXNG query failed (attempt %d/%d), retrying in %ds: %s",
                attempt, MAX_RETRIES, RETRY_DELAY_SECONDS, query,
            )
            time.sleep(RETRY_DELAY_SECONDS)

    results = resp.json().get("results", [])[:num_results]
    return [
        {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "snippet": r.get("content", ""),
        }
        for r in results
        if r.get("url")
    ]
