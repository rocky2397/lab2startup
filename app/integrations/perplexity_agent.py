"""Perplexity Agent API client for multi-step founder investigations."""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from app.integrations.agent_tools import AgentToolHandlers
from app.integrations.perplexity import (
    DEFAULT_USER_AGENT,
    PERPLEXITY_API_BASE,
    SIGNAL_RESPONSE_SCHEMA,
    _extract_json_object,
    build_founder_search_prompt,
    build_researcher_context,
    parse_perplexity_profile,
    parse_perplexity_signals,
)
from app.models import Researcher, Signal

InvestigationTier = Literal["skip", "light", "standard", "deep"]

TIER_MAX_STEPS: dict[InvestigationTier, int] = {
    "skip": 0,
    "light": 1,
    "standard": 3,
    "deep": 8,
}

TIER_PRESET: dict[InvestigationTier, str | None] = {
    "skip": None,
    "light": "fast-search",
    "standard": "pro-search",
    "deep": "deep-research",
}

TIER_SEARCH_CONTEXT: dict[InvestigationTier, str] = {
    "skip": "low",
    "light": "low",
    "standard": "medium",
    "deep": "high",
}

MAX_CUSTOM_TOOL_ROUNDS = 3
AGENT_INSTRUCTIONS = (
    "You are a VC sourcing analyst. Resolve identity first, then search for founder evidence. "
    "Use lookup_prior_run before web search when available. "
    "Return JSON matching the schema in your final message."
)

CUSTOM_TOOLS: list[dict[str, Any]] = [
    {"type": "people_search"},
    {
        "type": "web_search",
        "filters": {"search_recency_filter": "year"},
    },
    {"type": "fetch_url", "max_urls": 3},
    {
        "type": "function",
        "name": "github_repo_search",
        "description": "Search GitHub for repos linked to this researcher's papers or name",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "min_stars": {"type": "integer"},
            },
            "required": ["query"],
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "lookup_prior_run",
        "description": "Fetch prior Lab2Startup investigation results for this researcher from SQLite",
        "parameters": {
            "type": "object",
            "properties": {
                "researcher_name": {"type": "string"},
                "researcher_id": {"type": "string"},
            },
            "required": ["researcher_name"],
        },
        "strict": True,
    },
]


@dataclass
class AgentInvestigationConfig:
    """Per-investigation Agent API parameters."""

    model: str | None = None
    preset: str | None = "pro-search"
    max_steps: int = 3
    timeout: float = 180.0
    max_signals_per_researcher: int = 2
    enrich_profiles: bool = True
    fund_context: str | None = None


@dataclass
class AgentInvestigationResult:
    """Parsed outcome of one researcher investigation."""

    payload: dict[str, Any] | None
    citations: list[str]
    signals: list[Signal]
    researcher: Researcher
    status: Literal["completed", "failed", "skipped"]
    steps_used: int
    tool_calls_count: int
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float | None
    summary: str
    request_json: dict[str, Any]
    response_json: dict[str, Any] | None
    error_message: str | None = None


def tier_investigation_config(
    tier: InvestigationTier,
    *,
    preset_standard: str = "pro-search",
    preset_deep: str = "deep-research",
    model: str | None = None,
    max_signals_per_researcher: int = 2,
    enrich_profiles: bool = True,
    fund_context: str | None = None,
) -> AgentInvestigationConfig:
    """Map coordinator tier to Agent API caps."""
    if tier == "deep":
        preset = preset_deep
    elif tier == "standard":
        preset = preset_standard
    elif tier == "light":
        preset = TIER_PRESET["light"]
    else:
        preset = None
    return AgentInvestigationConfig(
        model=model,
        preset=preset,
        max_steps=TIER_MAX_STEPS[tier],
        max_signals_per_researcher=max_signals_per_researcher,
        enrich_profiles=enrich_profiles,
        fund_context=fund_context,
    )


def _extract_output_text(response: dict[str, Any]) -> str:
    chunks: list[str] = []
    for item in response.get("output") or []:
        if item.get("type") != "message":
            continue
        for block in item.get("content") or []:
            if block.get("type") == "output_text" and block.get("text"):
                chunks.append(str(block["text"]))
    return "\n".join(chunks).strip()


