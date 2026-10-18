"""Orbitra Reroute — FastAPI application entry point.

Routes: / (Trade Ops desk), /approvals, /audit, /amd,
        /shipment/{id}, /inbox/check, /healthz.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.store import init_db

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "web" / "templates"))
_DEMO_MODE = os.environ.get("DEMO_MODE", "true").lower() == "true"


async def _poll_inbox_job() -> None:
    """APScheduler job — poll Gmail and process new messages."""
    try:
        from app.integrations.gmail import get_message, mark_read, poll_inbox
        from app.orchestrator import process_email

        stubs = await poll_inbox(max_results=5)
        for stub in stubs:
            msg_id = stub["id"]
            try:
                message = await get_message(msg_id)
                await process_email(message)
                await mark_read(msg_id)
            except Exception as exc:
                logger.error("Scheduled poll: failed on message %s: %s", msg_id, exc)
    except Exception as exc:
        logger.error("Scheduled inbox poll failed: %s", exc)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    logger.info("Database initialised.")

    api_key = os.environ.get("TOKEN_FACTORY_API_KEY", "")
    if api_key:
        try:
            from app.llm import verify_primary_model_available
            await verify_primary_model_available()
        except Exception as exc:
            logger.error("Token Factory startup check failed: %s", exc)
    else:
        logger.warning("TOKEN_FACTORY_API_KEY not set — LLM calls will fail at runtime.")

    # Start periodic Gmail polling (every 60 seconds)
    gmail_key = os.environ.get("GMAIL_REFRESH_TOKEN", "")
    if gmail_key:
        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
            scheduler = AsyncIOScheduler()
            scheduler.add_job(_poll_inbox_job, "interval", seconds=60, id="gmail_poll")
            scheduler.start()
            logger.info("Gmail poll scheduler started (60s interval).")
        except Exception as exc:
            logger.warning("Could not start Gmail scheduler: %s", exc)

    yield

    logger.info("Shutting down.")


app = FastAPI(
    title="Orbitra Reroute",
    description="AI-powered cold-chain shipment pre-re-routing agent",
    version="0.1.0",
    lifespan=lifespan,
)

_STATIC = Path(__file__).parent / "web" / "static"
if _STATIC.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@app.get("/healthz", include_in_schema=False)
async def healthz() -> JSONResponse:
    return JSONResponse({"status": "ok", "demo_mode": _DEMO_MODE})


# ---------------------------------------------------------------------------
# Trade Ops Desk
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    from app.store import ShipmentRow, get_session
    from sqlmodel import select

    with get_session() as session:
        rows = session.exec(
            select(ShipmentRow).order_by(ShipmentRow.received_at.desc()).limit(20)
        ).all()

    stats = {
        "active": sum(
            1 for r in rows
            if r.state not in ("completed", "failed", "below_threshold")
        ),
        "pending_approvals": sum(
            1 for r in rows if r.state == "awaiting_trade_ops_approval"
        ),
        "processed_today": len(rows),
    }
    return _TEMPLATES.TemplateResponse(
        request, "index.html", {"shipments": rows, "stats": stats}
    )


# ---------------------------------------------------------------------------
# Shipment detail
# ---------------------------------------------------------------------------


@app.get("/shipment/{shipment_id}", response_class=HTMLResponse)
async def shipment_detail(request: Request, shipment_id: str) -> HTMLResponse:
    from app.store import AuditRow, DraftRow, ShipmentRow, get_session
    from sqlmodel import select

    with get_session() as session:
        shipment = session.exec(
            select(ShipmentRow).where(ShipmentRow.id == shipment_id)
        ).first()
        if not shipment:
            raise HTTPException(status_code=404, detail="Shipment not found.")

        drafts = session.exec(
            select(DraftRow)
            .where(DraftRow.shipment_id == shipment_id)
            .order_by(DraftRow.created_at.desc())
        ).all()

        audit_events = session.exec(
            select(AuditRow)
            .where(AuditRow.shipment_id == shipment_id)
            .order_by(AuditRow.occurred_at.desc())
        ).all()

    return _TEMPLATES.TemplateResponse(
        request,
        "shipment.html",
        {"shipment": shipment, "drafts": drafts, "audit_events": audit_events},
    )


# ---------------------------------------------------------------------------
# Inbox check (manual trigger + APScheduler hook)
# ---------------------------------------------------------------------------


@app.post("/inbox/check", include_in_schema=False)
async def check_inbox() -> JSONResponse:
    """Poll Gmail INBOX for UNREAD messages and process each one."""
    try:
        from app.integrations.gmail import get_message, mark_read, poll_inbox
        from app.orchestrator import process_email

        stubs = await poll_inbox(max_results=5)
        if not stubs:
            return JSONResponse({"found": 0, "processed": 0})

        results = []
        for stub in stubs:
            msg_id = stub["id"]
            try:
                message = await get_message(msg_id)
                state = await process_email(message)
                await mark_read(msg_id)
                results.append({"id": msg_id, "state": state})
            except Exception as exc:
                logger.error("Failed to process message %s: %s", msg_id, exc)
                results.append({"id": msg_id, "error": str(exc)})

        processed = sum(1 for r in results if "state" in r)
        return JSONResponse({"found": len(stubs), "processed": processed, "results": results})

    except Exception as exc:
        logger.error("Inbox check failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Approvals
# ---------------------------------------------------------------------------


@app.get("/approvals", response_class=HTMLResponse)
async def approvals_page(request: Request) -> HTMLResponse:
    from app.store import DraftRow, ShipmentRow, get_session
    from sqlmodel import select

    with get_session() as session:
        pending_drafts = session.exec(
            select(DraftRow)
            .where(DraftRow.status == "pending")
            .order_by(DraftRow.created_at.desc())
        ).all()

        drafts_with_ship: list[dict[str, Any]] = []
        for d in pending_drafts:
            ship = session.exec(
                select(ShipmentRow).where(ShipmentRow.id == d.shipment_id)
            ).first()
            drafts_with_ship.append({"draft": d, "shipment": ship})

    return _TEMPLATES.TemplateResponse(
        request, "approvals.html", {"drafts": drafts_with_ship}
    )


@app.post("/approvals/{draft_id}/approve")
async def approve_draft(
    draft_id: str,
    approver: str = "trade.ops",
) -> JSONResponse:
    from app.store import DraftRow, get_session
    from sqlmodel import select

    with get_session() as session:
        draft = session.exec(
            select(DraftRow).where(DraftRow.id == draft_id)
        ).first()
        if not draft:
            raise HTTPException(status_code=404, detail="Draft not found.")
        if draft.status != "pending":
            raise HTTPException(status_code=409, detail=f"Draft is already {draft.status}.")
        if not draft.gmail_draft_id:
            raise HTTPException(status_code=400, detail="No Gmail draft ID on record.")

    try:
        from app.orchestrator import _execute_approval
        result = await _execute_approval(draft_id, approver=approver)
    except Exception as exc:
        logger.error("approve_draft failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    return JSONResponse({"status": result, "draft_id": draft_id})


@app.post("/approvals/{draft_id}/resend-notification")
async def resend_notification(draft_id: str) -> JSONResponse:
    """Re-send the approval-request email for a pending draft (in case first email was lost)."""
    from app.store import DraftRow, ShipmentRow, get_session
    from sqlmodel import select

    with get_session() as session:
        draft = session.exec(select(DraftRow).where(DraftRow.id == draft_id)).first()
        if not draft:
            raise HTTPException(status_code=404, detail="Draft not found.")
        if draft.status != "pending":
            raise HTTPException(status_code=409, detail=f"Draft is already {draft.status}.")
        draft_subject = draft.subject
        draft_type = draft.draft_type
        shipment_id = draft.shipment_id
        ship = session.exec(select(ShipmentRow).where(ShipmentRow.id == shipment_id)).first()
        vessel_name = ship.vessel_name if ship else "Unknown Vessel"
        recommended_port = ship.recommended_port if ship else "TBD"

    trade_ops_email = os.environ.get("TRADE_OPS_EMAIL") or os.environ.get("DEMO_SINK_EMAIL", "")
    if not trade_ops_email:
        raise HTTPException(status_code=500, detail="TRADE_OPS_EMAIL / DEMO_SINK_EMAIL not set.")

    try:
        from app.orchestrator import _send_approval_request
        await _send_approval_request(
            to_email=trade_ops_email,
            subject=draft_subject,
            draft_type_label=draft_type.replace("_", " ").title(),
            vessel_name=vessel_name,
            recommended_port=recommended_port or "TBD",
        )
    except Exception as exc:
        logger.error("resend_notification failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    return JSONResponse({"status": "sent", "to": trade_ops_email, "subject": draft_subject})


@app.post("/approvals/{draft_id}/reject")
async def reject_draft(
    draft_id: str,
    approver: str = "trade.ops",
) -> JSONResponse:
    from app.store import DraftRow, get_session
    from sqlmodel import select

    with get_session() as session:
        draft = session.exec(
            select(DraftRow).where(DraftRow.id == draft_id)
        ).first()
        if not draft:
            raise HTTPException(status_code=404, detail="Draft not found.")
        if draft.status != "pending":
            raise HTTPException(status_code=409, detail=f"Draft is already {draft.status}.")

    try:
        from app.orchestrator import _execute_rejection
        result = await _execute_rejection(draft_id, approver=approver)
    except Exception as exc:
        logger.error("reject_draft failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    return JSONResponse({"status": result, "draft_id": draft_id})


# ---------------------------------------------------------------------------
# Workbook download
# ---------------------------------------------------------------------------


@app.get("/workbook/{shipment_id}")
async def download_workbook(shipment_id: str) -> FileResponse:
    import pathlib

    wb_dir = (
        pathlib.Path(__file__).parent.parent / "data" / "workbooks"
    )
    path = wb_dir / f"workbook_{shipment_id[:8]}.xlsx"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Workbook not found.")
    return FileResponse(
        str(path),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=path.name,
    )


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


@app.get("/audit", response_class=HTMLResponse)
async def audit_page(request: Request) -> HTMLResponse:
    from app.store import AuditRow, get_session
    from sqlmodel import select

    with get_session() as session:
        events = session.exec(
            select(AuditRow).order_by(AuditRow.occurred_at.desc()).limit(100)
        ).all()

    return _TEMPLATES.TemplateResponse(
        request, "audit.html", {"events": events}
    )


# ---------------------------------------------------------------------------
# AMD Panel
# ---------------------------------------------------------------------------


@app.get("/amd", response_class=HTMLResponse)
async def amd_panel(request: Request) -> HTMLResponse:
    from app.store import AuditRow, get_session
    from sqlmodel import select

    with get_session() as session:
        all_audit = session.exec(
            select(AuditRow).order_by(AuditRow.occurred_at.desc()).limit(500)
        ).all()

    evolus_calls = [e for e in all_audit if e.evolus_call][:50]

    # Compute stats for the AMD panel
    token_factory_calls = [
        e for e in all_audit if e.model_used and not e.evolus_call
    ]
    evolus_latencies = [
        e.latency_ms for e in evolus_calls if e.latency_ms is not None
    ]
    avg_latency_ms = (
        int(sum(evolus_latencies) / len(evolus_latencies))
        if evolus_latencies else None
    )

    model_breakdown: dict[str, int] = {}
    for e in all_audit:
        if e.model_used:
            model_breakdown[e.model_used] = model_breakdown.get(e.model_used, 0) + 1

    stats = {
        "total_token_factory_calls": len(token_factory_calls),
        "total_evolus_calls": len(evolus_calls),
        "avg_latency_ms": avg_latency_ms,
        "model_breakdown": model_breakdown,
    }

    return _TEMPLATES.TemplateResponse(
        request, "amd.html", {"evolus_calls": evolus_calls, "stats": stats}
    )


@app.get("/situation-room/{shipment_id}", response_class=HTMLResponse)
async def situation_room(request: Request, shipment_id: str) -> HTMLResponse:
    from app.store import ShipmentRow, get_session
    from sqlmodel import select

    with get_session() as session:
        shipment = session.exec(
            select(ShipmentRow).where(ShipmentRow.id == shipment_id)
        ).first()
        if not shipment:
            raise HTTPException(status_code=404, detail="Shipment not found.")

    spatial = None
    if shipment.recommended_port:
        try:
            from app.spatial.lookup import get_for_port
            spatial = get_for_port(shipment.recommended_port)
        except Exception as exc:
            logger.debug("Spatial lookup failed (non-fatal): %s", exc)

    return _TEMPLATES.TemplateResponse(
        request,
        "situation_room.html",
        {"shipment": shipment, "spatial": spatial},
    )


@app.get("/gmail/debug")
async def gmail_debug() -> JSONResponse:
    """Diagnose Gmail auth: returns authenticated account + INBOX UNREAD messages."""
    try:
        from app.integrations.gmail import get_authenticated_user, poll_inbox
        user_info = await get_authenticated_user()
        stubs = await poll_inbox(max_results=10)
        from app.integrations.gmail import list_recent_messages
        recent = await list_recent_messages(max_results=10)
        return JSONResponse({
            "authenticated_as": user_info,
            "gmail_monitor_address_env": os.environ.get("GMAIL_MONITOR_ADDRESS", "(not set)"),
            "demo_sink_email_env": os.environ.get("DEMO_SINK_EMAIL", "(not set)"),
            "unread_inbox_count": len(stubs),
            "unread_message_ids": [s["id"] for s in stubs],
            "recent_messages": recent,
        })
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.get("/amd/usage")
async def amd_usage() -> JSONResponse:
    api_key = os.environ.get("TOKEN_FACTORY_API_KEY", "")
    if not api_key:
        return JSONResponse({"error": "TOKEN_FACTORY_API_KEY not set"}, status_code=503)
    from app.llm import get_usage

    data = await get_usage()
    return JSONResponse(data)
