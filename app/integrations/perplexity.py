"""Perplexity integration — web search for founder and commercialization signals (Step 10e)."""

from __future__ import annotations

import argparse
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import urlparse

import httpx

from app.agents.ingestion_agent import make_researcher_id
from app.models import (
    EvidenceStrength,
    IdentityConfidence,
    Paper,
    Researcher,
    Signal,
    SignalType,
)
from app.researcher_links import normalize_github_profile_url, normalize_linkedin_profile_url

PERPLEXITY_API_BASE = "https://api.perplexity.ai"
DEFAULT_MODEL = "sonar-pro"
DEFAULT_USER_AGENT = "Lab2Startup/0.1"

SIGNAL_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "profile": {
            "type": "object",
            "properties": {
                "affiliation": {"type": "string"},
                "role": {"type": "string"},
                "identity_confidence": {
                    "type": "string",
                    "enum": ["high", "medium", "low"],
                },
                "profile_url": {"type": "string"},
                "linkedin_url": {"type": "string"},
                "github_url": {"type": "string"},
                "identity_explanation": {"type": "string"},
            },
            "required": [
                "affiliation",
                "role",
                "identity_confidence",
                "profile_url",
                "identity_explanation",
            ],
            "additionalProperties": False,
        },
        "signals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "signal_type": {
                        "type": "string",
                        "enum": [
                            "confirmed_founder",
                            "possible_founder",
                            "commercialization",
                            "no_signal",
                        ],
                    },
                    "description": {"type": "string"},
                    "source_url": {"type": "string"},
                    "evidence_strength": {
                        "type": "string",
                        "enum": ["high", "medium", "low"],
                    },
                },
                "required": [
                    "signal_type",
                    "description",
                    "source_url",
                    "evidence_strength",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["profile", "signals"],
    "additionalProperties": False,
}


@dataclass
class PerplexityConfig:
    """Parameters for Perplexity founder-signal detection."""

    enabled: bool = True
    api_key: str | None = None
    model: str = DEFAULT_MODEL
    max_researchers: int = 0  # 0 = investigate all researchers in the run
    max_signals_per_researcher: int = 2
    min_identity_confidence: IdentityConfidence = IdentityConfidence.HIGH
    supplement_mock_signals: bool = False
    request_delay_seconds: float = 1.0
    max_workers: int = 3
    fund_context: str | None = None
    enrich_profiles: bool = True