def _extract_citations(response: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for item in response.get("output") or []:
        item_type = item.get("type")
        if item_type == "search_results":
            for result in item.get("results") or []:
                url = str(result.get("url") or "").strip()
                if url and url not in seen:
                    seen.add(url)
                    urls.append(url)
        elif item_type == "fetch_url_results":
            for result in item.get("contents") or []:
                url = str(result.get("url") or "").strip()
                if url and url not in seen:
                    seen.add(url)
                    urls.append(url)
    return urls


def _count_tool_calls(response: dict[str, Any]) -> int:
    count = 0
    for item in response.get("output") or []:
        if item.get("type") in {"search_results", "fetch_url_results", "function_call"}:
            count += 1
    return count


def _extract_usage(response: dict[str, Any]) -> tuple[int, int, float | None]:
    usage = response.get("usage") or {}
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    cost_block = usage.get("cost") or {}
    total_cost = cost_block.get("total_cost")
    estimated = float(total_cost) if total_cost is not None else None
    return input_tokens, output_tokens, estimated


def _extract_function_calls(response: dict[str, Any]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for item in response.get("output") or []:
        if item.get("type") == "function_call":
            calls.append(item)
    return calls


def _build_request_body(
    context: dict[str, Any],
    *,
    tier: InvestigationTier,
    config: AgentInvestigationConfig,
) -> dict[str, Any]:
    prompt = build_founder_search_prompt(context)
    body: dict[str, Any] = {
        "input": prompt,
        "instructions": AGENT_INSTRUCTIONS,
        "max_steps": config.max_steps,
        "tools": _tools_for_tier(tier),
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "researcher_intel",
                "schema": SIGNAL_RESPONSE_SCHEMA,
            },
        },
    }
    if config.preset:
        body["preset"] = config.preset
    if config.model:
        body["model"] = config.model
    return body


def _tools_for_tier(tier: InvestigationTier) -> list[dict[str, Any]]:
    tools = []
    for tool in CUSTOM_TOOLS:
        if tool.get("type") == "web_search":
            tools.append(
                {
                    "type": "web_search",
                    "search_context_size": TIER_SEARCH_CONTEXT.get(tier, "medium"),
                    "filters": {"search_recency_filter": "year"},
                }
            )
        else:
            tools.append(tool)
    return tools


def is_retriable_tier_http_error(error_message: str | None) -> bool:
    """True when standard/deep should retry once at light tier."""
    if not error_message:
        return False
    lowered = error_message.lower()
    return any(
        token in lowered
        for token in (
            "400",
            "422",
            "bad request",
            "unprocessable entity",
        )
    )


def should_fallback_to_light(tier: InvestigationTier, error_message: str | None) -> bool:
    """Standard/deep preset failures often succeed at fast-search."""
    return tier in {"standard", "deep"} and is_retriable_tier_http_error(error_message)


