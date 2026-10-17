"""Unit tests for the Canonical Data Model."""

from datetime import datetime

import pytest

from app.domain.cdm import (
    Approval,
    ApprovalDecision,
    AuditEvent,
    BlockageClassification,
    BlockagePrediction,
    ChokePointFeatures,
    CostForecast,
    DisruptionSignal,
    Draft,
    DraftType,
    ProcessState,
    Provider,
    RiskAssessment,
    RiskLevel,
    Shipment,
    SpatialAsset,
    SpatialSource,
    Workbook,
)


class TestShipment:
    def test_defaults(self):
        s = Shipment()
        assert s.origin_port == "Santos"
        assert s.destination_port == "Jebel Ali"
        assert s.source_language == "en"
        assert s.id is not None

    def test_fields_optional(self):
        s = Shipment(vessel_name="MV FICTIONAL STAR", bill_of_lading="BLTEST000001")
        assert s.vessel_name == "MV FICTIONAL STAR"
        assert s.bill_of_lading == "BLTEST000001"
        assert s.carrier is None


class TestChokePointFeatures:
    def test_construction(self):
        f = ChokePointFeatures(
            date=datetime(2026, 10, 17),
            chokepoint="Strait of Hormuz",
            goldstein_score=-3.5,
            news_risk_score=7.2,
            vessel_count=142,
        )
        assert f.chokepoint == "Strait of Hormuz"
        assert f.goldstein_score == -3.5


class TestBlockagePrediction:
    def test_probability_bounds(self):
        p = BlockagePrediction(
            chokepoint="Strait of Hormuz",
            probability=0.85,
            classification=BlockageClassification.BLOCKED,
            model_used="xgboost",
            confidence=0.78,
        )
        assert 0.0 <= p.probability <= 1.0

    def test_invalid_probability(self):
        with pytest.raises(Exception):
            BlockagePrediction(
                chokepoint="test",
                probability=1.5,  # out of range
                classification=BlockageClassification.BLOCKED,
                model_used="xgboost",
                confidence=0.5,
            )


class TestRiskAssessment:
    def test_final_level(self):
        r = RiskAssessment(
            shipment_id="s1",
            rules_level=RiskLevel.HIGH,
            rules_score=8,
            llm_level=RiskLevel.MEDIUM,
            final_level=RiskLevel.HIGH,
            recommended_port="Salalah",
        )
        assert r.final_level == RiskLevel.HIGH
        assert r.recommended_port == "Salalah"


class TestProvider:
    def test_defaults(self):
        p = Provider(name="Test Logistics LLC", port="Jeddah", service_type="customs clearance")
        assert p.pre_registration_requirements == "TBC with provider"
        assert p.est_clearance_time == "TBC with provider"
        assert not p.preferred
        assert p.source == "places_api"


class TestWorkbook:
    def test_spatial_footer_label(self):
        wb = Workbook(shipment_id="s1", assessment_id="a1")
        assert "AI-generated spatial approximation" in wb.spatial_footer_label
        assert "verify with provider" in wb.spatial_footer_label


class TestSpatialAsset:
    def test_label_verbatim(self):
        asset = SpatialAsset(
            port="Jeddah",
            title="Jeddah Islamic Port",
            marble_url="https://marble.worldlabs.ai/world/example",
        )
        assert asset.label == (
            "AI-generated spatial approximation — verify with provider / port authority."
        )
        assert asset.source == SpatialSource.MARBLE_APP


class TestCostForecast:
    def test_label(self):
        cf = CostForecast(
            shipment_id="s1",
            assessment_id="a1",
            alternative_port="Salalah",
            p10_usd=45000,
            p50_usd=72000,
            p90_usd=110000,
            per_container_p50_usd=3600,
            container_count=20,
        )
        assert cf.label == "Estimate pending actual quotes"
        assert cf.assumptions_have_placeholders is True


class TestApproval:
    def test_decision(self):
        a = Approval(
            draft_id="d1",
            shipment_id="s1",
            decision=ApprovalDecision.APPROVED,
            approver="trade.ops@example.com",
        )
        assert a.decision == ApprovalDecision.APPROVED


class TestAuditEvent:
    def test_defaults(self):
        e = AuditEvent(shipment_id="s1", event_type="risk_assessed")
        assert e.actor == "system"
        assert not e.evolus_call
        assert e.id is not None


class TestEnums:
    def test_process_states(self):
        assert ProcessState.AWAITING_TRADE_OPS_APPROVAL in list(ProcessState)
        assert ProcessState.COMPLETED in list(ProcessState)

    def test_risk_levels_ordering(self):
        levels = [RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL]
        assert len(levels) == 4
