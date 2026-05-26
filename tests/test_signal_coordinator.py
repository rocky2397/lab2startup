"""Tests for agentic signal coordinator prefilter and tiers."""

from __future__ import annotations

from pathlib import Path
from app.agents.signal_coordinator import (
    assign_tier,
    build_investigation_plan,
    compute_prefilter_score,
    evaluate_continue,
    should_early_exit,
)
from app.config import AgenticSignalConfig
from app.models import EvidenceStrength, IdentityConfidence, Paper, Researcher, Signal, SignalType


def _researcher(
    *,
    researcher_id: str,
    name: str,
    paper_count: int = 2,
    confidence: IdentityConfidence = IdentityConfidence.HIGH,
) -> Researcher:
    return Researcher(
        id=researcher_id,
        name=name,
        affiliation="Stanford",
        role="PhD Student",
        papers=[f"paper_{index}" for index in range(paper_count)],
        identity_confidence=confidence,
    )


def test_compute_prefilter_score_orders_by_papers() -> None:
    paper = Paper(
        id="paper_0",
        title="Test",
        conference="NeurIPS",
        year=2024,
        topic="AI agents",
        abstract="",
        authors=[],
    )
    high = _researcher(researcher_id="r_high", name="High Papers", paper_count=4)
    low = _researcher(researcher_id="r_low", name="Low Papers", paper_count=1)
    high_score = compute_prefilter_score(high, papers_by_id={paper.id: paper}, topic_scores={"AI agents": 20})
    low_score = compute_prefilter_score(low, papers_by_id={paper.id: paper}, topic_scores={"AI agents": 20})
    assert high_score > low_score


def test_assign_tier_deep_standard_light() -> None:
    assert assign_tier(1, _researcher(researcher_id="r1", name="A"), deep_slots=3, standard_slots=7, prefilter_score=50, prefilter_min_score=20) == "deep"
    assert assign_tier(5, _researcher(researcher_id="r5", name="B"), deep_slots=3, standard_slots=7, prefilter_score=50, prefilter_min_score=20) == "standard"
    assert assign_tier(12, _researcher(researcher_id="r12", name="C", confidence=IdentityConfidence.MEDIUM), deep_slots=3, standard_slots=7, prefilter_score=50, prefilter_min_score=20) == "light"
    assert assign_tier(1, _researcher(researcher_id="r_skip", name="D"), deep_slots=3, standard_slots=7, prefilter_score=10, prefilter_min_score=20) == "skip"


def test_build_investigation_plan_respects_max_calls() -> None:
    researchers = [
        _researcher(researcher_id=f"r_{index}", name=f"Researcher {index}", paper_count=index + 1)
        for index in range(5)
    ]
    config = AgenticSignalConfig(enabled=True, max_agent_calls=2, queue_reserve=0)
    scores, queue, tiers = build_investigation_plan(researchers, papers_by_id={}, config=config)
    assert len(queue) <= 2
    assert all(tiers[researcher_id] != "skip" for researcher_id in queue)
    assert scores


def test_should_early_exit_on_high_confirmed_founder() -> None:
    signals = [
        Signal(
            id="agent_test_1",
            signal_type=SignalType.CONFIRMED_FOUNDER,
            description="Founded startup",
            source_url="https://example.com/startup",
            evidence_strength=EvidenceStrength.HIGH,
            date_found="2025-05-22",
            researcher_name="Jane Doe",
        )
    ]
    assert should_early_exit(signals, enabled=True)


def test_should_early_exit_on_three_strong_signals() -> None:
    signals = [
        Signal(
            id=f"agent_test_{index}",
            signal_type=SignalType.POSSIBLE_FOUNDER,
            description="Stealth mention",
            source_url=f"https://example.com/{index}",
            evidence_strength=EvidenceStrength.MEDIUM,
            date_found="2025-05-22",
            researcher_name=f"Researcher {index}",
        )
        for index in range(3)
    ]
    assert should_early_exit(signals, enabled=True)
    assert not should_early_exit(signals[:2], enabled=True)


def test_prefilter_score_uses_researcher_history(tmp_path: Path) -> None:
    from app.agent_trace_store import upsert_researcher_history, ResearcherHistoryRow

    db_path = tmp_path / "history.db"
    paper = Paper(
        id="paper_0",
        title="Test",
        conference="NeurIPS",
        year=2024,
        topic="AI agents",
        abstract="",
        authors=[],
    )
    researcher = _researcher(researcher_id="r_hist", name="History User", paper_count=1)
    base_score = compute_prefilter_score(
        researcher,
        papers_by_id={paper.id: paper},
        topic_scores={"AI agents": 10},
        db_path=db_path,
    )

    upsert_researcher_history(
        ResearcherHistoryRow(
            researcher_id=researcher.id,
            canonical_name=researcher.name,
            last_signal_count=3,
        ),
        db_path=db_path,
    )
    boosted = compute_prefilter_score(
        researcher,
        papers_by_id={paper.id: paper},
        topic_scores={"AI agents": 10},
        db_path=db_path,
    )
    assert boosted > base_score


def test_evaluate_continue_stops_on_early_exit() -> None:
    signals = [
        Signal(
            id="agent_exit_1",
            signal_type=SignalType.CONFIRMED_FOUNDER,
            description="Founded startup",
            source_url="https://example.com/startup",
            evidence_strength=EvidenceStrength.HIGH,
            date_found="2025-05-22",
            researcher_name="Jane Doe",
        )
    ]
    result = evaluate_continue(
        {
            "investigation_queue": ["researcher_next"],
            "agent_calls_used": 1,
            "max_agent_calls": 10,
            "steps_used_total": 3,
            "max_total_steps": 40,
            "signals": signals,
            "early_exit_enabled": True,
        }
    )
    assert result["should_continue"] is False
    assert result["stop_reason"] == "early_exit_high_signal"


def test_evaluate_continue_stops_on_empty_queue() -> None:
    result = evaluate_continue(
        {
            "investigation_queue": [],
            "agent_calls_used": 0,
            "max_agent_calls": 10,
            "steps_used_total": 0,
            "max_total_steps": 40,
            "signals": [],
            "early_exit_enabled": True,
        }
    )
    assert result["should_continue"] is False
    assert result["stop_reason"] == "queue_empty"