class PerplexityAgentClient:
    """Client for Perplexity Agent API (POST /v1/agent)."""

    def __init__(
        self,
        *,
        api_key: str,
        timeout: float = 180.0,
        request_delay_seconds: float = 1.5,
        max_retries: int = 2,
    ) -> None:
        self.request_delay_seconds = request_delay_seconds
        self.max_retries = max_retries
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

    def __enter__(self) -> PerplexityAgentClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _pause(self) -> None:
        if self.request_delay_seconds:
            time.sleep(self.request_delay_seconds)

    def _format_http_error(self, response: httpx.Response) -> str:
        try:
            payload = response.json()
            detail = json.dumps(payload)
        except (json.JSONDecodeError, ValueError):
            detail = response.text.strip() or response.reason_phrase
        return f"Agent API error {response.status_code}: {detail}"

    def _post_agent(self, body: dict[str, Any]) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self._client.post("/v1/agent", json=body)
                if response.status_code in {429, 500, 502, 503, 504} and attempt < self.max_retries:
                    time.sleep(2**attempt)
                    continue
                if response.status_code >= 400:
                    raise httpx.HTTPStatusError(
                        self._format_http_error(response),
                        request=response.request,
                        response=response,
                    )
                self._pause()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError("Agent API returned non-object JSON.")
                return payload
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    raise
                time.sleep(2**attempt)
        if last_error:
            raise last_error
        raise RuntimeError("Agent API request failed.")

    def _continue_with_tool_outputs(
        self,
        response: dict[str, Any],
        tool_handlers: AgentToolHandlers,
        *,
        base_body: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute pending custom function calls and continue the agent (max 3 round-trips)."""
        current = response
        for _ in range(MAX_CUSTOM_TOOL_ROUNDS):
            function_calls = _extract_function_calls(current)
            if not function_calls:
                return current

            outputs: list[dict[str, Any]] = []
            for call in function_calls:
                name = str(call.get("name") or "")
                raw_args = call.get("arguments") or "{}"
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                except json.JSONDecodeError:
                    args = {}
                result = tool_handlers.dispatch(name, args)
                outputs.append(
                    {
                        "type": "function_call_output",
                        "call_id": call.get("call_id") or call.get("id"),
                        "output": json.dumps(result),
                    }
                )

            continue_body = {
                **base_body,
                "previous_response_id": current.get("id"),
                "input": outputs,
            }
            current = self._post_agent(continue_body)
        return current

    def investigate_researcher(
        self,
        researcher: Researcher,
        papers_by_id: dict[str, Any],
        *,
        tier: InvestigationTier,
        config: AgentInvestigationConfig,
        tool_handlers: AgentToolHandlers,
        researchers_by_id: dict[str, Researcher] | None = None,
    ) -> AgentInvestigationResult:
        """Run a tiered Agent API investigation for one researcher."""
        if tier == "skip" or config.max_steps <= 0:
            return AgentInvestigationResult(
                payload=None,
                citations=[],
                signals=[],
                researcher=researcher,
                status="skipped",
                steps_used=0,
                tool_calls_count=0,
                input_tokens=0,
                output_tokens=0,
                estimated_cost_usd=None,
                summary="Skipped by coordinator tier.",
                request_json={},
                response_json=None,
            )

        context = build_researcher_context(
            researcher,
            papers_by_id,
            fund_context=config.fund_context,
            researchers_by_id=researchers_by_id,
        )
        request_body = _build_request_body(context, tier=tier, config=config)

        try:
            response = self._post_agent(request_body)
            response = self._continue_with_tool_outputs(
                response,
                tool_handlers,
                base_body=request_body,
            )
        except Exception as exc:
            return AgentInvestigationResult(
                payload=None,
                citations=[],
                signals=[],
                researcher=researcher,
                status="failed",
                steps_used=0,
                tool_calls_count=0,
                input_tokens=0,
                output_tokens=0,
                estimated_cost_usd=None,
                summary="Agent investigation failed.",
                request_json=_redact_request(request_body),
                response_json=None,
                error_message=str(exc),
            )

        citations = _extract_citations(response)
        text = _extract_output_text(response)
        input_tokens, output_tokens, estimated_cost = _extract_usage(response)
        tool_calls_count = _count_tool_calls(response)
        steps_used = int(response.get("max_steps") or config.max_steps)

        pending_calls = _extract_function_calls(response)
        if pending_calls:
            return AgentInvestigationResult(
                payload=None,
                citations=citations,
                signals=[],
                researcher=researcher,
                status="failed",
                steps_used=steps_used,
                tool_calls_count=tool_calls_count,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_cost_usd=estimated_cost,
                summary="Agent investigation ended with unresolved custom tool calls.",
                request_json=_redact_request(request_body),
                response_json=response,
                error_message=(
                    f"Unresolved custom tool calls after {MAX_CUSTOM_TOOL_ROUNDS} round-trips: "
                    f"{', '.join(str(call.get('name') or 'unknown') for call in pending_calls)}"
                ),
            )

        if not text:
            return AgentInvestigationResult(
                payload=None,
                citations=citations,
                signals=[],
                researcher=researcher,
                status="failed",
                steps_used=steps_used,
                tool_calls_count=tool_calls_count,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_cost_usd=estimated_cost,
                summary="Agent response did not contain output text.",
                request_json=_redact_request(request_body),
                response_json=response,
                error_message="Agent response did not contain output text.",
            )

        try:
            payload = _extract_json_object(text)
        except (ValueError, json.JSONDecodeError) as exc:
            return AgentInvestigationResult(
                payload=None,
                citations=citations,
                signals=[],
                researcher=researcher,
                status="failed",
                steps_used=steps_used,
                tool_calls_count=tool_calls_count,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_cost_usd=estimated_cost,
                summary="Agent response did not contain valid JSON.",
                request_json=_redact_request(request_body),
                response_json=response,
                error_message=str(exc),
            )

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
            signal_id_prefix="agent",
        )

        return AgentInvestigationResult(
            payload=payload,
            citations=citations,
            signals=signals,
            researcher=updated,
            status="completed",
            steps_used=steps_used,
            tool_calls_count=tool_calls_count,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=estimated_cost,
            summary=f"Investigated {researcher.name} ({tier}, {len(signals)} signals).",
            request_json=_redact_request(request_body),
            response_json=response,
        )


def _redact_request(body: dict[str, Any]) -> dict[str, Any]:
    return dict(body)


def merge_agent_signals(
    existing_signals: list[Signal],
    agent_signals: list[Signal],
) -> list[Signal]:
    """Append agent signals without duplicating source URLs."""
    seen_urls = {signal.source_url.rstrip("/") for signal in existing_signals}
    merged = list(existing_signals)
    for signal in agent_signals:
        url = signal.source_url.rstrip("/")
        if url in seen_urls:
            continue
        merged.append(signal)
        seen_urls.add(url)
    return merged


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Probe Perplexity Agent API for a single researcher investigation.")
    parser.add_argument("--name", required=True, help="Researcher full name")
    parser.add_argument("--affiliation", default="Unknown")
    parser.add_argument("--role", default="Researcher")
    parser.add_argument("--paper-title", help="Representative paper title for context")
    parser.add_argument(
        "--tier",
        choices=["light", "standard", "deep"],
        default="standard",
        help="Investigation tier (maps to preset and max_steps)",
    )
    parser.add_argument("--api-key", help="Override LAB2STARTUP_PERPLEXITY_API_KEY")
    parser.add_argument(
        "--db-path",
        help="SQLite path for lookup_prior_run custom tool (default: LAB2STARTUP_DB_PATH)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    import os
    from pathlib import Path

    from app.agents.ingestion_agent import make_researcher_id
    from app.config import get_settings
    from app.integrations.agent_tools import AgentToolHandlers
    from app.models import IdentityConfidence, Paper

    parser = build_arg_parser()
    args = parser.parse_args(argv)

    api_key = args.api_key or os.getenv("LAB2STARTUP_PERPLEXITY_API_KEY")
    if not api_key:
        raise SystemExit("Set --api-key or LAB2STARTUP_PERPLEXITY_API_KEY.")

    settings = get_settings()
    db_path = Path(args.db_path) if args.db_path else settings.db_path

    paper = Paper(
        id="paper_cli",
        title=args.paper_title or "Research paper",
        conference="NeurIPS",
        year=2024,
        topic="AI agents",
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
    tier: InvestigationTier = args.tier
    config = tier_investigation_config(
        tier,
        fund_context=settings.perplexity_config.fund_context,
    )
    handlers = AgentToolHandlers(
        db_path=db_path,
        github_config=settings.github_config,
        run_id="cli_probe",
    )

    with PerplexityAgentClient(api_key=api_key, request_delay_seconds=0) as client:
        result = client.investigate_researcher(
            researcher,
            {paper.id: paper},
            tier=tier,
            config=config,
            tool_handlers=handlers,
        )

    output = {
        "status": result.status,
        "tier": tier,
        "steps_used": result.steps_used,
        "tool_calls_count": result.tool_calls_count,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "estimated_cost_usd": result.estimated_cost_usd,
        "summary": result.summary,
        "signals": [signal.model_dump(mode="json") for signal in result.signals],
        "researcher": result.researcher.model_dump(mode="json"),
        "error_message": result.error_message,
    }
    print(json.dumps(output, indent=2))
    return 0 if result.status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
