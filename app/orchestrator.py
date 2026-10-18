"""Pre-re-route process state machine.

Steps 1–8 from KICKOFF.md §1. Each step is a transition between ProcessState
values. The Trade Ops approval gate is an async wait backed by the DB —
the orchestrator creates the draft and returns; the /approvals endpoint
handles the approve/reject action.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid

from app.domain.cdm import (
    DisruptionSignal,
    DraftType,
    ProcessState,
    RiskLevel,
    Shipment,
)

logger = logging.getLogger(__name__)

_LEVEL_ORDER = [RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL]


def _threshold() -> RiskLevel:
    t = os.environ.get("RISK_THRESHOLD", "Medium")
    return RiskLevel(t)


def _level_gte(level: RiskLevel, threshold: RiskLevel) -> bool:
    return _LEVEL_ORDER.index(level) >= _LEVEL_ORDER.index(threshold)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _save_state(shipment_id: str, state: str, **kwargs: object) -> None:
    from app.store import ShipmentRow, get_session
    from sqlmodel import select

    with get_session() as session:
        row = session.exec(
            select(ShipmentRow).where(ShipmentRow.id == shipment_id)
        ).first()
        if row:
            row.state = state
            for k, v in kwargs.items():
                if hasattr(row, k):
                    setattr(row, k, v)
            session.add(row)
            session.commit()


def _audit(
    shipment_id: str | None,
    event_type: str,
    details: dict,
    model_used: str | None = None,
    latency_ms: int | None = None,
    evolus_call: bool = False,
) -> None:
    from app.store import AuditRow, get_session

    with get_session() as session:
        row = AuditRow(
            id=str(uuid.uuid4()),
            shipment_id=shipment_id,
            event_type=event_type,
            actor="system",
            model_used=model_used,
            latency_ms=latency_ms,
            evolus_call=evolus_call,
            details_json=json.dumps(details, default=str),
        )
        session.add(row)
        session.commit()


# ---------------------------------------------------------------------------
# Extractor helpers
# ---------------------------------------------------------------------------


def _parse_extractor_output(shipment: Shipment, data: dict) -> DisruptionSignal:
    """Map Evolus extractor JSON → DisruptionSignal and enrich Shipment in-place."""
    if not shipment.vessel_name:
        shipment.vessel_name = (
            data.get("vessel_name") or data.get("vessel")
        )
    if not shipment.bill_of_lading:
        shipment.bill_of_lading = (
            data.get("bill_of_lading") or data.get("bl_number") or data.get("bol")
        )
    if not shipment.carrier:
        shipment.carrier = data.get("carrier")
    if shipment.eta_slip_days is None:
        for key in ("estimated_delay_days", "delay_days", "eta_slip_days"):
            if data.get(key) is not None:
                try:
                    shipment.eta_slip_days = int(data[key])
                except (ValueError, TypeError):
                    pass
                break

    chokepoint = data.get("chokepoint") or data.get("chokepoint_mentioned")

    diversion_lang = data.get("diversion_language") or []
    if isinstance(diversion_lang, str):
        diversion_lang = [diversion_lang]

    severity_kws = data.get("severity_keywords") or []
    if isinstance(severity_kws, str):
        severity_kws = [severity_kws]

    return DisruptionSignal(
        shipment_id=shipment.id,
        signal_type=data.get("signal_type", "diversion"),
        chokepoint_mentioned=chokepoint,
        diversion_language=list(diversion_lang),
        carrier_advisory_present=bool(data.get("carrier_advisory_present", False)),
        severity_keywords=list(severity_kws),
    )


def _keyword_signal(shipment: Shipment) -> DisruptionSignal:
    """Fallback: enrich Shipment and build DisruptionSignal by scanning the email body."""
    raw = shipment.raw_email_body or ""
    body = raw.lower()

    # Extract vessel name, BL, carrier from structured email fields
    for pattern, attr in [
        (r"(?:vessel|vessel name|navio)[:\s]+(MV [A-Z][A-Z ]{1,35})", "vessel_name"),
        (r"(?:bill of lading|b/?l)[:\s#]+([A-Z0-9]{6,20})", "bill_of_lading"),
        (r"carrier[:\s]+([^\n,]+)", "carrier"),
    ]:
        if not getattr(shipment, attr):
            m = re.search(pattern, raw, re.IGNORECASE)
            if m:
                setattr(shipment, attr, m.group(1).strip())

    # Fallback: find first "MV ALLCAPS" in the body
    if not shipment.vessel_name:
        m = re.search(r"\b(MV [A-Z][A-Z ]{2,30})\b", raw)
        if m:
            shipment.vessel_name = m.group(1).strip()

    # Extract container count
    if not shipment.container_count:
        m = re.search(r"(\d+)\s*x\s*(?:40ft|20ft|reefer)", raw, re.IGNORECASE)
        if m:
            shipment.container_count = int(m.group(1))

    chokepoint: str | None = None
    for cp in ("strait of hormuz", "hormuz", "bab-el-mandeb", "red sea", "suez"):
        if cp in body:
            chokepoint = cp.title()
            break

    signal_type = "eta_slip"
    if any(k in body for k in ("diversion", "diverted", "reroute", "alternative port")):
        signal_type = "diversion"
    elif any(k in body for k in ("security advisory", "threat", "attack")):
        signal_type = "security_advisory"

    if shipment.eta_slip_days is None:
        match = re.search(r"(\d+)[–\-–](\d+)\s*day", raw)
        if match:
            shipment.eta_slip_days = (int(match.group(1)) + int(match.group(2))) // 2

    return DisruptionSignal(
        shipment_id=shipment.id,
        signal_type=signal_type,
        chokepoint_mentioned=chokepoint,
        diversion_language=[w for w in ("diversion", "reroute", "alternative") if w in body],
        carrier_advisory_present="advisory" in body,
        severity_keywords=[
            w for w in ("critical", "urgent", "immediate", "suspended") if w in body
        ],
    )


# ---------------------------------------------------------------------------
# Notification HTML
# ---------------------------------------------------------------------------


def _notification_html(
    shipment: Shipment,
    assessment: object,  # RiskAssessment
    workbook: object,    # Workbook
    cost: object | None, # CostForecast | None
    spatial: object | None,  # SpatialAsset | None
    base_url: str,
) -> str:
    level_colors = {
        RiskLevel.LOW: "#2CECC0",
        RiskLevel.MEDIUM: "#FAC850",
        RiskLevel.HIGH: "#FA643C",
        RiskLevel.CRITICAL: "#DC3232",
    }
    lc = level_colors.get(assessment.final_level, "#E8F0F0")  # type: ignore[attr-defined]

    cost_html = ""
    if cost:
        placeholder_note = (
            '<p style="font-size:11px;color:#666;margin:8px 0 0;font-style:italic;">'
            "Estimate pending actual quotes — assumptions contain PLACEHOLDER values.</p>"
            if cost.assumptions_have_placeholders  # type: ignore[attr-defined]
            else ""
        )
        cost_html = f"""
