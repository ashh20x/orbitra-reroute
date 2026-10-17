#!/usr/bin/env python3
"""Smoke test — Gmail API.

Checks:
1. List labels on the demo mailbox
2. Create a draft addressed to DEMO_SINK_EMAIL
3. Delete the draft

Run: uv run python scripts/smoke_gmail.py
Skips cleanly if GMAIL_REFRESH_TOKEN or GOOGLE_OAUTH_CLIENT_ID not set.
"""

import os
import sys

from rich.console import Console

console = Console()

REFRESH_TOKEN = os.environ.get("GMAIL_REFRESH_TOKEN", "")
CLIENT_ID = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "")
DEMO_SINK = os.environ.get("DEMO_SINK_EMAIL", "")


def skip(reason: str) -> None:
    console.print(f"[yellow]SKIP:[/yellow] {reason}")
    sys.exit(0)


def main() -> None:
    if not REFRESH_TOKEN or not CLIENT_ID:
        skip("GMAIL_REFRESH_TOKEN or GOOGLE_OAUTH_CLIENT_ID not set — run scripts/gmail_auth.py first")
    if not DEMO_SINK:
        skip("DEMO_SINK_EMAIL not set")

    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    import base64
    from email.mime.text import MIMEText

    creds = Credentials(
        token=None,
        refresh_token=REFRESH_TOKEN,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        token_uri="https://oauth2.googleapis.com/token",
    )
    service = build("gmail", "v1", credentials=creds)

    # 1. List labels
    console.print("\n[bold cyan]1. List Gmail labels[/bold cyan]")
    labels = service.users().labels().list(userId="me").execute().get("labels", [])
    from rich.table import Table
    table = Table("Label ID", "Name", title="Gmail Labels")
    for lbl in labels[:15]:
        table.add_row(lbl["id"], lbl["name"])
    console.print(table)
    console.print(f"[green]OK:[/green] {len(labels)} labels found.")

    # 2. Create draft
    console.print(f"\n[bold cyan]2. Create draft → {DEMO_SINK}[/bold cyan]")
    msg = MIMEText("[DEMO] Orbitra Reroute smoke test — please ignore", "plain")
    msg["to"] = DEMO_SINK
    msg["subject"] = "[DEMO] Smoke test draft"
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    draft = service.users().drafts().create(
        userId="me", body={"message": {"raw": raw}}
    ).execute()
    draft_id = draft["id"]
    console.print(f"[green]OK:[/green] Draft created: {draft_id}")

    # 3. Delete draft
    console.print("\n[bold cyan]3. Delete draft[/bold cyan]")
    service.users().drafts().delete(userId="me", id=draft_id).execute()
    console.print(f"[green]OK:[/green] Draft {draft_id} deleted.")

    console.print("\n[bold green]Gmail smoke PASSED[/bold green]")


if __name__ == "__main__":
    main()