def _is_valid_http_url(value: str) -> bool:
    try:
        parsed = urlparse(value.strip())
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _extract_json_object(text: str) -> dict[str, Any]:
    """Parse JSON from model output, tolerating fenced code blocks."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        payload = json.loads(cleaned)
        if isinstance(payload, dict):
            return payload
    except json.JSONError:
        pass

    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if match:
        payload = json.loads(match.group(0))
        if isinstance(payload, dict):
            return payload

    raise ValueError("Perplexity response did not contain valid JSON.")


def build_researcher_context(
    researcher: Researcher,
    papers_by_id: dict[str, Paper],
    *,
    fund_context: str | None = None,
) -> dict[str, Any]:
    """Build a compact context bundle for a Perplexity query."""
    paper_records = []
    for paper_id in researcher.papers:
        paper = papers_by_id.get(paper_id)
        if paper is None:
            continue
        paper_records.append(
            {
                "title": paper.title,
                "topic": paper.topic,
                "conference": f"{paper.conference} {paper.year}",
            }
        )

    return {
        "name": researcher.name,
        "affiliation": researcher.affiliation,
        "role": researcher.role,
        "identity_confidence": researcher.identity_confidence.value,
        "papers": paper_records,
        "openreview_url": researcher.openreview_url,
        "github_username": researcher.github_username,
        "fund_context": fund_context,
    }


def build_founder_search_prompt(context: dict[str, Any]) -> str:
    """Create the user prompt for Perplexity web search."""
    paper_lines = [
        f"- {paper['title']} ({paper['conference']}, topic: {paper['topic']})" for paper in context.get("papers") or []
    ]
    paper_block = "\n".join(paper_lines) if paper_lines else "- No paper titles available."

    profile_lines = []
    if context.get("openreview_url"):
        profile_lines.append(f"OpenReview: {context['openreview_url']}")
    if context.get("github_username"):
        profile_lines.append(f"GitHub: https://github.com/{context['github_username']}")
    profile_block = "\n".join(profile_lines) if profile_lines else "No profile URLs on file."

    fund_block = ""
    if context.get("fund_context"):
        fund_block = f"Fund investment focus:\n{context['fund_context']}\n\n"

    return (
        "You are helping a deep-tech VC source potential academic founders.\n\n"
        f"{fund_block}"
        f"Researcher: {context['name']}\n"
        f"Known affiliation (may be missing): {context['affiliation']}\n"
        f"Known role: {context['role']}\n\n"
        f"Conference papers:\n{paper_block}\n\n"
        f"Known profiles:\n{profile_block}\n\n"
        "Step 1 — Resolve this exact person on the public web:\n"
        "- Find their current affiliation and role (university, lab, PhD/postdoc/faculty).\n"
        "- Set identity_confidence to high only when name + affiliation + papers clearly match "
        "one person; medium if plausible but ambiguous; low if uncertain.\n"
        "- profile_url should be their best primary page (personal site, lab page, Google Scholar, "
        "LinkedIn, or OpenReview) — use a real URL you found.\n\n"
        "Step 2 — Search for founder/commercialization evidence for THIS SAME PERSON:\n"
        "- founding or co-founding a startup,\n"
        "- commercializing research via a product or company website,\n"
        "- or actively building a public project with commercial potential.\n\n"
        "Rules:\n"
        "- Only include evidence that plausibly refers to this exact person.\n"
        "- Prefer primary sources: company sites, personal pages, news, LinkedIn, Crunchbase.\n"
        "- Do NOT invent URLs. Use real pages you found in search.\n"
        "- If no credible founder signal exists, return one signals item with signal_type=no_signal, "
        "source_url=https://example.com/no-signal, evidence_strength=low, "
        "and description explaining no public founder signal was found.\n"
        "- Return at most 2 signals, ranked by relevance.\n"
        "Return JSON matching the provided schema (profile + signals)."
    )


def _map_signal_type(raw: str) -> SignalType:
    try:
        return SignalType(raw)
    except ValueError:
        return SignalType.COMMERCIALIZATION


def _map_evidence_strength(raw: str) -> EvidenceStrength:
    try:
        return EvidenceStrength(raw)
    except ValueError:
        return EvidenceStrength.LOW


def _pick_source_url(
    candidate_url: str,
    citations: list[str],
) -> str | None:
    """Prefer citation URLs from Perplexity over model-generated links."""
    candidate = candidate_url.strip()
    if _is_valid_http_url(candidate) and candidate not in {
        "https://example.com/no-signal",
        "http://example.com/no-signal",
    }:
        normalized = candidate.rstrip("/")
        citation_set = {url.rstrip("/") for url in citations if _is_valid_http_url(url)}
        if normalized in citation_set or not citations:
            return candidate
        for citation in citations:
            if citation.rstrip("/") == normalized:
                return citation

    for citation in citations:
        if _is_valid_http_url(citation):
            return citation
    return None


def _map_identity_confidence(raw: str) -> IdentityConfidence:
    try:
        return IdentityConfidence(raw)
    except ValueError:
        return IdentityConfidence.MEDIUM


def parse_perplexity_profile(
    payload: dict[str, Any],
    *,
    researcher: Researcher,
    citations: list[str],
) -> Researcher:
    """Apply Perplexity profile resolution onto a researcher record."""
    profile = payload.get("profile")
    if not isinstance(profile, dict):
        return researcher

    affiliation = str(profile.get("affiliation") or "").strip()
    role = str(profile.get("role") or "").strip()
    confidence = _map_identity_confidence(str(profile.get("identity_confidence", "medium")))
    explanation = str(profile.get("identity_explanation") or "").strip()
    profile_url = _pick_source_url(str(profile.get("profile_url") or ""), citations)
    linkedin_url = normalize_linkedin_profile_url(str(profile.get("linkedin_url") or ""))
    if linkedin_url is None:
        for candidate in (profile_url, *citations):
            linkedin_url = normalize_linkedin_profile_url(str(candidate or ""))
            if linkedin_url:
                break

    github_url = normalize_github_profile_url(str(profile.get("github_url") or ""))
    if github_url is None:
        for candidate in (profile_url, *citations):
            github_url = normalize_github_profile_url(str(candidate or ""))
            if github_url:
                break

    updates: dict[str, Any] = {
        "identity_confidence": confidence,
        "identity_confidence_explanation": explanation
        or f"Perplexity web search resolved profile for {researcher.name}.",
    }

    if affiliation and affiliation.lower() != "unknown":
        updates["affiliation"] = affiliation[:200]
    if role and role.lower() != "researcher" or role:
        updates["role"] = role[:120]

    if profile_url and "openreview.net/profile" in profile_url.lower():
        profile_id = profile_url.split("id=", 1)[-1]
        updates["openreview_url"] = profile_url
        if profile_id:
            updates["openreview_profile_id"] = profile_id
    elif profile_url:
        updates["profile_url"] = profile_url

    if linkedin_url:
        updates["linkedin_url"] = linkedin_url
    if github_url:
        login = github_url.rstrip("/").rsplit("/", 1)[-1]
        updates["github_username"] = login

    return researcher.model_copy(update=updates)


def parse_perplexity_signals(
    payload: dict[str, Any],
    *,
    researcher: Researcher,
    citations: list[str],
    max_signals: int,
    signal_id_prefix: str = "perplexity",
) -> list[Signal]:
    """Convert a Perplexity JSON payload into Signal objects."""
    raw_signals = payload.get("signals") or []
    if not isinstance(raw_signals, list):
        return []

    slug = make_researcher_id(researcher.name).removeprefix("researcher_")
    signals: list[Signal] = []

    for index, item in enumerate(raw_signals[:max_signals]):
        if not isinstance(item, dict):
            continue

        signal_type = _map_signal_type(str(item.get("signal_type", "commercialization")))
        if signal_type == SignalType.NO_SIGNAL:
            continue

        description = str(item.get("description") or "").strip()
        if not description:
            continue

        source_url = _pick_source_url(str(item.get("source_url") or ""), citations)
        if source_url is None:
            continue

        signal_id = f"{signal_id_prefix}_{slug}_{index + 1}"
        signals.append(
            Signal(
                id=signal_id,
                signal_type=signal_type,
                description=description[:500],
                source_url=source_url,
                evidence_strength=_map_evidence_strength(str(item.get("evidence_strength", "medium"))),
                date_found=date.today(),
                researcher_name=researcher.name,
            )
        )

    return signals


class PerplexityClient:
    """Minimal Perplexity Sonar API client."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_MODEL,
        timeout: float = 120.0,
        request_delay_seconds: float = 1.0,
    ) -> None:
        self.model = model
        self.request_delay_seconds = request_delay_seconds
        self._client = httpx.Client(
            base_url=PERPLEXITY_API_BASE,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": DEFAULT_USER_AGENT,
            },
            timeout=timeout,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> PerplexityClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _pause(self) -> None:
        if self.request_delay_seconds:
            time.sleep(self.request_delay_seconds)

    def search_researcher_intel(self, context: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        """Run a structured Sonar query for profile resolution and founder signals."""
        prompt = build_founder_search_prompt(context)
        response = self._client.post(
            "/v1/sonar",
            json={
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "researcher_intel",
                        "schema": SIGNAL_RESPONSE_SCHEMA,
                    },
                },
            },
        )
        response.raise_for_status()
        self._pause()

        body = response.json()
        citations = [str(url) for url in body.get("citations") or [] if url]
        choices = body.get("choices") or []
        if not choices:
            raise ValueError("Perplexity response contained no choices.")

        content = choices[0].get("message", {}).get("content") or ""
        parsed = _extract_json_object(content)
        return parsed, citations

    def search_founder_signals(self, context: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        """Backward-compatible alias for researcher intel queries."""
        return self.search_researcher_intel(context)


def _target_researchers_for_perplexity(
    researchers: list[Researcher],
    config: PerplexityConfig,
) -> list[Researcher]:
    """Select researchers for Perplexity enrichment (all when max_researchers <= 0)."""
    ranked = sorted(researchers, key=lambda researcher: (-len(researcher.papers), researcher.name))
    if config.max_researchers <= 0:
        return ranked
    return ranked[: config.max_researchers]


def _query_researcher_intel(
    researcher: Researcher,
    papers_by_id: dict[str, Paper],
    config: PerplexityConfig,
) -> tuple[Researcher, list[Signal]]:
    """Run one Perplexity query for profile resolution and founder signals."""
    context = build_researcher_context(
        researcher,
        papers_by_id,
        fund_context=config.fund_context,
    )
    with PerplexityClient(
        api_key=config.api_key or "",
        model=config.model,
        request_delay_seconds=config.request_delay_seconds,
    ) as client:
        try:
            payload, citations = client.search_researcher_intel(context)
        except (httpx.HTTPError, ValueError, json.JSONDecodeError):
            return researcher, []

        updated = (
            parse_perplexity_profile(payload, researcher=researcher, citations=citations)
            if config.enrich_profiles
            else researcher
        )
        signals = parse_perplexity_signals(
            payload,
            researcher=updated,
            citations=citations,
            max_signals=config.max_signals_per_researcher,
        )
        return updated, signals


def apply_perplexity_researcher_updates(
    researchers: list[Researcher],
    updates_by_id: dict[str, Researcher],
) -> list[Researcher]:
    """Merge Perplexity profile updates back into the full researcher list."""
    if not updates_by_id:
        return researchers
    return [updates_by_id.get(researcher.id, researcher) for researcher in researchers]


def enrich_researchers_with_perplexity(
    papers: list[Paper],
    researchers: list[Researcher],
    config: PerplexityConfig,
) -> tuple[list[Researcher], list[Signal]]:
    """Resolve affiliations and founder signals via Perplexity web search."""
    if not config.enabled or not config.api_key or not researchers:
        return researchers, []

    papers_by_id = {paper.id: paper for paper in papers}
    targets = _target_researchers_for_perplexity(researchers, config)
    updates_by_id: dict[str, Researcher] = {}
    signals: list[Signal] = []
    seen_urls: set[str] = set()

    worker_count = max(1, min(config.max_workers, len(targets)))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {
            executor.submit(_query_researcher_intel, researcher, papers_by_id, config): researcher
            for researcher in targets
        }
        for future in as_completed(futures):
            try:
                updated_researcher, researcher_signals = future.result()
            except Exception:
                continue

            updates_by_id[updated_researcher.id] = updated_researcher
            for signal in researcher_signals:
                url = signal.source_url.rstrip("/")
                if url in seen_urls:
                    continue
                signals.append(signal)
                seen_urls.add(url)

    return apply_perplexity_researcher_updates(researchers, updates_by_id), signals


def _query_researcher_signals(
    researcher: Researcher,
    papers_by_id: dict[str, Paper],
    config: PerplexityConfig,
) -> list[Signal]:
    """Run one Perplexity query for a single researcher (signals only)."""
    _, signals = _query_researcher_intel(researcher, papers_by_id, config)
    return signals


def detect_perplexity_signals(
    papers: list[Paper],
    researchers: list[Researcher],
    config: PerplexityConfig,
) -> list[Signal]:
    """Query Perplexity for founder signals on selected researchers."""
    _, signals = enrich_researchers_with_perplexity(papers, researchers, config)
    return signals


def merge_perplexity_signals(
    existing_signals: list[Signal],
    perplexity_signals: list[Signal],
) -> list[Signal]:
    """Append Perplexity signals without duplicating source URLs."""
    seen_urls = {signal.source_url.rstrip("/") for signal in existing_signals}
    merged = list(existing_signals)
    for signal in perplexity_signals:
        url = signal.source_url.rstrip("/")
        if url in seen_urls:
            continue
        merged.append(signal)
        seen_urls.add(url)
    return merged


def summarize_perplexity_signals(signals: list[Signal]) -> dict[str, object]:
    """Return quick stats for Perplexity signal detection."""
    perplexity_signals = [signal for signal in signals if signal.id.startswith("perplexity_")]
    return {
        "perplexity_signal_count": len(perplexity_signals),
        "researchers_with_perplexity_signals": len(
            {signal.researcher_name for signal in perplexity_signals if signal.researcher_name}
        ),
        "sample_signals": [
            {
                "id": signal.id,
                "researcher_name": signal.researcher_name,
                "signal_type": signal.signal_type.value,
                "source_url": signal.source_url,
                "evidence_strength": signal.evidence_strength.value,
            }
            for signal in perplexity_signals[:5]
        ],
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Search Perplexity for founder signals on a researcher.")
    parser.add_argument("--name", required=True)
    parser.add_argument("--affiliation", default="Unknown")
    parser.add_argument("--role", default="Researcher")
    parser.add_argument("--paper-title")
    parser.add_argument("--api-key")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    import os

    api_key = args.api_key or os.getenv("LAB2STARTUP_PERPLEXITY_API_KEY")
    if not api_key:
        raise SystemExit("Set --api-key or LAB2STARTUP_PERPLEXITY_API_KEY.")

    paper = Paper(
        id="paper_cli",
        title=args.paper_title or "Research paper",
        conference="NeurIPS",
        year=2024,
        topic="AI",
        abstract="",
        authors=[],
    )
    researcher = Researcher(
        id=make_researcher_id(args.name),
        name=args.name,
        affiliation=args.affiliation,
        role=args.role,
        papers=[paper.id],
        identity_confidence=IdentityConfidence.HIGH,
    )
    config = PerplexityConfig(
        enabled=True,
        api_key=api_key,
        model=args.model,
        max_researchers=1,
    )
    signals = detect_perplexity_signals([paper], [researcher], config)
    print(json.dumps(summarize_perplexity_signals(signals), indent=2))
    for signal in signals:
        print(json.dumps(signal.model_dump(mode="json"), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