<div style="background:#0d1d20;border:1px solid #2C838C;border-radius:6px;padding:16px 20px;margin:16px 0;">
  <h3 style="color:#2CECC0;margin:0 0 12px;font-size:13px;text-transform:uppercase;letter-spacing:.05em;">Cost Forecast</h3>
  <table style="width:100%;border-collapse:collapse;font-size:13px;">
    <tr><td style="padding:4px 12px 4px 0;color:#888;">Optimistic</td><td style="font-weight:600;color:#2CECC0;">${cost.p10_usd:,.0f}</td></tr>
    <tr><td style="padding:4px 12px 4px 0;color:#888;">Expected</td><td style="font-weight:600;color:#E8F0F0;">${cost.p50_usd:,.0f}</td></tr>
    <tr><td style="padding:4px 12px 4px 0;color:#888;">Conservative</td><td style="font-weight:600;color:#FA643C;">${cost.p90_usd:,.0f}</td></tr>
    <tr><td style="padding:4px 12px 4px 0;color:#888;">Per container (expected)</td><td style="font-weight:600;">${cost.per_container_p50_usd:,.0f}</td></tr>
  </table>
  {placeholder_note}
</div>"""

    spatial_html = ""
    if spatial and spatial.marble_url:  # type: ignore[attr-defined]
        spatial_html = f"""
<div style="margin:16px 0;">
  <p style="color:#888;font-size:11px;margin:0 0 6px;">Spatial overview (AI-generated)</p>
  <a href="{spatial.marble_url}" style="color:#2CECC0;font-size:12px;">{spatial.title} →</a>
  <p style="color:#666;font-size:10px;margin:4px 0 0;font-style:italic;">{spatial.label}</p>
</div>"""

    secondary = ", ".join(getattr(assessment, "secondary_ports", []))
    cold_chain = getattr(assessment, "cold_chain_implications", None)
    justification = getattr(assessment, "llm_justification", None) or "Risk assessment complete."
    wb_count = len(
        getattr(workbook, "jeddah_providers", []) + getattr(workbook, "salalah_providers", [])
    )
    demo_sink = os.environ.get("DEMO_SINK_EMAIL", "—")

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="background:#071216;color:#E8F0F0;font-family:'Inter',Arial,sans-serif;font-size:14px;padding:0;margin:0;">
<div style="max-width:640px;margin:0 auto;padding:32px 24px;">

  <div style="border-bottom:2px solid #2C838C;padding-bottom:16px;margin-bottom:24px;">
    <span style="font-size:11px;color:#2C838C;text-transform:uppercase;letter-spacing:.08em;">Orbitra Reroute · Pre-Route Alert</span>
    <h1 style="color:#E8F0F0;font-size:20px;margin:8px 0 0;font-weight:600;">{shipment.vessel_name or 'Unknown Vessel'} — Action Required</h1>
  </div>

  <div style="background:#0d1d20;border-left:4px solid {lc};border-radius:0 6px 6px 0;padding:14px 18px;margin:0 0 20px;">
    <span style="background:{lc};color:#071216;font-weight:700;font-size:11px;padding:2px 10px;border-radius:12px;text-transform:uppercase;">{assessment.final_level.value}</span>
    <h2 style="color:#E8F0F0;margin:10px 0 4px;font-size:16px;">{assessment.chokepoint_affected or 'Disruption'} — Reroute Recommended</h2>
    <p style="color:#aaa;margin:0;font-size:13px;">{justification}</p>
  </div>

  <table style="width:100%;border-collapse:collapse;font-size:13px;margin-bottom:20px;">
    <tr><td style="padding:6px 12px 6px 0;color:#888;">Vessel</td><td style="font-weight:500;">{shipment.vessel_name or '—'}</td></tr>
    <tr><td style="padding:6px 12px 6px 0;color:#888;">B/L Number</td><td>{shipment.bill_of_lading or '—'}</td></tr>
    <tr><td style="padding:6px 12px 6px 0;color:#888;">Carrier</td><td>{shipment.carrier or '—'}</td></tr>
    <tr><td style="padding:6px 12px 6px 0;color:#888;">ETA Slip</td><td>{f"{shipment.eta_slip_days} days" if shipment.eta_slip_days else "TBD"}</td></tr>
    <tr><td style="padding:6px 12px 6px 0;color:#888;">Recommended Port</td><td style="font-weight:600;color:#2CECC0;">{assessment.recommended_port or 'TBD'}</td></tr>
    {"<tr><td style='padding:6px 12px 6px 0;color:#888;'>Secondary Options</td><td>" + secondary + "</td></tr>" if secondary else ""}
    {"<tr><td style='padding:6px 12px 6px 0;color:#888;'>Cold Chain</td><td style='color:#FAC850;'>" + cold_chain + "</td></tr>" if cold_chain else ""}
  </table>

  {cost_html}
  {spatial_html}

  <div style="background:#0d1d20;border:1px solid #2C838C;border-radius:6px;padding:16px 20px;margin:20px 0;text-align:center;">
    <p style="color:#888;font-size:12px;margin:0 0 12px;">A draft notification is ready for your review.</p>
    <a href="{base_url}/approvals" style="background:#2C838C;color:#E8F0F0;padding:10px 24px;border-radius:6px;text-decoration:none;font-weight:600;font-size:14px;display:inline-block;">Review &amp; Approve in Web UI →</a>
  </div>

  <div style="background:#071216;border:1px solid rgba(44,131,140,0.4);border-radius:6px;padding:14px 18px;margin:12px 0;">
    <p style="color:#2CECC0;font-weight:600;font-size:13px;margin:0 0 6px;">Or reply to this email:</p>
    <p style="color:#aaa;font-size:12px;margin:0;">
      Reply <strong style="color:#2CECC0;font-size:14px;">APPROVE</strong> to send the reroute outreach to providers, Finance, and Compliance.<br>
      Reply <strong style="color:#FA643C;font-size:14px;">REJECT</strong> to cancel this reroute recommendation.
    </p>
  </div>

  <p style="font-size:11px;color:#666;text-align:center;margin-top:8px;">Workbook attached — {wb_count} providers qualified.</p>

  <hr style="border:none;border-top:1px solid #1a2e32;margin:24px 0;">
  <p style="font-size:11px;color:#555;text-align:center;">
    Generated by Orbitra Reroute AI Agent · AMD Hackathon Act III<br>
    <span style="color:#2C838C;">Powered by AMD Token Factory + Evolus Business Agent</span><br>
    DEMO MODE — all outbound email routed to {demo_sink}
  </p>
</div>
</body></html>"""


