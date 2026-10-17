"""Canonical Data Model — every integration maps into/out of these types.

Raw vendor JSON (Gmail, Evolus, Places, GDELT, PortWatch) never travels
through the app. Only CDM types cross module boundaries.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class RiskLevel(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    CRITICAL = "Critical"


class ProcessState(str, Enum):
    """States of the pre-re-route orchestration state machine."""
    RECEIVED = "received"
    EXTRACTING = "extracting"
    ASSESSING_RISK = "assessing_risk"
    BELOW_THRESHOLD = "below_threshold"
    BUILDING_WORKBOOK = "building_workbook"
    AWAITING_TRADE_OPS_APPROVAL = "awaiting_trade_ops_approval"
    TRADE_OPS_APPROVED = "trade_ops_approved"
    TRADE_OPS_REJECTED = "trade_ops_rejected"
    SENDING_OUTREACH = "sending_outreach"
    NOTIFYING_FINANCE = "notifying_finance"
    NOTIFYING_COMPLIANCE = "notifying_compliance"
    COMPLETED = "completed"
    FAILED = "failed"


class BlockageClassification(str, Enum):
    BLOCKED = "blocked"
    NOT_BLOCKED = "not_blocked"
    UNKNOWN = "unknown"


class SpatialSource(str, Enum):
    MARBLE_APP = "marble_app"
    WORLD_API = "world_api"


class DraftType(str, Enum):
    TRADE_OPS_NOTIFICATION = "trade_ops_notification"
    PROVIDER_OUTREACH = "provider_outreach"
    FINANCE_DIGEST = "finance_digest"
    COMPLIANCE_NOTES = "compliance_notes"


class ApprovalDecision(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"


# ---------------------------------------------------------------------------
# Stage 1 — Acquisition & Feature Engineering (Ericka's pipeline)
# ---------------------------------------------------------------------------


class ChokePointFeatures(BaseModel):
    """Features extracted per chokepoint event for the ML blockage classifier."""
    date: datetime
    chokepoint: str                          # e.g. "Strait of Hormuz"
    goldstein_score: float | None = None     # GDELT Goldstein scale (-10 to +10)
    news_risk_score: float | None = None     # Gemma 0-10 score
    news_risk_reason: str | None = None
    vessel_count: int | None = None          # port congestion from PortWatch
    vessel_distance_km: float | None = None  # vessel distance from chokepoint
    event_distance_km: float | None = None   # event distance from chokepoint
    headline_sample: str | None = None       # representative headline


# ---------------------------------------------------------------------------
# Stage 2 — ML Blockage Prediction
# ---------------------------------------------------------------------------


class BlockagePrediction(BaseModel):
    """Output of the ML blockage classifier (Stage 2)."""
    chokepoint: str
    probability: float = Field(ge=0.0, le=1.0)
    classification: BlockageClassification
    model_used: str                         # "xgboost" | "lstm" | "fallback_rules"
    confidence: float = Field(ge=0.0, le=1.0)
    features_used: ChokePointFeatures | None = None


# ---------------------------------------------------------------------------
# Core shipping entities
# ---------------------------------------------------------------------------


class Shipment(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    gmail_message_id: str | None = None
    vessel_name: str | None = None
    bill_of_lading: str | None = None
    carrier: str | None = None
    origin_port: str = "Santos"
    destination_port: str = "Jebel Ali"
    current_eta: datetime | None = None
    original_eta: datetime | None = None
    eta_slip_days: int | None = None
    cargo_description: str | None = None
    container_count: int | None = None
    temperature_requirement_c: float | None = None
    received_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    raw_email_subject: str | None = None
    raw_email_body: str | None = None
    source_language: str = "en"


class DisruptionSignal(BaseModel):
    """Extracted disruption signals from an email."""
    shipment_id: str
    signal_type: str                        # "diversion", "security_advisory", "eta_slip", etc.
    chokepoint_mentioned: str | None = None # "Hormuz", "Bab-el-Mandeb", "Red Sea", etc.
    diversion_language: list[str] = Field(default_factory=list)
    carrier_advisory_present: bool = False
    severity_keywords: list[str] = Field(default_factory=list)
    extracted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Stage 3 — Risk Assessment (hybrid: rules + ML + Evolus LLM)
# ---------------------------------------------------------------------------


class RiskAssessment(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    shipment_id: str
    rules_level: RiskLevel
    rules_score: int = 0
    llm_level: RiskLevel | None = None
    llm_justification: str | None = None
    ml_prediction: BlockagePrediction | None = None
    final_level: RiskLevel
    recommended_port: str | None = None    # "Jeddah" | "Salalah"
    secondary_ports: list[str] = Field(default_factory=list)
    cold_chain_implications: str | None = None
    chokepoint_affected: str | None = None
    assessed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    model_used: str | None = None


# ---------------------------------------------------------------------------
# Provider workbook
# ---------------------------------------------------------------------------


class Provider(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    port: str                              # "Jeddah" | "Salalah"
    service_type: str
    google_rating: float | None = None
    google_review_count: int | None = None
    key_strengths: list[str] = Field(default_factory=list)
    strengths_evidence: list[str] = Field(default_factory=list)
    contact_website: str | None = None
    contact_phone: str | None = None
    pre_registration_requirements: str = "TBC with provider"
    est_clearance_time: str = "TBC with provider"
    notes: str | None = None
    preferred: bool = False
    place_id: str | None = None
    spatial_preview_url: str | None = None
    source: str = "places_api"             # "places_api" | "seed_csv"


class Workbook(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    shipment_id: str
    assessment_id: str
    jeddah_providers: list[Provider] = Field(default_factory=list)
    salalah_providers: list[Provider] = Field(default_factory=list)
    spatial_footer_label: str = (
        "AI-generated spatial approximation — verify with provider / port authority."
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    file_path: str | None = None


# ---------------------------------------------------------------------------
# Spatial asset (World Labs W1/W2/W3/W4)
# ---------------------------------------------------------------------------


class SpatialAsset(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    port: str
    scope: str = "port"                    # "port" | "provider"
    provider_ref: str | None = None
    title: str
    marble_url: str
    thumbnail_url: str | None = None
    source: SpatialSource = SpatialSource.MARBLE_APP
    prompt: str | None = None
    input_images: list[dict[str, str]] = Field(default_factory=list)
    created_at: datetime | None = None
    link_verified_at: datetime | None = None
    label: str = (
        "AI-generated spatial approximation — verify with provider / port authority."
    )


# ---------------------------------------------------------------------------
# Cost forecast (Stage 2 output — deterministic Monte Carlo, not LLM)
# ---------------------------------------------------------------------------


class CostForecast(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    shipment_id: str
    assessment_id: str
    alternative_port: str
    p10_usd: float
    p50_usd: float
    p90_usd: float
    per_container_p50_usd: float
    container_count: int
    assumptions_have_placeholders: bool = True
    plain_language_summary: str | None = None  # written by Gemma
    label: str = "Estimate pending actual quotes"
    computed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Drafts and approvals
# ---------------------------------------------------------------------------


class Draft(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    shipment_id: str
    draft_type: DraftType
    gmail_draft_id: str | None = None
    to_addresses: list[str] = Field(default_factory=list)
    subject: str = ""
    body_html: str = ""
    attachments: list[str] = Field(default_factory=list)
    ai_transparency_line: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    created_by_model: str | None = None


class Approval(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    draft_id: str
    shipment_id: str
    decision: ApprovalDecision
    approver: str
    notes: str | None = None
    decided_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


class AuditEvent(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    shipment_id: str | None = None
    event_type: str
    actor: str = "system"                  # "system" | "user:<name>" | "model:<name>"
    details: dict[str, Any] = Field(default_factory=dict)
    model_used: str | None = None
    latency_ms: int | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    evolus_call: bool = False
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
