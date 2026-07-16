"""Fund profile loading and fund-scoped conference logic (Step 15)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from app.models import Paper

FUNDS_DIR = Path(__file__).resolve().parents[1] / "funds"
DEFAULT_FUND_ID = "default"


@dataclass(frozen=True)
class ThesisFitConfig:
    """Rule and Sonar parameters for fund thesis fit."""

    europe_regions: tuple[str, ...]
    infra_keywords: tuple[str, ...]
    application_keywords: tuple[str, ...]
    hard_exclude_keywords: tuple[str, ...] = ()
    sonar_min_score: int = 60
    sonar_max_calls: int = 30


@dataclass(frozen=True)
class FundConference:
    """A conference a fund monitors."""

    name: str
    sources: tuple[str, ...] = ("openreview", "openalex")
    priority: str = "medium"


@dataclass(frozen=True)
class FundProfile:
    """Investment thesis and sourcing scope for a VC fund."""

    id: str
    name: str
    description: str
    conferences: tuple[FundConference, ...]
    topic_keywords: tuple[str, ...] = ()
    exclude_topic_keywords: tuple[str, ...] = ()
    topic_scores: dict[str, int] = field(default_factory=dict)
    perplexity_context: str = ""
    default_paper_source: str = "openreview"
    thesis_fit: ThesisFitConfig | None = None

    @property
    def conference_names(self) -> list[str]:
        return [conference.name for conference in self.conferences]

    def conference(self, name: str) -> FundConference | None:
        normalized = name.strip().lower()
        for conference in self.conferences:
            if conference.name.lower() == normalized:
                return conference
        return None

    def conferences_with_priority(self, priority: str) -> tuple[FundConference, ...]:
        normalized = priority.strip().lower()
        return tuple(conference for conference in self.conferences if conference.priority.lower() == normalized)

    @property
    def high_priority_conferences(self) -> list[str]:
        return [conference.name for conference in self.conferences_with_priority("high")]

    def conference_label(self, name: str) -> str:
        """Human-readable label with paper source hint."""
        entry = self.conference(name)
        if entry is None:
            return name
        sources = "/".join(entry.sources)
        return f"{entry.name} ({sources}, {entry.priority})"

    def supports_source(self, conference: str, paper_source: str) -> bool:
        entry = self.conference(conference)
        if entry is None:
            return False
        return paper_source in entry.sources

    def default_source_for(self, conference: str) -> str:
        entry = self.conference(conference)
        if entry is None:
            return self.default_paper_source
        if "openreview" in entry.sources:
            return "openreview"
        if "openalex" in entry.sources:
            return "openalex"
        return self.default_paper_source


def _parse_conferences(raw: list[dict[str, object]] | None) -> tuple[FundConference, ...]:
    conferences: list[FundConference] = []
    for item in raw or []:
        sources = item.get("sources") or ["openreview", "openalex"]
        conferences.append(
            FundConference(
                name=str(item["name"]),
                sources=tuple(str(source) for source in sources),
                priority=str(item.get("priority", "medium")),
            )
        )
    return tuple(conferences)


def load_fund_profile(fund_id: str, *, funds_dir: Path | None = None) -> FundProfile:
    """Load a fund profile YAML from the funds/ directory."""
    directory = funds_dir or FUNDS_DIR
    path = directory / f"{fund_id}.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"Fund profile not found: {path}")

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Invalid fund profile: {path}")

    topic_scores = data.get("topic_scores") or {}
    if not isinstance(topic_scores, dict):
        raise ValueError(f"topic_scores must be a mapping in {path}")

    exclude_keywords = tuple(str(item) for item in data.get("exclude_topic_keywords") or [])
    thesis_fit_raw = data.get("thesis_fit")
    thesis_fit: ThesisFitConfig | None = None
    if isinstance(thesis_fit_raw, dict):
        hard_exclude = thesis_fit_raw.get("hard_exclude_keywords") or list(exclude_keywords)
        thesis_fit = ThesisFitConfig(
            europe_regions=tuple(str(item) for item in thesis_fit_raw.get("europe_regions") or []),
            infra_keywords=tuple(str(item) for item in thesis_fit_raw.get("infra_keywords") or []),
            application_keywords=tuple(str(item) for item in thesis_fit_raw.get("application_keywords") or []),
            hard_exclude_keywords=tuple(str(item) for item in hard_exclude),
            sonar_min_score=int(thesis_fit_raw.get("sonar_min_score", 60)),
            sonar_max_calls=int(thesis_fit_raw.get("sonar_max_calls", 30)),
        )

    return FundProfile(
        id=str(data.get("id", fund_id)),
        name=str(data.get("name", fund_id)),
        description=str(data.get("description", "")).strip(),
        conferences=_parse_conferences(data.get("conferences")),
        topic_keywords=tuple(str(item) for item in data.get("topic_keywords") or []),
        exclude_topic_keywords=exclude_keywords,
        topic_scores={str(key): int(value) for key, value in topic_scores.items()},
        perplexity_context=str(data.get("perplexity_context", "")).strip(),
        default_paper_source=str(data.get("default_paper_source", "openreview")),
        thesis_fit=thesis_fit,
    )


def list_fund_profiles(*, funds_dir: Path | None = None) -> list[str]:
    """Return available fund profile IDs."""
    directory = funds_dir or FUNDS_DIR
    if not directory.is_dir():
        return []
    return sorted(path.stem for path in directory.glob("*.yaml"))


def load_default_fund_profile() -> FundProfile:
    """Load the default fund profile."""
    return load_fund_profile(DEFAULT_FUND_ID)


def validate_conference_for_fund(conference: str, fund: FundProfile) -> FundConference:
    """Ensure a conference is in scope for the fund."""
    entry = fund.conference(conference)
    if entry is None:
        allowed = ", ".join(fund.conference_names)
        raise ValueError(f"Conference '{conference}' is not in scope for {fund.name}. Allowed conferences: {allowed}")
    return entry


def resolve_paper_source_for_fund(
    *,
    conference: str,
    fund: FundProfile,
    requested_source: str | None,
) -> str:
    """Pick a paper source compatible with the fund conference."""
    entry = validate_conference_for_fund(conference, fund)
    if requested_source:
        if requested_source not in entry.sources:
            allowed = ", ".join(entry.sources)
            raise ValueError(
                f"Paper source '{requested_source}' is not supported for {conference} "
                f"under {fund.name}. Supported: {allowed}"
            )
        return requested_source
    return fund.default_source_for(conference)


def _paper_text(paper: Paper) -> str:
    return " ".join(part for part in (paper.title, paper.abstract, paper.topic) if part).lower()


def paper_matches_fund(paper: Paper, fund: FundProfile) -> bool:
    """Return True when a paper fits the fund's topic scope."""
    text = _paper_text(paper)
    if fund.exclude_topic_keywords and any(keyword.lower() in text for keyword in fund.exclude_topic_keywords):
        return False
    if not fund.topic_keywords:
        return True
    return any(keyword.lower() in text for keyword in fund.topic_keywords)


def filter_papers_for_fund(papers: list[Paper], fund: FundProfile) -> list[Paper]:
    """Keep papers aligned with the fund thesis (soft conference-level filter)."""
    if not fund.topic_keywords:
        return papers
    return [paper for paper in papers if paper_matches_fund(paper, fund)]


def applied_topic_scores_for_fund(fund: FundProfile | None) -> dict[str, int]:
    """Return topic score overrides for scoring."""
    if fund is None or not fund.topic_scores:
        return {}
    return dict(fund.topic_scores)


def resolve_conference_list(
    fund: FundProfile,
    *,
    conferences: list[str] | None = None,
    priority: str | None = None,
) -> list[str]:
    """Resolve which conferences to run for a fund."""
    if conferences:
        for name in conferences:
            validate_conference_for_fund(name, fund)
        return conferences
    if priority:
        selected = fund.conferences_with_priority(priority)
        if not selected:
            raise ValueError(f"No conferences with priority '{priority}' for {fund.name}")
        return [conference.name for conference in selected]
    raise ValueError("Provide conferences or a priority filter")
