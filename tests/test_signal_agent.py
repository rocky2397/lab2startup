"""Tests for mock signal detection (Step 5)."""

from app.agents.signal_agent import (
    attach_signals,
    detect_signals,
    group_signals_by_cluster,
    group_signals_by_researcher,
    summarize_signal_detection,
)
from app.models import SignalType
from app.schemas import load_signals


def test_detect_signals_matches_researchers() -> None:
    result = detect_signals()
    summary = summarize_signal_detection(result)

    assert summary["signal_count"] == 9
    assert summary["matched_signal_count"] == 9
    assert summary["unmatched_researcher_names"] == []
    assert summary["researchers_with_signals"] == 9


def test_signal_gets_researcher_and_cluster_ids() -> None:
    result = detect_signals()

    john_signal = next(signal for signal in result.signals if signal.researcher_name == "John Yang")
    assert john_signal.researcher_id == "researcher_john_yang"
    assert john_signal.cluster_id is not None
    assert john_signal.signal_type == SignalType.COMMERCIALIZATION

    carlos_signal = next(signal for signal in result.signals if signal.researcher_name == "Carlos E. Jimenez")
    assert carlos_signal.researcher_id == "researcher_carlos_e_jimenez"
    assert john_signal.cluster_id == carlos_signal.cluster_id


def test_group_signals_by_researcher_and_cluster() -> None:
    result = detect_signals()
    by_researcher = group_signals_by_researcher(result.signals)
    by_cluster = group_signals_by_cluster(result.signals)

    assert len(by_researcher["researcher_marinka_zitnik"]) == 1
    assert by_researcher["researcher_marinka_zitnik"][0].signal_type == SignalType.CONFIRMED_FOUNDER
    assert len(by_cluster) >= 1


def test_unmatched_researcher_name_reported() -> None:
    from app.agents.profile_agent import build_profiles

    profile = build_profiles()
    raw_signals = load_signals()
    unknown = raw_signals[0].model_copy(update={"researcher_name": "Unknown Person"})
    resolved, unmatched = attach_signals([unknown], profile.researchers, profile.clusters)

    assert resolved[0].researcher_id is None
    assert unmatched == ["Unknown Person"]