# ---------------------------------------------------------------------------
# Approval / rejection helpers (shared by web UI and email reply handler)
# ---------------------------------------------------------------------------


async def _execute_approval(draft_id: str, approver: str = "trade.ops") -> str:
    """Send the notification draft and fire the post-approval pipeline.

    Returns "sent" or raises on failure.
    """
    import asyncio as _asyncio

    from app.store import DraftRow, ShipmentRow, get_session
    from sqlmodel import select

    draft_shipment_id: str | None = None
    draft_type: str | None = None
    with get_session() as session:
        draft = session.exec(select(DraftRow).where(DraftRow.id == draft_id)).first()
        if not draft:
            raise ValueError(f"Draft {draft_id} not found")
        if draft.status != "pending":
            return draft.status
        gmail_draft_id = draft.gmail_draft_id
        draft_shipment_id = draft.shipment_id
        draft_type = draft.draft_type

    if not gmail_draft_id:
        raise ValueError("No Gmail draft ID on record")

    from app.integrations.gmail import send_draft
    await send_draft(gmail_draft_id)

    shipment_id_for_pipeline: str | None = None

    with get_session() as session:
        draft = session.exec(select(DraftRow).where(DraftRow.id == draft_id)).first()
        if draft:
            draft.status = "sent"
            session.add(draft)
        ship = session.exec(
            select(ShipmentRow).where(ShipmentRow.id == draft_shipment_id)
        ).first()
        if ship:
            if draft_type == DraftType.TRADE_OPS_NOTIFICATION.value:
                ship.state = "trade_ops_approved"
                shipment_id_for_pipeline = ship.id
            else:
                ship.state = "completed"
            session.add(ship)
        session.commit()

    event = "trade_ops_approved" if draft_type == DraftType.TRADE_OPS_NOTIFICATION.value else f"{draft_type}_approved"
    _audit(draft_shipment_id, event, {"draft_id": draft_id, "approver": approver})

    if shipment_id_for_pipeline:
        with get_session() as _s:
            shipment_row = _s.exec(
                select(ShipmentRow).where(ShipmentRow.id == shipment_id_for_pipeline)
            ).first()
            # Capture all needed fields before session closes
            if shipment_row:
                from types import SimpleNamespace
                snap = SimpleNamespace(
                    id=shipment_row.id,
                    vessel_name=shipment_row.vessel_name,
                    bill_of_lading=shipment_row.bill_of_lading,
                    recommended_port=shipment_row.recommended_port,
                    risk_level=shipment_row.risk_level,
                    received_at=shipment_row.received_at,
                )
        if shipment_row:
            _asyncio.create_task(post_approval_pipeline(snap))

    return "sent"


async def _execute_rejection(draft_id: str, approver: str = "trade.ops") -> str:
    """Delete the notification draft and mark the shipment rejected.

    Returns "rejected" or raises on failure.
    """
    from app.store import DraftRow, ShipmentRow, get_session
    from sqlmodel import select

    with get_session() as session:
        draft = session.exec(select(DraftRow).where(DraftRow.id == draft_id)).first()
        if not draft:
            raise ValueError(f"Draft {draft_id} not found")
        if draft.status != "pending":
            return draft.status
        gmail_draft_id = draft.gmail_draft_id
        shipment_id = draft.shipment_id

    if gmail_draft_id:
        try:
            from app.integrations.gmail import delete_draft
            await delete_draft(gmail_draft_id)
        except Exception as exc:
            logger.warning("delete_draft failed (non-fatal): %s", exc)

    with get_session() as session:
        draft = session.exec(select(DraftRow).where(DraftRow.id == draft_id)).first()
        if draft:
            draft.status = "rejected"
            session.add(draft)
        ship = session.exec(
            select(ShipmentRow).where(ShipmentRow.id == shipment_id)
        ).first()
        if ship:
            ship.state = "trade_ops_rejected"
            session.add(ship)
        session.commit()

    _audit(
        shipment_id,
        "trade_ops_rejected",
        {"draft_id": draft_id, "approver": approver},
    )
    return "rejected"


