import logging

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (SecondBrainInternshipMonitor/1.0)"


def fetch_page_text(url: str, max_chars: int = 6000) -> str | None:
    """Fetch a URL and return its visible text, or None if it's not live/reachable.

    This is the guard against sending fabricated or dead listings: nothing goes
    into the digest unless its page actually loaded.
    """
    try:
        resp = requests.get(
            url, headers={"User-Agent": USER_AGENT}, timeout=15, allow_redirects=True
        )
    except requests.RequestException:
        log.info("Unreachable, skipping: %s", url)
        return None

    if resp.status_code >= 400:
        log.info("HTTP %s, skipping: %s", resp.status_code, url)
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()

    text = " ".join(soup.get_text(separator=" ").split())
    if len(text) < 200:
        # Likely a JS-rendered shell with no real content to validate against.
        log.info("Too little text to validate, skipping: %s", url)
        return None

    return text[:max_chars]
