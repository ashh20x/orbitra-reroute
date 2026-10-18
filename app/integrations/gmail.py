"""Gmail integration — polling, draft creation, send on approval.

Scopes: gmail.modify, gmail.compose.
All outbound recipients are rewritten to DEMO_SINK_EMAIL when DEMO_MODE=true.
"""

from __future__ import annotations

import asyncio
import base64
import os
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

_DEMO_MODE = os.environ.get("DEMO_MODE", "true").lower() == "true"
_DEMO_SINK = os.environ.get("DEMO_SINK_EMAIL", "")
_MONITOR   = os.environ.get("GMAIL_MONITOR_ADDRESS", "")


def _get_service() -> Any:
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


def _extract_body(payload: dict) -> str:
    """Recursively extract plain-text body from a Gmail message payload."""
    mime = payload.get("mimeType", "")
    if mime == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
    for part in payload.get("parts", []):
        result = _extract_body(part)
        if result:
            return result
    return ""


def _header(headers: list[dict], name: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


async def get_authenticated_user() -> dict[str, Any]:
    """Return the email address of the authenticated Gmail account."""
    service = _get_service()

    def _fetch() -> dict:
        return service.users().getProfile(userId="me").execute()

    result = await asyncio.get_event_loop().run_in_executor(None, _fetch)
    return {"email": result.get("emailAddress"), "messages_total": result.get("messagesTotal")}


async def list_recent_messages(max_results: int = 10, label_ids: list[str] | None = None) -> list[dict]:
    """List recent messages across all labels (or filtered by label_ids)."""
    service = _get_service()

    def _fetch() -> list[dict]:
        kwargs: dict = {"userId": "me", "maxResults": max_results}
        if label_ids:
            kwargs["labelIds"] = label_ids
        res = service.users().messages().list(**kwargs).execute()
        msgs = res.get("messages", [])
        result = []
        for m in msgs[:5]:
            try:
                full = service.users().messages().get(userId="me", id=m["id"], format="metadata",
                    metadataHeaders=["Subject", "From", "To"]).execute()
                headers = {h["name"].lower(): h["value"] for h in full.get("payload", {}).get("headers", [])}
                result.append({
                    "id": m["id"],
                    "labels": full.get("labelIds", []),
                    "subject": headers.get("subject", ""),
                    "from": headers.get("from", ""),
                })
            except Exception:
                result.append({"id": m["id"]})
        return result

    return await asyncio.get_event_loop().run_in_executor(None, _fetch)


async def poll_inbox(max_results: int = 20) -> list[dict[str, Any]]:
    """Return INBOX message stubs (id + threadId only).

    We no longer filter by UNREAD because Gmail does not mark replies to
    agent-initiated threads as unread — the reply arrives in INBOX but
    without the UNREAD label.  Deduplication in process_email() and
    archiving via mark_read() prevent double-processing.
    """
    service = _get_service()

    def _fetch() -> list[dict]:
        res = service.users().messages().list(
            userId="me",
            labelIds=["INBOX"],
            maxResults=max_results,
        ).execute()
        return res.get("messages", [])

    return await asyncio.get_event_loop().run_in_executor(None, _fetch)


async def get_message(message_id: str) -> dict[str, Any]:
    """Fetch a single message and return a parsed dict."""
    service = _get_service()

    def _fetch() -> dict:
        return service.users().messages().get(
            userId="me", id=message_id, format="full"
        ).execute()

    raw = await asyncio.get_event_loop().run_in_executor(None, _fetch)
    headers = raw.get("payload", {}).get("headers", [])

    return {
        "id": raw["id"],
        "thread_id": raw.get("threadId"),
        "subject": _header(headers, "subject"),
        "from": _header(headers, "from"),
        "to": _header(headers, "to"),
        "date": _header(headers, "date"),
        "in_reply_to": _header(headers, "in-reply-to"),
        "body": _extract_body(raw.get("payload", {})),
        "label_ids": raw.get("labelIds", []),
        "snippet": raw.get("snippet", ""),
    }


async def create_draft(
    to: list[str],
    subject: str,
    body_html: str,
    attachments: list[tuple[str, bytes, str]] | None = None,
) -> str:
    """Create a Gmail draft and return the draft_id.

    In DEMO_MODE all recipients are replaced with DEMO_SINK_EMAIL and
    the subject is prefixed with [DEMO].
    """
    service = _get_service()
    recipients  = [_DEMO_SINK] if (_DEMO_MODE and _DEMO_SINK) else to
    final_subj  = f"[DEMO] {subject}" if _DEMO_MODE else subject

    msg = MIMEMultipart("mixed")
    msg["to"]      = ", ".join(recipients)
    msg["from"]    = _MONITOR
    msg["subject"] = final_subj

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(body_html, "html", "utf-8"))
    msg.attach(alt)

    for filename, data, _mime in (attachments or []):
        part = MIMEApplication(data)
        part.add_header("Content-Disposition", "attachment", filename=filename)
        msg.attach(part)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

    def _create() -> dict:
        return service.users().drafts().create(
            userId="me", body={"message": {"raw": raw}}
        ).execute()

    result = await asyncio.get_event_loop().run_in_executor(None, _create)
    return result["id"]


async def send_draft(draft_id: str) -> None:
    """Send an existing draft (called after human approval)."""
    service = _get_service()

    def _send() -> None:
        service.users().drafts().send(
            userId="me", body={"id": draft_id}
        ).execute()

    await asyncio.get_event_loop().run_in_executor(None, _send)


async def delete_draft(draft_id: str) -> None:
    """Delete a draft (called on rejection)."""
    service = _get_service()

    def _delete() -> None:
        service.users().drafts().delete(userId="me", id=draft_id).execute()

    await asyncio.get_event_loop().run_in_executor(None, _delete)


async def send_message(
    to: list[str],
    subject: str,
    body_html: str,
) -> None:
    """Send an email immediately (no draft — used for approval-request notifications)."""
    service = _get_service()
    recipients = [_DEMO_SINK] if (_DEMO_MODE and _DEMO_SINK) else to
    final_subj = f"[DEMO] {subject}" if _DEMO_MODE else subject

    msg = MIMEMultipart("alternative")
    msg["to"] = ", ".join(recipients)
    msg["from"] = _MONITOR
    msg["subject"] = final_subj
    msg.attach(MIMEText(body_html, "html", "utf-8"))

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

    def _send() -> None:
        service.users().messages().send(
            userId="me", body={"raw": raw}
        ).execute()

    await asyncio.get_event_loop().run_in_executor(None, _send)


async def mark_read(message_id: str) -> None:
    """Archive a processed message — removes INBOX and UNREAD labels.

    Archiving (removing INBOX) ensures the message won't be returned by
    subsequent polls even when Gmail omits the UNREAD label (e.g. for
    replies to agent-initiated threads).
    """
    service = _get_service()

    def _modify() -> None:
        service.users().messages().modify(
            userId="me",
            id=message_id,
            body={"removeLabelIds": ["INBOX", "UNREAD"]},
        ).execute()

    await asyncio.get_event_loop().run_in_executor(None, _modify)


async def label_message(
    message_id: str,
    add_labels: list[str],
    remove_labels: list[str] | None = None,
) -> None:
    """Add/remove labels on a message."""
    service = _get_service()

    def _modify() -> None:
        service.users().messages().modify(
            userId="me",
            id=message_id,
            body={
                "addLabelIds": add_labels,
                "removeLabelIds": remove_labels or [],
            },
        ).execute()

    await asyncio.get_event_loop().run_in_executor(None, _modify)