async def _send_approval_request(
    to_email: str,
    subject: str,
    draft_type_label: str,
    vessel_name: str,
    recommended_port: str,
    extra_rows: list[tuple[str, str]] | None = None,
) -> None:
    """Send an email to Trade Ops asking them to reply APPROVE or REJECT for a pending draft."""
    extra_html = ""
    for label, value in (extra_rows or []):
        extra_html += f'<tr><td style="color:#888;padding:3px 14px 3px 0;">{label}</td><td style="color:#E8F0F0;font-weight:600;">{value}</td></tr>'

    body_html = f"""<!DOCTYPE html><html><body style="background:#071216;color:#E8F0F0;font-family:Arial,sans-serif;padding:24px;max-width:600px;">
  <div style="border-bottom:2px solid #2C838C;padding-bottom:10px;margin-bottom:16px;">
    <span style="font-size:11px;color:#2C838C;text-transform:uppercase;letter-spacing:.08em;">Orbitra Reroute · AI Agent Draft</span>
  </div>
  <p style="color:#2CECC0;font-weight:600;font-size:15px;margin:0 0 12px;">Draft ready for approval — {draft_type_label}</p>
  <table style="font-size:13px;border-collapse:collapse;margin-bottom:16px;width:100%;">
    <tr><td style="color:#888;padding:4px 16px 4px 0;white-space:nowrap;">Vessel</td><td style="color:#E8F0F0;font-weight:600;">{vessel_name}</td></tr>
    <tr><td style="color:#888;padding:4px 16px 4px 0;white-space:nowrap;">Diversion Port</td><td style="color:#2CECC0;font-weight:600;">{recommended_port}</td></tr>
    {extra_html}
  </table>
  <div style="background:#0d1d20;border:1px solid rgba(44,131,140,0.4);border-radius:6px;padding:14px 18px;margin:12px 0;">
    <p style="color:#2CECC0;font-weight:600;font-size:13px;margin:0 0 8px;">Action required — reply to this email:</p>
    <p style="color:#ccc;font-size:13px;margin:0 0 4px;">
      Reply <strong style="color:#2CECC0;font-size:15px;">APPROVE</strong> to send this draft to the recipient.
    </p>
    <p style="color:#ccc;font-size:13px;margin:0;">
      Reply <strong style="color:#FA643C;font-size:15px;">REJECT</strong> to discard it.
    </p>
  </div>
  <p style="font-size:11px;color:#555;margin-top:16px;">Orbitra Reroute AI Agent · AMD Hackathon Act III<br>Powered by AMD Token Factory + Evolus</p>
</body></html>"""
    try:
        from app.integrations.gmail import send_message
        await send_message(to=[to_email], subject=subject, body_html=body_html)
        logger.info("Approval-request notification sent to %s: %s", to_email, subject)
        _audit(None, "approval_request_sent", {"to": to_email, "subject": subject, "draft_type": draft_type_label})
    except Exception as exc:
        logger.error("Failed to send approval-request notification to %s: %s", to_email, exc)
        _audit(None, "approval_request_failed", {"to": to_email, "error": str(exc), "subject": subject})


async def _handle_approval_reply(message: dict) -> str | None:
    """If the message is a reply to a pending notification draft, process it.

    Returns the resulting state string, or None if not a reply we handle.
    """
    subject = message.get("subject", "")
    if not re.match(r"re:\s+", subject, re.IGNORECASE):
        return None

    # Strip "Re: " then "[DEMO] " so the subject matches DraftRow.subject exactly
    bare_subject = re.sub(r"^re:\s*", "", subject, flags=re.IGNORECASE).strip()
    bare_subject = re.sub(r"^\[DEMO\]\s*", "", bare_subject, flags=re.IGNORECASE).strip()

    from app.store import DraftRow, get_session
    from sqlmodel import select

    with get_session() as session:
        draft = session.exec(
            select(DraftRow)
            .where(DraftRow.subject == bare_subject)
            .where(DraftRow.status == "pending")
            .order_by(DraftRow.created_at.desc())
        ).first()

    if not draft:
        return None

    body_upper = message.get("body", "").upper()
    sender = message.get("from", "trade.ops@email")

    draft_type = draft.draft_type

    if "APPROVE" in body_upper:
        result = await _execute_approval(draft.id, approver=sender)
        _audit(draft.shipment_id, "reply_approved", {"from": sender, "draft_type": draft_type, "result": result})
        logger.info("Reply approval: draft=%s type=%s approver=%s", draft.id, draft_type, sender)
        return "trade_ops_approved" if draft_type == "trade_ops_notification" else f"{draft_type}_sent"
    elif "REJECT" in body_upper:
        result = await _execute_rejection(draft.id, approver=sender)
        _audit(draft.shipment_id, "reply_rejected", {"from": sender, "draft_type": draft_type, "result": result})
        logger.info("Reply rejection: draft=%s type=%s approver=%s", draft.id, draft_type, sender)
        return "trade_ops_rejected" if draft_type == "trade_ops_notification" else f"{draft_type}_rejected"
    else:
        _audit(
            draft.shipment_id,
            "reply_ambiguous",
            {"from": sender, "draft_type": draft_type, "snippet": message.get("body", "")[:120]},
        )
        logger.warning("Ambiguous reply to draft %s (type=%s) from %s", draft.id, draft_type, sender)
        return None


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


