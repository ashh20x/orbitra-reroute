#!/usr/bin/env python3
"""Run the Gmail OAuth flow once and print the refresh token.

Usage:
  1. Set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET in .env
  2. uv run python scripts/gmail_auth.py
  3. Copy the printed GMAIL_REFRESH_TOKEN into .env

Scopes: gmail.modify, gmail.compose
"""

import os

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.compose",
]


def main() -> None:
    client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")
    client_secret = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        print("ERROR: GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET must be set in .env")
        return

    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_config(
        {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"],
            }
        },
        scopes=SCOPES,
    )
    creds = flow.run_local_server(port=0)
    print("\n=== Add these to your .env ===")
    print(f"GMAIL_REFRESH_TOKEN={creds.refresh_token}")
    print("==============================\n")


if __name__ == "__main__":
    main()
