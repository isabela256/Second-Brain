"""One-time Google OAuth setup (Gmail + Sheets + Drive).

You need `secrets/credentials.json` (an OAuth *Desktop app* client from Google
Cloud Console) first, and the Gmail, Sheets, and Drive APIs enabled on that
project.

Easiest: run it inside the agent container, which already has the libraries.
It maps port 8090 for the OAuth redirect and prints a URL to open in your browser:

    docker compose run --rm -p 8090:8090 agent python gmail_auth_setup.py

Or on the host, if you have the deps:

    pip install google-auth-oauthlib google-api-python-client
    python3 agent/gmail_auth_setup.py

Either way it writes secrets/token.json, which the container reuses and refreshes.
Re-run it (delete secrets/token.json first) whenever config.GOOGLE_SCOPES changes.
"""

import os
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

SCOPES = config.GOOGLE_SCOPES

# In the container these are /app/secrets/...; on the host, fall back to the
# repo's secrets/ dir next to this file's parent.
_HOST_SECRETS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "secrets"
)
CREDENTIALS_PATH = (
    config.GMAIL_CREDENTIALS_PATH
    if os.path.exists(config.GMAIL_CREDENTIALS_PATH)
    else os.path.join(_HOST_SECRETS, "credentials.json")
)
TOKEN_PATH = (
    config.GMAIL_TOKEN_PATH
    if os.path.isdir(os.path.dirname(config.GMAIL_TOKEN_PATH))
    else os.path.join(_HOST_SECRETS, "token.json")
)


def main() -> None:
    if not os.path.exists(CREDENTIALS_PATH):
        raise SystemExit(
            f"Missing {CREDENTIALS_PATH}. Download an OAuth client (Desktop app type) "
            "from Google Cloud Console and save it there as credentials.json first."
        )

    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
    # open_browser=False + a fixed port works both on the host and inside the
    # container (with -p 8090:8090). Google's OOB copy-paste flow is dead, so the
    # redirect has to reach a local server.
    creds = flow.run_local_server(
        port=8090,
        open_browser=False,
        bind_addr="0.0.0.0",
        authorization_prompt_message=(
            "\n>>> Open this URL in your browser, approve access, and wait for the "
            "'you may close this window' page:\n\n{url}\n"
        ),
    )

    with open(TOKEN_PATH, "w") as f:
        f.write(creds.to_json())

    print(f"\nSaved token to {TOKEN_PATH}. Done — (re)start the stack: docker compose up -d")


if __name__ == "__main__":
    main()
