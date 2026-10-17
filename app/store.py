"""SQLite persistence layer (SQLModel + aiosqlite).

Stores shipments, drafts, approvals, and audit events.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

from sqlmodel import Field, Session, SQLModel, create_engine, select

_DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./orbitra.db")
# Use synchronous engine for simplicity during the hackathon;
# swap to aiosqlite async engine if concurrency becomes an issue.
engine = create_engine(_DATABASE_URL, echo=False)


class ShipmentRow(SQLModel, table=True):
    __tablename__ = "shipments"
    id: str = Field(primary_key=True)
    gmail_message_id: str | None = Field(default=None, unique=True, index=True)
    vessel_name: str | None = None
    bill_of_lading: str | None = None
    state: str = "received"
    risk_level: str | None = None
    recommended_port: str | None = None
    cost_p10_usd: float | None = None
    cost_p50_usd: float | None = None
    cost_p90_usd: float | None = None
    cost_per_container_usd: float | None = None
    received_at: datetime = Field(default_factory=_utcnow)


class DraftRow(SQLModel, table=True):
    __tablename__ = "drafts"
    id: str = Field(primary_key=True)
    shipment_id: str
    draft_type: str
    gmail_draft_id: str | None = None
    subject: str = ""
    status: str = "pending"             # "pending" | "approved" | "rejected" | "sent"
    created_at: datetime = Field(default_factory=_utcnow)


class AuditRow(SQLModel, table=True):
    __tablename__ = "audit"
    id: str = Field(primary_key=True)
    shipment_id: str | None = None
    event_type: str
    actor: str = "system"
    model_used: str | None = None
    latency_ms: int | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    evolus_call: bool = False
    details_json: str = "{}"
    occurred_at: datetime = Field(default_factory=_utcnow)


def init_db() -> None:
    SQLModel.metadata.create_all(engine)


# Ensure tables exist as soon as this module is imported.
init_db()


def get_session() -> Session:
    return Session(engine)