async def process_email(message: dict) -> str:
    """Process one Gmail message dict through the full pipeline.

    Returns the final ProcessState value.
    """
    from app.store import ShipmentRow, get_session

    message_id = message.get("id", "")
    body = message.get("body", "")
    subject = message.get("subject", "")

    # ── Early exit: deduplication — skip if already processed this message ID ──
    if message_id:
        from app.store import ShipmentRow, get_session
        from sqlmodel import select as _select
        with get_session() as _ds:
            existing = _ds.exec(
                _select(ShipmentRow).where(ShipmentRow.gmail_message_id == message_id)
            ).first()
        if existing:
            logger.debug("Skipping already-processed message %s (shipment %s)", message_id, existing.id[:8])
            return existing.state

    # ── Early exit: skip emails sent BY this agent (approval-request notifications) ──
    monitor = os.environ.get("GMAIL_MONITOR_ADDRESS", "")
    sender_addr = message.get("from", "")
    if monitor and monitor.lower() in sender_addr.lower() and not subject.lower().startswith("re:"):
        logger.debug("Skipping self-sent email: %s", subject)
        return "self_sent_skipped"

    # ── Early exit: reply-based approval/rejection ─────────────────────────
    # Any "Re:" email is treated as an approval reply attempt.  If no
    # matching pending draft exists we skip it entirely — a stale reply
    # should never kick off a fresh disruption pipeline.
    if re.match(r"re:\s*", subject, re.IGNORECASE):
        reply_result = await _handle_approval_reply(message)
        if reply_result is not None:
            return reply_result
        logger.info("Re: email with no matching pending draft — skipping: %s", subject)
        return "reply_no_matching_draft"

    reply_result = await _handle_approval_reply(message)
    if reply_result is not None:
        return reply_result

    shipment = Shipment(
        id=str(uuid.uuid4()),
        gmail_message_id=message_id,
        raw_email_subject=subject,
        raw_email_body=body,
    )

    # Detect Portuguese body and translate (P3 path)
    source_language = "en"
    pt_markers = ("informamos", "desvio", "aviso formal", "estreito")
    if any(kw in body.lower() for kw in pt_markers):
        source_language = "pt"
        try:
            from app.integrations.evolus import run_ai_tool

            translated = await run_ai_tool("translate", body, options={"target": "en"})
            # Normalize escaped newlines that some LLMs return as literal \n
            body = translated.replace("\\n", "\n")
            shipment.raw_email_body = body
            _audit(
                shipment.id, "email_translated",
                {"from": "pt", "to": "en"},
                evolus_call=True,
            )
        except Exception as exc:
            logger.warning("Translation failed (non-fatal): %s", exc)
    shipment.source_language = source_language

    # Persist initial record — unique constraint on gmail_message_id prevents
    # duplicate processing if APScheduler and manual trigger race each other.
    try:
        with get_session() as session:
            row = ShipmentRow(
                id=shipment.id,
                gmail_message_id=message_id,
                state=ProcessState.RECEIVED.value,
            )
            session.add(row)
            session.commit()
    except Exception as exc:
        if "UNIQUE" in str(exc).upper():
            logger.warning("Duplicate message_id %s — skipping (race condition)", message_id)
            return "duplicate_skipped"
        raise

    _audit(shipment.id, "email_received", {
        "subject": subject,
        "message_id": message_id,
        "source_language": source_language,
    })

    # Extract structured data via Evolus extractor
    _save_state(shipment.id, ProcessState.EXTRACTING.value)
    signal: DisruptionSignal | None = None

    evolus_agent_id = os.environ.get("EVOLUS_AGENT_ID", "")
    evolus_extractor_id = os.environ.get("EVOLUS_EXTRACTOR_ID", "")

    if evolus_agent_id and evolus_extractor_id:
        try:
            t0 = time.monotonic()
            from app.integrations.evolus import run_extractor

            raw = await run_extractor(evolus_agent_id, evolus_extractor_id, body)
            latency_ms = int((time.monotonic() - t0) * 1000)
            signal = _parse_extractor_output(shipment, raw)
            _audit(
                shipment.id, "extractor_run",
                {
                    "chokepoint": signal.chokepoint_mentioned,
                    "signal_type": signal.signal_type,
                    "result_keys": list(raw.keys()),
                },
                evolus_call=True,
                latency_ms=latency_ms,
            )
        except Exception as exc:
            logger.error("Extractor failed: %s", exc)
            _audit(shipment.id, "extractor_error", {"error": str(exc)})

    if signal is None:
        signal = _keyword_signal(shipment)
        _audit(shipment.id, "signal_fallback", {"reason": "extractor_unavailable_or_failed"})

    # Persist enriched vessel data
    with get_session() as session:
        from sqlmodel import select
        row = session.exec(
            select(ShipmentRow).where(ShipmentRow.id == shipment.id)
        ).first()
        if row:
            row.vessel_name = shipment.vessel_name
            row.bill_of_lading = shipment.bill_of_lading
            session.add(row)
            session.commit()

    return await run(shipment, signal)


