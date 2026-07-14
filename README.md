# Lab2Startup

[![CI](https://github.com/rocky2397/lab2startup/actions/workflows/ci.yml/badge.svg)](https://github.com/rocky2397/lab2startup/actions/workflows/ci.yml)

**Agentic VC sourcing: find academic AI researchers who are about to found startups — before they announce anything.**

Deep-tech founders usually publish before they incorporate. Lab2Startup watches top AI/systems conferences (NeurIPS, ICML, OSDI, USENIX Security, …), builds researcher and coauthor-team profiles from accepted papers, sends AI agents to search the public web for founder and commercialization evidence, and ranks everyone 0–100 by startup likelihood — so a fund's monthly sourcing pass takes minutes instead of days.

## What a run produces

For one conference, one command produces:

- A **ranked candidate list** with a 6-component score breakdown, priority band, and a concrete VC action (take meeting / monitor monthly / watchlist / ignore)
- **Founder signals with evidence**: every claim links to a public source URL, typed (confirmed founder / possible founder / commercialization) and graded by strength
- **Thesis-fit assessment** per researcher against the fund's investment thesis (infra vs application layer, Europe nexus)
- A **diff vs the previous run** of the same conference: new take-meetings, new researchers, score jumps, new signals
- **Full audit trail**: per-researcher investigation traces with step timelines, token counts, and estimated cost, persisted in SQLite

<!-- RESULTS: after the first published production run, insert the numbers table here:
     N researchers ranked · X investigated (deep/standard/light) · Y founder signals ·
     Z verified true positives (links) · total cost $ · cost per verified signal -->

## Architecture

```mermaid
flowchart LR
    SRC[OpenReview / OpenAlex<br/>accepted papers] --> ING[Ingestion<br/>researchers + coauthor clusters]
    ING --> PRE[Deterministic prefilter<br/>rule-based score, zero LLM calls]
    PRE --> COORD{LangGraph<br/>coordinator}
    COORD -->|top 3| DEEP[Deep tier<br/>8-step budget]
    COORD -->|next 7| STD[Standard tier<br/>3-step budget]
    COORD -->|rest| LIGHT[Light tier / skip]
    DEEP --> AGENT[Perplexity Agent API<br/>web search · page fetch · custom tools]
    STD --> AGENT
    LIGHT --> AGENT
    AGENT --> DB[(SQLite<br/>runs · traces · audits)]
    AGENT --> SCORE[Scoring 0–100<br/>6 components + identity penalty]
    SCORE --> POST[Thesis-fit + run-diff agents]
    POST --> DB
    DB --> UI[Desktop app<br/>FastAPI + native window]
```

The signal stage has two modes: **one-shot Sonar** (default — one structured web-search query per researcher) and **agentic** (`LAB2STARTUP_AGENTIC_SIGNALS=true` — a LangGraph coordinator assigns investigation tiers and drives multi-step Perplexity Agent API investigations with custom function tools like `github_repo_search` and `lookup_prior_run`).

## Design decisions

- **Deterministic prefilter before any LLM call.** A rule-based score (conference tier, topic relevance, coauthor network, recency) decides who is worth investigating. LLM spend goes only to plausible candidates, and the ranking is reproducible.
- **Tiered budgets, enforced on our side.** The top 3 candidates get a deep 8-step investigation, the next 7 get 3 steps, the rest get a light pass or are skipped. Per-run call caps and a global step budget bound worst-case cost regardless of what the agent wants to do.
- **Early exit.** When a deep investigation surfaces high-confidence founder evidence, the queue stops — the interesting finding is already in hand, so the remaining budget isn't burned on long shots.
- **Build vs buy on the agent loop.** The multi-step tool-use loop is delegated to Perplexity's Agent API; LangGraph is the deterministic control plane around it (tiering, budgets, retries, trace persistence). Hand-rolling a ReAct loop over raw search APIs would have meant maintaining scraping reliability for zero product benefit — the value here is in candidate selection, cost control, and traceability, which all stay on our side.
- **Identity confidence gating.** Common names are the biggest false-positive source. Profile matches carry a confidence level; low-confidence researchers are not investigated by default, and uncertain matches take a score penalty rather than silently polluting the ranking.
- **Everything is auditable.** Every investigation stores its full request/response, step timeline, tokens, and estimated cost in SQLite. Enrichment audits capture researcher state before/after, so "did the agents actually find anything?" is a query, not a guess.

## Quickstart

Requires Python 3.11+ and a [Perplexity API key](https://www.perplexity.ai/settings/api).

```bash
git clone https://github.com/rocky2397/lab2startup && cd lab2startup
python -m venv .venv && .venv/bin/pip install -e .

cp .env.example .env   # then set LAB2STARTUP_PERPLEXITY_API_KEY=...
```

Run a conference and open the app:

```bash
# One conference (papers from OpenReview, signals from Perplexity)
python run_pipeline.py --conference NeurIPS --year 2024

# Or all high-priority conferences in the fund scope
python run_pipeline.py --priority high --year 2024

# Native desktop app (also: ./Lab2Startup.command, or `just app`)
python run_app.py
```

The desktop app opens in a native window: pick a stored run, filter by score/recommendation/thesis fit, inspect ranked candidates with score breakdowns and full reports, launch new conference runs with live progress, and (behind the developer-tools toggle) review enrichment audits and per-candidate investigation traces.

Recurring monitoring:

```bash
python run_monitor.py --fund backtrace --priority high --year 2024   # monthly batch
python run_monitor.py --digest-only --since 2026-06-01               # diff digest
```

All configuration is environment-driven — see **[docs/configuration.md](docs/configuration.md)** for the full reference (paper sources, agentic budgets, supplements, post-pipeline agents).

## Development

```bash
.venv/bin/pip install -e ".[dev]"
pytest -q          # 178 tests, no network, no API key needed
just lint          # ruff format check + lint
```

Development mode (`LAB2STARTUP_MODE=development`, the default in tests) runs the whole pipeline against mock JSON papers and signals, so everything is testable offline.

Signal quality is measured against a golden set of researchers with verified founder / non-founder ground truth — see **[evals/](evals/README.md)** for methodology; published precision/recall numbers land here once the verified run completes.

```
app/
  agents/            # ingestion, profiling, signal, scoring, report, thesis-fit, diff agents
  integrations/      # openreview, openalex, perplexity (sonar + agent), github, semantic scholar
  dashboard_api.py   # /api backing the desktop app
  main.py            # FastAPI entrypoint (serves API + webapp/)
  run_service.py     # pipeline execution + SQLite persistence
funds/backtrace.yaml # fund profile: conference scope, topic scores, thesis rules
webapp/              # desktop app frontend (vanilla JS, no build step)
run_app.py           # native desktop launcher (uvicorn + pywebview)
run_pipeline.py      # CLI pipeline runner
```

Fund scope is a YAML profile (`funds/backtrace.yaml` by default): which conferences to monitor at which priority, topic scoring overrides, thesis-fit rules, and the Perplexity context string. Adding a fund = adding a YAML file.
