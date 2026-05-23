# Lab2Startup

An agentic VC sourcing tool that tracks researchers from top academic AI conferences and detects public signals that they may be founding, joining, or commercializing deep-tech startups.

## Project Goal

Start from academic conference data (papers and authors), extract researcher profiles and coauthor clusters, attach commercialization signals, score startup likelihood, and produce founder-monitoring reports.

**Current status: Step 10a complete** — OpenAlex paper ingestion with JSON fallback. Streamlit dashboard and API unchanged; set env vars to fetch live papers.

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

## How to Run / Test (Step 10a)

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

## Next Step

**Step 10b:** Semantic Scholar integration for citations and author metadata.

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
| 10a | OpenAlex paper ingestion *(current)* |
| 10b+ | Semantic Scholar, OpenReview, GitHub, web search |