async def run(shipment: Shipment, signal: DisruptionSignal) -> str:
    """Run the risk→workbook→draft pipeline for one shipment+signal pair."""
    from app.domain.cdm import Workbook
    from app.risk.assess import assess

    # ── Step 1: Risk assessment ────────────────────────────────────────────
    _save_state(shipment.id, ProcessState.ASSESSING_RISK.value)
    t0 = time.monotonic()
    try:
        assessment = await assess(shipment, signal)
    except Exception as exc:
        logger.error("Risk assessment failed: %s", exc)
        _save_state(shipment.id, ProcessState.FAILED.value)
        _audit(shipment.id, "assessment_failed", {"error": str(exc)})
        return ProcessState.FAILED.value

    _audit(
        shipment.id, "risk_assessed",
        {
            "final_level": assessment.final_level.value,
            "rules_level": assessment.rules_level.value,
            "llm_level": assessment.llm_level.value if assessment.llm_level else None,
            "recommended_port": assessment.recommended_port,
        },
        model_used=assessment.model_used,
        latency_ms=int((time.monotonic() - t0) * 1000),
        evolus_call=bool(assessment.llm_level),
    )
    _save_state(
        shipment.id,
        ProcessState.ASSESSING_RISK.value,
        risk_level=assessment.final_level.value,
        recommended_port=assessment.recommended_port,
    )

    # ── Step 2: Threshold check ────────────────────────────────────────────
    if not _level_gte(assessment.final_level, _threshold()):
        _save_state(shipment.id, ProcessState.BELOW_THRESHOLD.value)
        _audit(
            shipment.id, "below_threshold",
            {
                "final_level": assessment.final_level.value,
                "threshold": _threshold().value,
            },
        )
        logger.info(
            "Shipment %s below threshold (%s < %s) — archived.",
            shipment.id[:8],
            assessment.final_level,
            _threshold(),
        )
        return ProcessState.BELOW_THRESHOLD.value

    # ── Step 3: Provider workbook ──────────────────────────────────────────
    _save_state(shipment.id, ProcessState.BUILDING_WORKBOOK.value)
    from app.workbook.build import build as build_workbook

    try:
        workbook = await build_workbook(assessment)
        _audit(
            shipment.id, "workbook_built",
            {
                "jeddah_providers": len(workbook.jeddah_providers),
                "salalah_providers": len(workbook.salalah_providers),
                "file_path": workbook.file_path,
            },
        )
    except Exception as exc:
        logger.error("Workbook build failed: %s", exc)
        workbook = Workbook(shipment_id=shipment.id, assessment_id=assessment.id)

    # ── Step 4: Cost forecast ──────────────────────────────────────────────
    cost = None
    try:
        from app.cost.model import forecast

        container_count = shipment.container_count or 20
        cost = await forecast(assessment, container_count)
        _audit(
            shipment.id, "cost_forecast",
            {
                "p10_usd": cost.p10_usd,
                "p50_usd": cost.p50_usd,
                "p90_usd": cost.p90_usd,
                "per_container_p50_usd": cost.per_container_p50_usd,
                "port": cost.alternative_port,
                "containers": container_count,
            },
        )
        _save_state(
            shipment.id, ProcessState.BUILDING_WORKBOOK.value,
            cost_p10_usd=cost.p10_usd,
            cost_p50_usd=cost.p50_usd,
            cost_p90_usd=cost.p90_usd,
            cost_per_container_usd=cost.per_container_p50_usd,
        )
    except Exception as exc:
        logger.warning("Cost forecast failed (non-fatal): %s", exc)

    # ── Step 5: Spatial asset lookup (non-blocking) ────────────────────────
    spatial = None
    if os.environ.get("FEATURE_SPATIAL", "true").lower() == "true":
        try:
            from app.spatial.lookup import get_for_port

            spatial = get_for_port(assessment.recommended_port or "Jeddah")
            if spatial:
                _audit(
                    shipment.id, "spatial_found",
                    {"marble_url": spatial.marble_url},
                )
        except Exception as exc:
            logger.debug("Spatial lookup error (non-fatal): %s", exc)

    # ── Step 6: Build notification HTML ───────────────────────────────────
    base_url = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000")
    body_html = _notification_html(shipment, assessment, workbook, cost, spatial, base_url)

    # ── Step 7: Create Gmail draft ─────────────────────────────────────────
    trade_ops_email = (
        os.environ.get("TRADE_OPS_EMAIL")
        or os.environ.get("DEMO_SINK_EMAIL", "")
    )
    subject = (
        f"[{assessment.final_level.value.upper()}] Pre-Reroute Alert — "
        f"{shipment.vessel_name or 'Vessel'} via "
        f"{assessment.chokepoint_affected or 'Disruption'}"
    )

    attachments = []
    if workbook.file_path:
        try:
            import pathlib
            with open(workbook.file_path, "rb") as fh:
                wb_bytes = fh.read()
            fname = pathlib.Path(workbook.file_path).name
            attachments = [(
                fname,
                wb_bytes,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )]
        except Exception as exc:
            logger.warning("Could not attach workbook: %s", exc)

    _save_state(shipment.id, ProcessState.AWAITING_TRADE_OPS_APPROVAL.value)
    try:
        from app.integrations.gmail import create_draft

        gmail_draft_id = await create_draft(
            to=[trade_ops_email],
            subject=subject,
            body_html=body_html,
            attachments=attachments or None,
        )
    except Exception as exc:
        logger.error("Gmail draft creation failed: %s", exc)
        _audit(shipment.id, "draft_error", {"error": str(exc)})
        _save_state(shipment.id, ProcessState.FAILED.value)
        return ProcessState.FAILED.value

    # ── Step 8: Persist draft row ──────────────────────────────────────────
    from app.store import DraftRow, get_session

    with get_session() as session:
        draft_row = DraftRow(
            id=str(uuid.uuid4()),
            shipment_id=shipment.id,
            draft_type=DraftType.TRADE_OPS_NOTIFICATION.value,
            gmail_draft_id=gmail_draft_id,
            subject=subject,
            status="pending",
        )
        session.add(draft_row)
        session.commit()

    _audit(
        shipment.id, "draft_created",
        {
            "gmail_draft_id": gmail_draft_id,
            "draft_type": DraftType.TRADE_OPS_NOTIFICATION.value,
            "to": trade_ops_email,
        },
    )

    # ── Step 9: Notify Trade Ops by email so they can reply APPROVE/REJECT ───
    cost_summary = ""
    if cost:
        cost_summary = f"Optimistic ${cost.p10_usd:,.0f} / Expected ${cost.p50_usd:,.0f} / Conservative ${cost.p90_usd:,.0f}"

    await _send_approval_request(
        to_email=trade_ops_email,
        subject=subject,
        draft_type_label="Trade Ops Pre-Reroute Notification",
        vessel_name=shipment.vessel_name or "Unknown Vessel",
        recommended_port=assessment.recommended_port or "TBD",
        extra_rows=[
            ("Risk Level", assessment.final_level.value),
            ("Chokepoint", assessment.chokepoint_affected or "—"),
            ("Route", f"Santos → {assessment.recommended_port or 'TBD'} (diverted from Jebel Ali)"),
            ("Cost Estimate", cost_summary or "Pending provider quotes"),
            ("Approving sends", "Pre-reroute alert + workbook to Trade Ops"),
        ],
    )

    logger.info(
        "Shipment %s → AWAITING_TRADE_OPS_APPROVAL (gmail_draft=%s)",
        shipment.id[:8],
        gmail_draft_id,
    )
    return ProcessState.AWAITING_TRADE_OPS_APPROVAL.value


# ---------------------------------------------------------------------------
# P2: Post-approval pipeline
# ---------------------------------------------------------------------------


def _html_from_text(text: str) -> str:
    """Wrap plain text in a dark-branded HTML email."""
    escaped = (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\n", "<br>\n")
    )
    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="background:#071216;color:#E8F0F0;font-family:'Inter',Arial,sans-serif;font-size:14px;padding:0;margin:0;">
<div style="max-width:640px;margin:0 auto;padding:32px 24px;">
  <div style="border-bottom:2px solid #2C838C;padding-bottom:12px;margin-bottom:20px;">
    <span style="font-size:11px;color:#2C838C;text-transform:uppercase;letter-spacing:.08em;">Orbitra Reroute · AI Agent Draft</span>
  </div>
  <div style="line-height:1.7;color:#E8F0F0;">{escaped}</div>
  <hr style="border:none;border-top:1px solid #1a2e32;margin:24px 0;">
  <p style="font-size:11px;color:#555;text-align:center;">
    Drafted by an AI agent; reviewed and approved by Trade Ops.<br>
    <span style="color:#2C838C;">Generated by Orbitra Reroute · AMD Hackathon Act III</span>
  </p>
