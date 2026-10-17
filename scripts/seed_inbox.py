#!/usr/bin/env python3
"""Seed the demo Gmail mailbox with fixture emails.

Usage:
  uv run python scripts/seed_inbox.py --fixture a   # single fixture
  uv run python scripts/seed_inbox.py --all         # all fixtures

Fixtures: a (Hormuz diversion), b (ETA slip), c (Portuguese diversion),
          d (Red Sea advisory), e (noise invoice)
"""

import argparse
import base64
import os
import sys
from email.mime.text import MIMEText
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "emails"
FIXTURES = {
    "a": "a_diversion_hormuz.txt",
    "b": "b_eta_slip.txt",
    "c": "c_diversion_portuguese.txt",
    "d": "d_redsea_advisory.txt",
    "e": "e_noise_invoice.txt",
}


def build_service():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials(
        token=None,
        refresh_token=os.environ["GMAIL_REFRESH_TOKEN"],
        client_id=os.environ["GOOGLE_OAUTH_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_OAUTH_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
    )
    return build("gmail", "v1", credentials=creds)


def send_fixture(service, fixture_key: str) -> None:
    fname = FIXTURES[fixture_key]
    path = FIXTURES_DIR / fname
    if not path.exists():
        print(f"ERROR: fixture {path} not found")
        return

    raw_text = path.read_text(encoding="utf-8")
    lines = raw_text.split("\n")

    # Parse From / Subject headers from fixture
    subject = next((l.replace("Subject:", "").strip() for l in lines if l.startswith("Subject:")), f"Fixture {fixture_key}")
    from_addr = next((l.replace("From:", "").strip() for l in lines if l.startswith("From:")), "seed@example.com")

    to_addr = os.environ.get("GMAIL_MONITOR_ADDRESS", os.environ.get("DEMO_SINK_EMAIL", ""))
    if not to_addr:
        print("ERROR: GMAIL_MONITOR_ADDRESS not set")
        return

    msg = MIMEText(raw_text, "plain", "utf-8")
    msg["to"] = to_addr
    msg["from"] = from_addr
    msg["subject"] = f"[DEMO] {subject}"

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    # Use insert() instead of send() so the message lands directly in INBOX as UNREAD.
    # messages.send() to self can skip INBOX/UNREAD labels in Gmail.
    result = service.users().messages().insert(
        userId="me",
        body={"raw": raw, "labelIds": ["INBOX", "UNREAD"]},
    ).execute()
    print(f"  Inserted fixture '{fixture_key}' ({fname}) → message ID: {result['id']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed demo Gmail inbox with fixture emails")
    parser.add_argument("--fixture", choices=list(FIXTURES.keys()), help="Single fixture to send")
    parser.add_argument("--all", action="store_true", help="Send all fixtures")
    args = parser.parse_args()

    if not args.fixture and not args.all:
        parser.print_help()
        sys.exit(1)

    if not os.environ.get("GMAIL_REFRESH_TOKEN"):
        print("SKIP: GMAIL_REFRESH_TOKEN not set — run scripts/gmail_auth.py first")
        sys.exit(0)

    service = build_service()
    keys = list(FIXTURES.keys()) if args.all else [args.fixture]
    for k in keys:
        send_fixture(service, k)
    print("Done.")


if __name__ == "__main__":
    main()
