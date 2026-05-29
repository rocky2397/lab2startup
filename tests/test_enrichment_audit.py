"""Tests for enrichment audit capture and verification."""

from __future__ import annotations

from app.agents.report_agent import run_reports
from app.agents.signal_agent import detect_signals
from app.config import AgenticSignalConfig, PerplexityConfig
from app.enrichment_audit import (
    EnrichmentMode,
    build_enrichment_audit,
    deserialize_enrichment_audit,
    rerun_enrichment_verification,
    serialize_enrichment_audit,
    summarize_enrichment_audit,
)
from app.models import IdentityConfidence, Researcher
from app.run_store import load_enrichment_audit, save_enrichment_audit, save_run_snapshot


def test_build_enrichment_audit_tracks_affiliation_resolution() -> None:
    pre = Researcher(
        id="researcher_a",
        name="Ada Lovelace",
        affiliation="Unknown",
        role="Researcher",
        papers=["paper_1"],
        identity_confidence=IdentityConfidence.LOW,
    )
    post = pre.model_copy(
        update={
            "affiliation": "Analytical Engines Inc",
            "role": "Founder",
            "identity_confidence": IdentityConfidence.HIGH,
        }
    )
    audit = build_enrichment_audit(
        run_id="run_test",
        mode=EnrichmentMode.SONAR,
        pre_researchers=[pre],
        post_researchers=[post],
        signals=[],
        targeted_ids={pre.id},
        investigated_ids={pre.id},
        skip_reason_by_id={},
        config_summary={"max_researchers": 10},
    )

    assert audit.enrichment_worked is True
    assert audit.affiliation_resolved_count == 1
    assert audit.records[0].status == "enriched"


def test_build_enrichment_audit_distinguishes_investigation_outcomes() -> None:
    from app.models import EvidenceStrength, Signal, SignalType

    pre = Researcher(
        id="researcher_a",
        name="Ada Lovelace",
        affiliation="Unknown",
        role="Researcher",
        papers=["paper_1"],
    )
    post = pre.model_copy(deep=True)
    failed = Researcher(
        id="researcher_b",
        name="Grace Hopper",
        affiliation="Unknown",
        role="Researcher",
        papers=["paper_2"],
    )
    signal = Signal(
        id="agent_signal_1",
        signal_type=SignalType.POSSIBLE_FOUNDER,
        description="Stealth startup",
        source_url="https://example.com",
        evidence_strength=EvidenceStrength.MEDIUM,
        date_found="2025-05-22",
        researcher_id=pre.id,
        researcher_name=pre.name,
    )

    audit = build_enrichment_audit(
        run_id="run_test",
        mode=EnrichmentMode.AGENTIC,
        pre_researchers=[pre, failed],
        post_researchers=[post, failed],
        signals=[signal],
        targeted_ids={pre.id, failed.id},
        investigated_ids={pre.id, failed.id},
        investigation_failed_ids={failed.id},
    )

    by_id = {record.researcher_id: record for record in audit.records}
    assert by_id[pre.id].status == "investigated_with_signals"
    assert by_id[failed.id].status == "investigation_failed"

    no_signal_pre = Researcher(
        id="researcher_c",
        name="Alan Turing",
        affiliation="Unknown",
        role="Researcher",
        papers=["paper_3"],
    )
    no_signal_audit = build_enrichment_audit(
        run_id="run_test",
        mode=EnrichmentMode.AGENTIC,
        pre_researchers=[no_signal_pre],
        post_researchers=[no_signal_pre],
        signals=[],
        targeted_ids={no_signal_pre.id},
        investigated_ids={no_signal_pre.id},
    )
    assert no_signal_audit.records[0].status == "investigated_no_signal"


def test_summarize_enrichment_audit_lists_profile_names() -> None:
    pre = Researcher(
        id="researcher_a",
        name="Ada Lovelace",
        affiliation="Unknown",
        role="Researcher",
        papers=["paper_1"],
    )
    post = pre.model_copy(update={"affiliation": "Analytical Engines Inc", "role": "Founder"})
    audit = build_enrichment_audit(
        run_id="run_test",
        mode=EnrichmentMode.SONAR,
        pre_researchers=[pre],
        post_researchers=[post],
        signals=[],
        targeted_ids={pre.id},
        investigated_ids={pre.id},
    )
    summary = summarize_enrichment_audit(audit)
    assert summary["enriched_profile_lines"] == [
        "Ada Lovelace — Unknown → Analytical Engines Inc — role: Researcher → Founder"
    ]
    assert summary["investigated_profile_names"] == ["Ada Lovelace"]


def test_serialize_enrichment_audit_roundtrip() -> None:
    pre = Researcher(
        id="researcher_a",
        name="Ada Lovelace",
        affiliation="Unknown",
        role="Researcher",
        papers=["paper_1"],
    )
    audit = build_enrichment_audit(
        run_id="run_test",
        mode=EnrichmentMode.NONE,
        pre_researchers=[pre],
        post_researchers=[pre],
        signals=[],
    )
    restored = deserialize_enrichment_audit(serialize_enrichment_audit(audit))
    assert restored.run_id == audit.run_id
    assert restored.total_researchers == 1
    assert restored.pre_researchers == audit.pre_researchers


def test_detect_signals_records_none_mode_audit() -> None:
    result = detect_signals(
        perplexity_config=PerplexityConfig(enabled=False),
        agentic_signal_config=AgenticSignalConfig(enabled=False),
        use_mock_signals=True,
    )
    assert result.enrichment_audit is not None
    assert result.enrichment_audit.mode == EnrichmentMode.NONE
    summary = summarize_enrichment_audit(result.enrichment_audit)
    assert summary["available"] is True


def test_save_and_load_enrichment_audit(tmp_path) -> None:
    result = run_reports(include_clusters=False)
    detection = result.scoring.detection
    from app.enrichment_audit import build_enrichment_audit

    audit = build_enrichment_audit(
        run_id="run_store_test",
        mode=EnrichmentMode.NONE,
        pre_researchers=detection.researchers,
        post_researchers=detection.researchers,
        signals=detection.signals,
    )
    db_path = tmp_path / "lab2startup.db"
    save_run_snapshot("run_store_test", result, db_path=db_path)
    save_enrichment_audit("run_store_test", audit, db_path=db_path)

    loaded = load_enrichment_audit("run_store_test", db_path=db_path)
    assert loaded is not None
    assert loaded.run_id == "run_store_test"
    assert loaded.total_researchers == len(detection.researchers)


def test_rerun_enrichment_without_api_key_reports_error() -> None:
    pre = Researcher(
        id="researcher_a",
        name="Ada Lovelace",
        affiliation="Unknown",
        role="Researcher",
        papers=["paper_1"],
    )
    audit = build_enrichment_audit(
        run_id="run_test",
        mode=EnrichmentMode.SONAR,
        pre_researchers=[pre],
        post_researchers=[pre],
        signals=[],
    )
    rerun = rerun_enrichment_verification(
        run_id="run_test",
        papers=[],
        audit=audit,
        perplexity_config=PerplexityConfig(enabled=False),
    )
    assert rerun.mode == EnrichmentMode.NONE
    assert rerun.config_summary.get("error")