</div>
</body></html>"""


def _load_preferred_providers(port: str) -> list[dict]:
    """Load preferred providers from providers_seed.csv for a given port."""
    import csv
    import pathlib

    csv_path = pathlib.Path(__file__).parent.parent / "data" / "providers_seed.csv"
    if not csv_path.exists():
        return []

    results: list[dict] = []
    preferred: list[dict] = []
    port_lower = port.lower()

    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            if row.get("port", "").lower() == port_lower:
                results.append(row)
                if row.get("preferred", "").lower() == "true":
                    preferred.append(row)

    # Return preferred providers (up to 2); fall back to first 2 for that port
    if preferred:
        return preferred[:2]
    return results[:2]


async def post_approval_pipeline(shipment_row) -> None:
    """Generate provider outreach, finance, and compliance drafts after Trade Ops approval."""
    import time as _time

    shipment_id: str = shipment_row.id
    vessel_name: str = shipment_row.vessel_name or "Unknown Vessel"
    bill_of_lading: str = shipment_row.bill_of_lading or "—"
    recommended_port: str = shipment_row.recommended_port or "Jeddah"
    risk_level: str = shipment_row.risk_level or "High"
    received_at: str = str(getattr(shipment_row, "received_at", ""))

    sink_email = (
        os.environ.get("DEMO_SINK_EMAIL")
        or os.environ.get("TRADE_OPS_EMAIL", "")
    )

    from app.integrations.evolus import run_ai_tool
    from app.integrations.gmail import create_draft
    from app.store import DraftRow, get_session

    providers = _load_preferred_providers(recommended_port)

    # ── Provider outreach drafts (2 providers) ────────────────────────────
    _save_state(shipment_id, "sending_outreach")
    for idx, provider in enumerate(providers):
        provider_name = provider.get("name", f"Provider {idx + 1}")
        service_type = provider.get("service_type", "logistics services")
        contact_website = provider.get("contact_website", "N/A")

        primary_text = (
            f""
            f"Urgent provider outreach for shipment diversion to {recommended_port}.\n\n"
            f"Vessel: {vessel_name}\n"
            f"Bill of Lading: {bill_of_lading}\n"
            f"Risk Level: {risk_level}\n"
            f"Port of Diversion: {recommended_port}\n\n"
            f"We require {service_type} services at {recommended_port} immediately. "
            f"Please confirm availability and capacity for refrigerated poultry cargo "
            f"(Santos, Brazil origin — Jebel Ali, UAE destination diverted)."
        )
        secondary_text = (
            f"Provider: {provider_name}\n"
            f"Service: {service_type}\n"
            f"Website: {contact_website}\n"
            f"Request: Confirm cold-chain handling capacity, estimated clearance time, "
            f"and pre-registration requirements for diverted reefer shipment."
        )

        body_text = primary_text
        evolus_result = None
        try:
            t0 = _time.monotonic()
            evolus_result = await run_ai_tool(
                "email",
                primary_text,
                secondary_text,
                options={"mode": "write", "tone": "formal", "language": "en"},
            )
            latency_ms = int((_time.monotonic() - t0) * 1000)
            body_text = evolus_result
            _audit(
                shipment_id,
                "provider_outreach_draft_generated",
                {"provider": provider_name, "port": recommended_port, "idx": idx},
                evolus_call=True,
                latency_ms=latency_ms,
            )
        except Exception as exc:
            logger.warning("Evolus email tool failed for provider %s (non-fatal): %s", provider_name, exc)
            _audit(
                shipment_id,
                "provider_outreach_evolus_error",
                {"provider": provider_name, "error": str(exc)},
                evolus_call=True,
            )

        ai_line = "\n\nDrafted by an AI agent; reviewed and approved by Trade Ops."
        body_html = _html_from_text(body_text + ai_line)
        subject = (
            f"[REROUTE] Urgent — {vessel_name} Diversion to {recommended_port}: "
            f"Cold-Chain Capacity Confirmation"
        )

        try:
            gmail_draft_id = await create_draft(
                to=[sink_email],
                subject=subject,
                body_html=body_html,
            )
            with get_session() as session:
                draft_row = DraftRow(
                    id=str(uuid.uuid4()),
                    shipment_id=shipment_id,
                    draft_type=DraftType.PROVIDER_OUTREACH.value,
                    gmail_draft_id=gmail_draft_id,
                    subject=subject,
                    status="pending",
                )
                session.add(draft_row)
                session.commit()
            _audit(
                shipment_id,
                "provider_outreach_draft_created",
                {"provider": provider_name, "gmail_draft_id": gmail_draft_id},
            )
            await _send_approval_request(
                to_email=sink_email,
                subject=subject,
                draft_type_label=f"Provider Outreach — {provider_name}",
                vessel_name=vessel_name,
                recommended_port=recommended_port,
                extra_rows=[
                    ("Provider", provider_name),
                    ("Service", service_type),
                    ("Route", f"Santos → {recommended_port} (diverted from Jebel Ali)"),
                    ("Approving sends", f"Cold-chain capacity request to {provider_name}"),
                ],
            )
        except Exception as exc:
            logger.error("Failed to create provider outreach draft for %s: %s", provider_name, exc)

    # ── Finance digest draft ──────────────────────────────────────────────
    _save_state(shipment_id, "notifying_finance")
    summary_text = (
        f"[WRITE ENTIRELY IN ENGLISH]\n\n"
        f"Shipment Diversion Summary — Finance Digest\n\n"
        f"Vessel: {vessel_name}\n"
        f"Bill of Lading: {bill_of_lading}\n"
        f"Risk Level: {risk_level}\n"
        f"Recommended Diversion Port: {recommended_port}\n"
        f"Received: {received_at}\n\n"
        f"Action: Pre-re-route process triggered due to Gulf/Hormuz/Red Sea disruption. "
        f"Shipment originally routed Santos (Brazil) → Jebel Ali (UAE). "
        f"Rerouting to {recommended_port} with reefer cold-chain continuity. "
        f"Cost estimates (Optimistic/Expected/Conservative) are pending actual provider quotes. "
        f"Additional clearance fees, storage, and feeder vessel costs expected. "
        f"Finance team to initiate budget contingency review and update ETA in TMS."
    )

    finance_body_text = summary_text
    try:
        t0 = _time.monotonic()
        finance_result = await run_ai_tool(
            "summarize",
            summary_text,
            options={"length": "short", "style": "bullets", "language": "en"},
        )
        latency_ms = int((_time.monotonic() - t0) * 1000)
        finance_body_text = finance_result
        _audit(
            shipment_id,
            "finance_digest_generated",
            {"port": recommended_port},
            evolus_call=True,
            latency_ms=latency_ms,
        )
    except Exception as exc:
        logger.warning("Evolus summarize tool failed for finance digest (non-fatal): %s", exc)
        _audit(
            shipment_id,
            "finance_digest_evolus_error",
            {"error": str(exc)},
            evolus_call=True,
        )

    ai_line = "\n\nDrafted by an AI agent; reviewed and approved by Finance."
    finance_html = _html_from_text(finance_body_text + ai_line)
    finance_subject = (
        f"[FINANCE DIGEST] Reroute Alert — {vessel_name} → {recommended_port} ({risk_level})"
    )

    try:
        finance_draft_id = await create_draft(
            to=[sink_email],
            subject=finance_subject,
            body_html=finance_html,
            cc=["finance@orbitralab.com"],
        )
        with get_session() as session:
            draft_row = DraftRow(
                id=str(uuid.uuid4()),
                shipment_id=shipment_id,
                draft_type=DraftType.FINANCE_DIGEST.value,
                gmail_draft_id=finance_draft_id,
                subject=finance_subject,
                status="pending",
            )
            session.add(draft_row)
            session.commit()
        _audit(
            shipment_id,
            "finance_digest_draft_created",
            {"gmail_draft_id": finance_draft_id},
        )
        await _send_approval_request(
            to_email=sink_email,
            subject=finance_subject,
            draft_type_label="Finance Digest",
            vessel_name=vessel_name,
            recommended_port=recommended_port,
            extra_rows=[
                ("B/L Number", bill_of_lading),
                ("Risk Level", risk_level),
                ("Route", f"Santos → {recommended_port} (diverted from Jebel Ali)"),
                ("Approving sends", "Finance team budget contingency alert + ETA update request"),
            ],
        )
    except Exception as exc:
        logger.error("Failed to create finance digest draft: %s", exc)

    # ── Compliance notes draft ────────────────────────────────────────────
    _save_state(shipment_id, "notifying_compliance")
    compliance_text = (
        f"[WRITE ENTIRELY IN ENGLISH]\n\n"
        f"Compliance Notification — Shipment Diversion\n\n"
        f"Vessel: {vessel_name}\n"
        f"Bill of Lading: {bill_of_lading}\n"
        f"Risk Level: {risk_level}\n"
        f"Diversion Port: {recommended_port}\n\n"
        f"This notification confirms that a pre-re-route process has been initiated "
        f"for a refrigerated poultry shipment (Santos, Brazil → Jebel Ali, UAE) "
        f"due to detected disruption in the Gulf/Hormuz/Red Sea corridor.\n\n"
        f"Compliance actions required:\n"
        f"- Verify SFDA import permit applicability for {recommended_port}\n"
        f"- Confirm halal certification documentation is current\n"
        f"- Review cold-chain temperature log continuity requirements\n"
        f"- Update bill of lading port of discharge to {recommended_port}\n"
        f"- Notify UAE customs and Jebel Ali port authority of diversion\n"
        f"- Confirm phytosanitary certificates remain valid for alternate port\n\n"
        f"All regulatory filings must be completed before vessel arrival at {recommended_port}."
    )

    compliance_body_text = compliance_text
    try:
        t0 = _time.monotonic()
        compliance_result = await run_ai_tool(
            "email",
            compliance_text,
            options={"mode": "write", "tone": "formal", "language": "en"},
        )
        latency_ms = int((_time.monotonic() - t0) * 1000)
        compliance_body_text = compliance_result
        _audit(
            shipment_id,
            "compliance_notes_generated",
            {"port": recommended_port},
            evolus_call=True,
            latency_ms=latency_ms,
        )
    except Exception as exc:
        logger.warning("Evolus email tool failed for compliance notes (non-fatal): %s", exc)
        _audit(
            shipment_id,
            "compliance_notes_evolus_error",
            {"error": str(exc)},
            evolus_call=True,
        )

    ai_line = "\n\nDrafted by an AI agent; reviewed and approved by Compliance."
    compliance_html = _html_from_text(compliance_body_text + ai_line)
    compliance_subject = (
        f"[COMPLIANCE] Reroute Notification — {vessel_name} Diversion to {recommended_port}"
    )

    try:
        compliance_draft_id = await create_draft(
            to=[sink_email],
            subject=compliance_subject,
            body_html=compliance_html,
            cc=["compliance@orbitralab.com"],
        )
        with get_session() as session:
            draft_row = DraftRow(
                id=str(uuid.uuid4()),
                shipment_id=shipment_id,
                draft_type=DraftType.COMPLIANCE_NOTES.value,
                gmail_draft_id=compliance_draft_id,
                subject=compliance_subject,
                status="pending",
            )
            session.add(draft_row)
            session.commit()
        _audit(
            shipment_id,
            "compliance_notes_draft_created",
            {"gmail_draft_id": compliance_draft_id},
        )
        await _send_approval_request(
            to_email=sink_email,
            subject=compliance_subject,
            draft_type_label="Compliance Notes",
            vessel_name=vessel_name,
            recommended_port=recommended_port,
            extra_rows=[
                ("B/L Number", bill_of_lading),
                ("Diversion Port", recommended_port),
                ("Risk Level", risk_level),
                ("Approving sends", "Compliance alert — SFDA permits, halal certs, customs notification"),
            ],
        )
    except Exception as exc:
        logger.error("Failed to create compliance notes draft: %s", exc)

    _save_state(shipment_id, "completed")
    logger.info(
        "Shipment %s → post_approval_pipeline COMPLETED (provider drafts + finance + compliance)",
        shipment_id[:8],
    )
