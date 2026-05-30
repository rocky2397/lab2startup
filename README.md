# Lab2Startup

An agentic VC sourcing tool that tracks researchers from top academic AI conferences and detects public signals that they may be founding, joining, or commercializing deep-tech startups.

## Project Goal

Start from academic conference data (papers and authors), extract researcher profiles and coauthor clusters, attach commercialization signals, score startup likelihood, and produce founder-monitoring reports.

**Current status: Step 11–14 in progress** — SQLite run persistence, CLI orchestrator, production mode, dashboard run selector. See [PLAN.md](PLAN.md).

## How to Use

**You need a [Perplexity API key](https://www.perplexity.ai/settings/api)** for real founder signals and profile enrichment. Without it, production runs will fetch papers but produce little or no signal data.

### 1. Install

```bash
cd lab2startup
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and set at minimum:

```bash
LAB2STARTUP_MODE=production
LAB2STARTUP_PERPLEXITY_API_KEY=your_key_here   # required
```

Perplexity is the **primary signal source** — it searches the web for founder/startup evidence and resolves researcher affiliations. See [Perplexity founder signal search](#perplexity-founder-signal-search-step-10e) for all options.

### 3. Run a conference pipeline

```bash
# List conferences in the Backtrace fund scope
python run_pipeline.py --list-conferences

# Single conference (papers from OpenReview, signals from Perplexity)
python run_pipeline.py --conference NeurIPS --year 2024

# All high-priority conferences in one batch
python run_pipeline.py --priority high --year 2024
```

Runs are saved to SQLite (`.cache/lab2startup.db` by default).

After each run, **thesis fit** (Backtrace EU + infra rules, optional Sonar) and **run diff** (vs prior complete run for the same conference) are saved automatically when enabled.

```bash
# Recompute diff or thesis fit for an existing run
python run_diff.py --run-id run_2024_neurips_...
python run_thesis_fit.py --run-id run_2024_neurips_... --no-sonar

# Monthly batch + diff digest
python run_monitor.py --fund backtrace --priority high --year 2024
python run_monitor.py --digest-only --since 2026-05-01
```

### 4. Open the dashboard

```bash
python run_dashboard.py
```

Open the URL Streamlit prints (usually http://localhost:8501). Use the sidebar to pick a **Stored run**, filter candidates, and explore reports. Enable **Only show runs with results** to hide empty runs after batch jobs.

### Quick dev mode (no API key)

For tests and offline prototyping, leave `LAB2STARTUP_MODE=development` (default). The dashboard uses mock JSON papers and signals — no Perplexity key required.

## MVP Scope

1. Ingest conference papers/authors from **OpenAlex** or local JSON
2. Extract researchers and coauthor clusters
3. Store public commercialization signals
4. Score each researcher/team with a rule-based model
5. Produce a simple founder-monitoring report

For now, the project uses **local mock JSON by default** — OpenAlex is available when configured.

## Folder Structure

```
lab2startup/
  app/
    config.py                # Env-based paper source settings (Step 10a)
    integrations/
      openalex.py            # OpenAlex fetch + normalize (Step 10a)
      semantic_scholar.py    # Semantic Scholar enrichment (Step 10b)
      openreview.py          # OpenReview fetch + affiliations (Step 10c)
      github.py              # GitHub signal detection (Step 10d)
      perplexity.py          # Perplexity founder signal search (Step 10e)
    main.py                  # FastAPI entrypoint (Step 8)
    database.py              # SQLite helpers (Step 2/8)
    models.py                # Data models (Step 2)
    schemas.py               # Pydantic schemas (Step 2)
    scoring.py               # Scoring logic (Step 6)
    report_generator.py      # Report generation (Step 7)
    agents/
      ingestion_agent.py     # Load papers, extract researchers (Step 3)
      profile_agent.py       # Researcher profiles (Step 4)
      signal_agent.py        # Attach signals (Step 5)
      scoring_agent.py       # Compute scores (Step 6)
      report_agent.py        # Generate reports (Step 7)
    data/
      sample_papers.json     # Mock conference papers
      sample_signals.json    # Mock commercialization signals
  dashboard/
    streamlit_app.py         # Streamlit dashboard (Step 9)
  tests/
    test_scoring.py          # Scoring tests (Step 6)
  README.md
  requirements.txt
```

## Mock Data

### Papers (`app/data/sample_papers.json`)

Real NeurIPS papers from 2023–2024, sourced from OpenAlex, NeurIPS proceedings, OpenReview, and arXiv:

- **7 papers** across AI agents, robotics, and biotech AI
- **Topics:** AI agents (SWE-agent, AGILE, ToolkenGPT), robotics (PIVOT-R, bridge-policy recovery), biotech AI (PocketFlow, S3F)
- **~25 unique researchers** with overlapping coauthor themes for future clustering

Each paper includes: `id`, `title`, `conference`, `year`, `topic`, `abstract`, `authors`, plus `source_url` and `openalex_id` for traceability.

### Signals (`app/data/sample_signals.json`)

- **9 signals** tied to real authors from the paper set, using verifiable public URLs (personal sites, GitHub, OpenReview, institutional pages)

## OpenAlex ingestion (Step 10a)

Fetch papers from [OpenAlex](https://openalex.org/) and normalize them into the existing `Paper` model. Mock JSON remains the default so tests and offline use keep working.

### CLI — fetch and save JSON

```bash
cd lab2startup
source .venv/bin/activate

# Fetch by explicit OpenAlex work IDs (matches sample dataset papers)
python -m app.integrations.openalex \
  --work-id W4399114781 --work-id W4398859681 \
  --conference NeurIPS --year 2024 \
  --output app/data/openalex_papers.json

# Search NeurIPS 2024 papers with "agent" in the title
python -m app.integrations.openalex \
  --conference NeurIPS --year 2024 --search agent \
  --topic "AI agents" --max-results 25 \
  --mailto you@example.com \
  --output app/data/openalex_papers.json
```

Load a saved file via `LAB2STARTUP_PAPERS_PATH` or pass the path into the pipeline.

### Live pipeline via environment variables

```bash
export LAB2STARTUP_PAPER_SOURCE=openalex
export LAB2STARTUP_OPENALEX_CONFERENCE=NeurIPS
export LAB2STARTUP_OPENALEX_YEAR=2024
export LAB2STARTUP_OPENALEX_SEARCH=agent
export LAB2STARTUP_OPENALEX_TOPICS="AI agents,ML systems"
export LAB2STARTUP_OPENALEX_MAX_RESULTS=25
export LAB2STARTUP_OPENALEX_MAILTO=you@example.com

uvicorn app.main:app --reload
```

| Variable | Default | Purpose |
|----------|---------|---------|
| `LAB2STARTUP_PAPER_SOURCE` | `json` | `json` or `openalex` |
| `LAB2STARTUP_PAPERS_PATH` | `app/data/sample_papers.json` | JSON file when source is `json` |
| `LAB2STARTUP_OPENALEX_CONFERENCE` | `NeurIPS` | Conference name (resolved to OpenAlex source ID) |
| `LAB2STARTUP_OPENALEX_YEAR` | `2024` | Publication year filter |
| `LAB2STARTUP_OPENALEX_SEARCH` | — | Optional title search |
| `LAB2STARTUP_OPENALEX_TOPICS` | — | Comma-separated topic keyword filters |
| `LAB2STARTUP_OPENALEX_WORK_IDS` | — | Comma-separated OpenAlex work IDs |
| `LAB2STARTUP_OPENALEX_MAX_RESULTS` | `50` | Max papers to fetch |
| `LAB2STARTUP_OPENALEX_MAILTO` | — | Email for OpenAlex polite pool |

## Semantic Scholar enrichment (Step 10b)

Enrich ingested papers and researchers with citation counts, influential citations, and author profiles from [Semantic Scholar](https://www.semanticscholar.org/). Disabled by default so the mock dataset and tests stay fast and offline-friendly.

Papers are matched via arXiv/DOI URLs in `source_url`. Author profiles are matched by normalized name across coauthors on enriched papers.

### Enable in the pipeline

```bash
export LAB2STARTUP_SEMANTIC_SCHOLAR_ENABLED=true
export LAB2STARTUP_S2_API_KEY=your_key_here   # optional but recommended (1 req/s)
export LAB2STARTUP_S2_FETCH_AUTHORS=true
export LAB2STARTUP_S2_REQUEST_DELAY=1.1

uvicorn app.main:app --reload
```

| Variable | Default | Purpose |
|----------|---------|---------|
| `LAB2STARTUP_SEMANTIC_SCHOLAR_ENABLED` | `false` | Enable Semantic Scholar enrichment |
| `LAB2STARTUP_S2_API_KEY` | — | API key for higher rate limits |
| `LAB2STARTUP_S2_FETCH_AUTHORS` | `true` | Fetch author h-index and citation totals |
| `LAB2STARTUP_S2_REQUEST_DELAY` | `1.1` | Delay between API requests (seconds) |

When enrichment is enabled, scoring adds a small citation bonus to **Research quality** and reports include Semantic Scholar profile stats in the summary.

### CLI — enrich a papers JSON file

```bash
python -m app.integrations.semantic_scholar \
  --input app/data/sample_papers.json \
  --output app/data/enriched_papers.json \
  --api-key your_key_here
```

## OpenReview integration (Step 10c)

Fetch or enrich conference papers from [OpenReview](https://openreview.net/) with **author affiliations, roles, and profile links**. Supports NeurIPS, ICLR, and ICML via API v2.

Two modes:

1. **Fetch source** — load papers directly from OpenReview (`LAB2STARTUP_PAPER_SOURCE=openreview`)
2. **Enrichment** — match your existing JSON/OpenAlex papers by title and backfill affiliations (default-friendly)

When a researcher is linked to an OpenReview profile, identity confidence is raised to **HIGH**.

### Enrich the mock dataset

```bash
export LAB2STARTUP_OPENREVIEW_ENABLED=true
export LAB2STARTUP_OPENREVIEW_CONFERENCE=NeurIPS
export LAB2STARTUP_OPENREVIEW_YEAR=2024

uvicorn app.main:app --reload
```

### Fetch live papers from OpenReview

```bash
export LAB2STARTUP_PAPER_SOURCE=openreview
export LAB2STARTUP_OPENREVIEW_CONFERENCE=NeurIPS
export LAB2STARTUP_OPENREVIEW_YEAR=2024
export LAB2STARTUP_OPENREVIEW_MAX_RESULTS=50

python -m app.integrations.openreview --conference NeurIPS --year 2024 --max-results 10 \
  --output app/data/openreview_papers.json
```

| Variable | Default | Purpose |
|----------|---------|---------|
| `LAB2STARTUP_PAPER_SOURCE` | `json` | Set to `openreview` to fetch papers from OpenReview |
| `LAB2STARTUP_OPENREVIEW_ENABLED` | `false` | Enrich existing papers with OpenReview metadata |
| `LAB2STARTUP_OPENREVIEW_CONFERENCE` | `NeurIPS` | Conference name (`NeurIPS`, `ICLR`, `ICML`) |
| `LAB2STARTUP_OPENREVIEW_YEAR` | `2024` | Conference year |
| `LAB2STARTUP_OPENREVIEW_MAX_RESULTS` | `50` / `1000` | Max papers (fetch vs enrich) |
| `LAB2STARTUP_OPENREVIEW_ACCEPTED_ONLY` | `true` | Keep accepted submissions only |
| `LAB2STARTUP_OPENREVIEW_FETCH_PROFILES` | `true` | Load author profile affiliations |
| `LAB2STARTUP_OPENREVIEW_REQUEST_DELAY` | `0.5` | Delay between API requests (seconds) |

## GitHub signal detection (Step 10d)

Detect **open-source commercialization signals** by searching GitHub for repositories related to each paper title. Disabled by default; when enabled, GitHub signals are **merged with** `sample_signals.json` (deduplicated by URL).

Repositories are matched to researchers using author-name heuristics and organization names derived from paper titles (e.g. `SWE-agent/SWE-agent` → SWE-agent paper authors).

### Enable in the pipeline

```bash
export LAB2STARTUP_GITHUB_ENABLED=true
export LAB2STARTUP_GITHUB_TOKEN=your_token_here   # optional, improves rate limits
export LAB2STARTUP_GITHUB_MIN_STARS=5
export LAB2STARTUP_GITHUB_MAX_REPOS_PER_PAPER=2

uvicorn app.main:app --reload
```

| Variable | Default | Purpose |
|----------|---------|---------|
| `LAB2STARTUP_GITHUB_ENABLED` | `false` | Enable GitHub signal detection |
| `LAB2STARTUP_GITHUB_TOKEN` | — | GitHub personal access token |
| `LAB2STARTUP_GITHUB_MIN_STARS` | `5` | Minimum repository stars to emit a signal |
| `LAB2STARTUP_GITHUB_MAX_REPOS_PER_PAPER` | `2` | Max GitHub signals per paper |
| `LAB2STARTUP_GITHUB_SUPPLEMENT_MOCK` | `true` | Keep mock signals and append new GitHub ones |
| `LAB2STARTUP_GITHUB_REQUEST_DELAY` | `0.5` | Delay between API requests (seconds) |

Evidence strength scales with stars and recent activity. GitHub URLs feed directly into the existing **open source / project momentum** scoring component.

### CLI — probe a paper title

```bash
python -m app.integrations.github \
  --paper-title "SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering" \
  --researcher "John Yang"
```

## Perplexity founder signal search (Step 10e)

Use the [Perplexity Sonar API](https://docs.perplexity.ai/) to search the public web for founder, startup, and commercialization evidence **per researcher**. Disabled by default; when enabled, Perplexity signals are **merged with** mock JSON and GitHub signals (deduplicated by URL).

Researchers are queried with paper titles, affiliation, and profile URLs as context. Only researchers meeting the identity-confidence threshold are queried (default: `high` only) to reduce false positives.

### Enable in the pipeline

Copy `.env.example` to `.env` and set your API key, or export variables directly:

```bash
export LAB2STARTUP_PERPLEXITY_ENABLED=true
export LAB2STARTUP_PERPLEXITY_API_KEY=your_key_here
export LAB2STARTUP_PERPLEXITY_MODEL=sonar-pro
export LAB2STARTUP_PERPLEXITY_MAX_RESEARCHERS=10

python run_dashboard.py
```

| Variable | Default | Purpose |
|----------|---------|---------|
| `LAB2STARTUP_PERPLEXITY_ENABLED` | `true` | Enable Perplexity signal detection |
| `LAB2STARTUP_PERPLEXITY_API_KEY` | — | Perplexity API key (required when enabled) |
| `LAB2STARTUP_PERPLEXITY_MODEL` | `sonar-pro` | Sonar model (`sonar-pro`, `sonar`, etc.) |
| `LAB2STARTUP_PERPLEXITY_MAX_RESEARCHERS` | `10` | Cap API calls per pipeline run |
| `LAB2STARTUP_PERPLEXITY_MAX_SIGNALS_PER_RESEARCHER` | `2` | Max signals emitted per researcher |
| `LAB2STARTUP_PERPLEXITY_MIN_IDENTITY` | `high` | Minimum identity confidence (`high`, `medium`, `low`) |
| `LAB2STARTUP_PERPLEXITY_SUPPLEMENT_MOCK` | `true` | Keep mock signals and append new Perplexity ones |
| `LAB2STARTUP_PERPLEXITY_REQUEST_DELAY` | `1.0` | Delay between API requests (seconds) |
| `LAB2STARTUP_PERPLEXITY_MAX_WORKERS` | `3` | Parallel Perplexity queries |

The first request with a new JSON schema may take 10–30 seconds while Perplexity prepares it. Subsequent requests are faster.

### Performance tips

| Goal | Setting |
|------|---------|
| **Fast dashboard startup** | Disk cache (`LAB2STARTUP_PIPELINE_CACHE_ENABLED=true`, default) — restarts skip live API calls |
| **Live founder search** | Perplexity is **on by default**; runs on first load or **Refresh live data** |
| **Disable Perplexity** | `LAB2STARTUP_PERPLEXITY_ENABLED=false` |
| **Faster Perplexity refresh** | Raise `LAB2STARTUP_PERPLEXITY_MAX_WORKERS` (e.g. `3`) |

Cached results live in `.cache/` and invalidate when config or input JSON files change.

### CLI — probe a researcher

```bash
python -m app.integrations.perplexity \
  --name "John Yang" \
  --affiliation "Stanford University" \
  --paper-title "SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering"
```

## Agentic signal pipeline (LangGraph + Perplexity Agent API)

When `LAB2STARTUP_AGENTIC_SIGNALS=true`, the signal stage uses a **LangGraph coordinator** that ranks researchers, assigns investigation tiers (light / standard / deep), and calls the **Perplexity Agent API** (`POST /v1/agent`) with built-in tools plus custom functions (`github_repo_search`, `lookup_prior_run`). Full investigation traces are stored in SQLite for dashboard review.

See [PLAN_AGENTIC.md](PLAN_AGENTIC.md) for architecture and rollout details.

### Enable agentic mode

```bash
export LAB2STARTUP_AGENTIC_SIGNALS=true
export LAB2STARTUP_PERPLEXITY_API_KEY=your_key_here
export LAB2STARTUP_AGENTIC_MAX_CALLS=10
export LAB2STARTUP_AGENTIC_EARLY_EXIT=true

python run_pipeline.py --conference NeurIPS --year 2024
python run_dashboard.py
```

In the dashboard, agentic runs show **Signal mode: Agentic (LangGraph)** with investigation count, token totals, and estimated cost. On the **Explore & details** tab, expand **Investigation trace** per candidate to see the step timeline and download raw JSON.

| Variable | Default | Purpose |
|----------|---------|---------|
| `LAB2STARTUP_AGENTIC_SIGNALS` | `false` | Enable LangGraph + Agent API path |
| `LAB2STARTUP_AGENTIC_MAX_CALLS` | `10` | Max Agent API investigations per run |
| `LAB2STARTUP_AGENTIC_MAX_TOTAL_STEPS` | `40` | Global step budget across all investigations |
| `LAB2STARTUP_AGENTIC_EARLY_EXIT` | `true` | Stop queue on high-confidence founder evidence |
| `LAB2STARTUP_AGENTIC_DEEP_SLOTS` | `3` | Top N researchers get deep tier |
| `LAB2STARTUP_AGENTIC_STANDARD_SLOTS` | `7` | Next N get standard tier |
| `LAB2STARTUP_AGENTIC_PREFILTER_MIN_SCORE` | `20` | Skip researchers below prefilter score |
| `LAB2STARTUP_AGENTIC_MODEL` | — | Override model (else use tier preset) |
| `LAB2STARTUP_AGENTIC_PRESET_STANDARD` | `pro-search` | Preset for standard tier |
| `LAB2STARTUP_AGENTIC_PRESET_DEEP` | `deep-research` | Preset for deep tier |
| `LAB2STARTUP_AGENTIC_REQUEST_DELAY` | `1.5` | Delay between investigations (seconds) |

When agentic mode is on, `LAB2STARTUP_PERPLEXITY_MAX_RESEARCHERS` is ignored for call count (use `LAB2STARTUP_AGENTIC_MAX_CALLS` instead). The Sonar one-shot path remains unchanged when `LAB2STARTUP_AGENTIC_SIGNALS=false`.

### CLI — probe a single researcher (Agent API)

```bash
python -m app.integrations.perplexity_agent \
  --name "John Yang" \
  --affiliation "Stanford University" \
  --paper-title "SWE-agent" \
  --tier deep
```

Tiers map to presets and step caps: `light` (1 step), `standard` (3), `deep` (8). Output is JSON with status, signals, token usage, and estimated cost.

### Manual live smoke test

```bash
export LAB2STARTUP_PERPLEXITY_API_KEY=...
pytest tests/test_agentic_live.py -k live_agent_probe -s
```

This test is skipped in CI (`@pytest.mark.skip`).

## How to Run / Test (Step 10e)

### Dashboard (recommended)

```bash
cd lab2startup
source .venv/bin/activate
pip install -e .   # one-time: makes `app` importable everywhere

python run_dashboard.py
# or: streamlit run dashboard/streamlit_app.py
```

Open the URL Streamlit prints (usually http://localhost:8501).

Use the sidebar to filter by conference, year, topic, minimum score, and recommendation. Select a candidate to view score breakdown, signals, and the full markdown report.

### API (optional)

```bash
uvicorn app.main:app --reload
```

API docs: http://127.0.0.1:8000/docs

### Tests

```bash
pytest tests/ -v
```

**Expected output:** dashboard loads ranked candidates; pytest passes all tests.

## Tech Stack

- Python
- FastAPI (backend, Step 8)
- SQLite via plain `sqlite3` (Step 2/8)
- Pydantic (schemas, Step 2)
- Streamlit (dashboard, Step 9)
- pytest (tests)

## Data Models (Step 2)

Core types live in [`app/models.py`](app/models.py):

| Model | Purpose |
|-------|---------|
| `Paper`, `PaperAuthor` | Conference papers from mock JSON |
| `Researcher` | Extracted researcher profiles (Step 3) |
| `Signal` | Commercialization / founder signals |
| `Cluster` | Coauthor teams (Step 4) |
| `ScoreBreakdown`, `Report` | Scoring and reporting (Steps 6–7) |

Enums include `SignalType`, `EvidenceStrength`, `IdentityConfidence`, `PriorityBand`, and `VCAction`.

JSON loaders live in [`app/schemas.py`](app/schemas.py):

- `load_papers()` — parse `sample_papers.json`
- `load_signals()` — parse `sample_signals.json`
- `load_sample_data()` — load both files
- `summarize_dataset()` — quick counts for inspection

Ingestion logic lives in [`app/agents/ingestion_agent.py`](app/agents/ingestion_agent.py):

- `ingest_papers()` — load JSON and extract researchers
- `extract_researchers()` — build profiles with papers, coauthors, identity confidence
- `make_researcher_id()` — stable slug IDs for downstream signal matching (Step 5)

Clustering logic lives in [`app/agents/profile_agent.py`](app/agents/profile_agent.py):

- `build_profiles()` — ingest papers and build coauthor clusters
- `build_clusters()` — group researchers connected by shared papers
- `summarize_profiles()` — quick cluster stats for inspection

Signal matching lives in [`app/agents/signal_agent.py`](app/agents/signal_agent.py):

- `detect_signals()` — load profiles + `sample_signals.json`, attach IDs
- `attach_signals()` — map `researcher_name` → `researcher_id` and `cluster_id`
- `group_signals_by_researcher()` / `group_signals_by_cluster()` — lookup helpers
- `summarize_signal_detection()` — quick stats for inspection

Scoring logic lives in [`app/scoring.py`](app/scoring.py) and [`app/agents/scoring_agent.py`](app/agents/scoring_agent.py):

- `run_scoring()` — full pipeline through ranked scores
- `score_researcher()` / `score_cluster()` — component scoring
- Components: research quality, applied relevance, team continuity, project momentum, signal strength, recency

Priority bands: 80+ high, 60–79 monitor, 40–59 watchlist, 0–39 ignore.

Report generation lives in [`app/report_generator.py`](app/report_generator.py) and [`app/agents/report_agent.py`](app/agents/report_agent.py):

- `run_reports()` — full pipeline through markdown reports
- `render_report_markdown()` — convert a `Report` to markdown
- `write_reports_to_directory()` — save `.md` files locally

The FastAPI app lives in [`app/main.py`](app/main.py):

| Endpoint | Description |
|----------|-------------|
| `GET /` | Health check and dataset counts |
| `GET /papers` | List papers (`conference`, `year`, `topic` filters) |
| `GET /researchers` | List researcher profiles |
| `GET /clusters` | List coauthor clusters |
| `GET /signals` | List attached signals |
| `GET /scores` | Ranked researcher and cluster scores |
| `GET /reports` | Report summaries (`min_score`, `recommendation` filters) |
| `GET /reports/{id}` | Full report with markdown |

The Streamlit dashboard lives in [`dashboard/streamlit_app.py`](dashboard/streamlit_app.py):

- Ranked candidate table with score, priority, recommendation
- Filters: conference, year, topic, min score, recommendation
- Researcher or cluster view
- Score breakdown chart, signal list, full markdown report

## Monthly conference run (Backtrace scope)

Lab2Startup defaults to the **Backtrace Capital** fund profile (`funds/backtrace.yaml`). Only Backtrace-relevant conferences are allowed (NeurIPS, ICML, MLSys, OSDI, SOSP, NSDI, USENIX Security, ICSE).

```bash
export LAB2STARTUP_MODE=production
export LAB2STARTUP_FUND=backtrace   # default

# List conferences in scope
python run_pipeline.py --list-conferences

# Run NeurIPS 2025 (OpenReview — auto-selected for NeurIPS)
python run_pipeline.py --conference NeurIPS --year 2025

# MLSys via OpenAlex (OpenReview not available)
python run_pipeline.py --conference MLSys --year 2025 --paper-source openalex

python run_dashboard.py
```

| Variable | Default | Purpose |
|----------|---------|---------|
| `LAB2STARTUP_FUND` | `backtrace` | Fund profile in `funds/` — scopes conferences, scoring, Perplexity |
| `LAB2STARTUP_MODE` | `development` | `production` disables mock signals, dashboard uses SQLite runs |
| `LAB2STARTUP_DB_PATH` | `.cache/lab2startup.db` | SQLite database for stored runs |
| `LAB2STARTUP_USE_MOCK_SIGNALS` | `true` in dev, `false` in prod | Load `sample_signals.json` |

Development mode keeps mock JSON for tests and prototyping. See [PLAN.md](PLAN.md) for the full roadmap.

## Next Step

**Step 15:** Fund/thesis profiles (`funds/*.yaml`) and run diff vs previous month.

## Build Roadmap

| Step | Description |
|------|-------------|
| 1 | Project scaffold and mock data |
| 2 | Data models and schemas |
| 3 | Ingestion agent |
| 4 | Coauthor clustering |
| 5 | Mock signal detection |
| 6 | Scoring logic |
| 7 | Report generation |
| 8 | FastAPI backend |
| 9 | Streamlit dashboard |
| 10a | OpenAlex paper ingestion |
| 10b | Semantic Scholar enrichment |
| 10c | OpenReview affiliations |
| 10d | GitHub signal detection |
| 10e | Perplexity founder signal search |
| 11 | SQLite run persistence *(current)* |
| 12 | CLI `run_pipeline.py` / `lab2startup-run` |
| 13 | Production mode (`LAB2STARTUP_MODE=production`) |
| 14 | Dashboard run selector |
| 15+ | Fund profiles, run diff |
