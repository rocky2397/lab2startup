# Configuration reference

All settings are environment variables (loaded from `.env` — copy `.env.example` to get started). Only `LAB2STARTUP_PERPLEXITY_API_KEY` is required for real runs.

## Core

| Variable | Default | Purpose |
|----------|---------|---------|
| `LAB2STARTUP_MODE` | `development` | `production` loads stored SQLite runs and disables mock signals |
| `LAB2STARTUP_FUND` | `default` | Fund profile in `funds/` — scopes conferences, scoring, Perplexity context |
| `LAB2STARTUP_DB_PATH` | `.cache/lab2startup.db` | SQLite database for stored runs, traces, audits |
| `LAB2STARTUP_PAPER_SOURCE` | `openreview` (prod) / `json` (dev) | `openreview`, `openalex`, or `json` |
| `LAB2STARTUP_PAPERS_PATH` | `app/data/sample_papers.json` | Paper JSON when source is `json` |
| `LAB2STARTUP_USE_MOCK_SIGNALS` | `false` (prod) / `true` (dev) | Load `sample_signals.json` (tests/dev only) |

## Perplexity founder search (primary signal source)

Searches the public web for founder/startup evidence per researcher and resolves affiliations. Only researchers meeting the identity-confidence threshold are queried, to reduce false positives.

| Variable | Default | Purpose |
|----------|---------|---------|
| `LAB2STARTUP_PERPLEXITY_ENABLED` | `true` | Enable Perplexity signal detection |
| `LAB2STARTUP_PERPLEXITY_API_KEY` | — | API key (required when enabled) |
| `LAB2STARTUP_PERPLEXITY_MODEL` | `sonar-pro` | Sonar model |
| `LAB2STARTUP_PERPLEXITY_MAX_RESEARCHERS` | `0` (no cap) | Cap Sonar calls per run |
| `LAB2STARTUP_PERPLEXITY_MAX_SIGNALS_PER_RESEARCHER` | `2` | Max signals emitted per researcher |
| `LAB2STARTUP_PERPLEXITY_MIN_IDENTITY` | `high` | Minimum identity confidence to query |
| `LAB2STARTUP_PERPLEXITY_ENRICH_PROFILES` | `true` | Resolve affiliation/role/links via Perplexity |
| `LAB2STARTUP_PERPLEXITY_REQUEST_DELAY` | `1.0` | Delay between API requests (seconds) |
| `LAB2STARTUP_PERPLEXITY_MAX_WORKERS` | `3` | Parallel Perplexity queries |

CLI probe:

```bash
python -m app.integrations.perplexity \
  --name "John Yang" --affiliation "Stanford University" \
  --paper-title "SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering"
```

## Agentic investigations (LangGraph + Perplexity Agent API)

| Variable | Default | Purpose |
|----------|---------|---------|
| `LAB2STARTUP_AGENTIC_SIGNALS` | `false` | Use the LangGraph coordinator + Agent API instead of one-shot Sonar |
| `LAB2STARTUP_AGENTIC_MAX_CALLS` | `0` (no cap) | Max Agent API investigations per run |
| `LAB2STARTUP_AGENTIC_MAX_TOTAL_STEPS` | `0` (no cap) | Global step budget across all investigations |
| `LAB2STARTUP_AGENTIC_EARLY_EXIT` | `true` | Stop the queue on high-confidence founder evidence |
| `LAB2STARTUP_AGENTIC_DEEP_SLOTS` | `3` | Top N researchers get the deep tier |
| `LAB2STARTUP_AGENTIC_STANDARD_SLOTS` | `7` | Next N get the standard tier |
| `LAB2STARTUP_AGENTIC_PREFILTER_MIN_SCORE` | `20` | Skip researchers below the deterministic prefilter score |
| `LAB2STARTUP_AGENTIC_MODEL` | — | Override model (else tier preset) |
| `LAB2STARTUP_AGENTIC_PRESET_STANDARD` | `pro-search` | Preset for standard tier |
| `LAB2STARTUP_AGENTIC_PRESET_DEEP` | `deep-research` | Preset for deep tier |
| `LAB2STARTUP_AGENTIC_REQUEST_DELAY` | `1.5` | Delay between investigations (seconds) |

Tiers map to step caps: `light` (1), `standard` (3), `deep` (8). When agentic mode is on, `LAB2STARTUP_PERPLEXITY_MAX_RESEARCHERS` is ignored (use `LAB2STARTUP_AGENTIC_MAX_CALLS`).

CLI probe:

```bash
python -m app.integrations.perplexity_agent \
  --name "John Yang" --affiliation "Stanford University" \
  --paper-title "SWE-agent" --tier deep
```

Manual live smoke test (skipped in CI):

```bash
export LAB2STARTUP_PERPLEXITY_API_KEY=...
pytest tests/test_agentic_live.py -k live_agent_probe -s
```

## Paper sources

### OpenReview (default for NeurIPS/ICML/ICLR)

| Variable | Default | Purpose |
|----------|---------|---------|
| `LAB2STARTUP_OPENREVIEW_ENABLED` | `true` | Enrich existing papers with OpenReview metadata |
| `LAB2STARTUP_OPENREVIEW_CONFERENCE` | `NeurIPS` | Conference (`NeurIPS`, `ICLR`, `ICML`) |
| `LAB2STARTUP_OPENREVIEW_YEAR` | `2024` | Conference year |
| `LAB2STARTUP_OPENREVIEW_MAX_RESULTS` | `50` / `1000` | Max papers (fetch vs enrich) |
| `LAB2STARTUP_OPENREVIEW_ACCEPTED_ONLY` | `true` | Keep accepted submissions only |
| `LAB2STARTUP_OPENREVIEW_FETCH_PROFILES` | `false` | Author profile lookups (rate-limit prone; Perplexity resolves affiliations instead) |
| `LAB2STARTUP_OPENREVIEW_REQUEST_DELAY` | `1.0` | Delay between API requests (seconds) |
| `LAB2STARTUP_OPENREVIEW_MAX_RETRIES` | `6` | Retries on 429s |

### OpenAlex (systems/security conferences)

| Variable | Default | Purpose |
|----------|---------|---------|
| `LAB2STARTUP_OPENALEX_CONFERENCE` | `NeurIPS` | Conference name |
| `LAB2STARTUP_OPENALEX_YEAR` | `2024` | Publication year filter |
| `LAB2STARTUP_OPENALEX_SEARCH` | — | Optional title search |
| `LAB2STARTUP_OPENALEX_TOPICS` | — | Comma-separated topic keyword filters |
| `LAB2STARTUP_OPENALEX_WORK_IDS` | — | Explicit OpenAlex work IDs |
| `LAB2STARTUP_OPENALEX_MAX_RESULTS` | `50` | Max papers to fetch |
| `LAB2STARTUP_OPENALEX_MAILTO` | — | Email for the OpenAlex polite pool |

## Optional supplements

### GitHub open-source signals

| Variable | Default | Purpose |
|----------|---------|---------|
| `LAB2STARTUP_GITHUB_ENABLED` | `false` | Search GitHub repos related to paper titles |
| `LAB2STARTUP_GITHUB_TOKEN` | — | Personal access token (better rate limits) |
| `LAB2STARTUP_GITHUB_MIN_STARS` | `5` | Minimum stars to emit a signal |
| `LAB2STARTUP_GITHUB_MAX_REPOS_PER_PAPER` | `2` | Max GitHub signals per paper |
| `LAB2STARTUP_GITHUB_REQUEST_DELAY` | `0.5` | Delay between API requests (seconds) |

### Semantic Scholar enrichment

| Variable | Default | Purpose |
|----------|---------|---------|
| `LAB2STARTUP_SEMANTIC_SCHOLAR_ENABLED` | `false` | Citation counts and author profiles |
| `LAB2STARTUP_S2_API_KEY` | — | API key for higher rate limits |
| `LAB2STARTUP_S2_FETCH_AUTHORS` | `true` | Fetch author h-index and citation totals |
| `LAB2STARTUP_S2_REQUEST_DELAY` | `1.1` | Delay between API requests (seconds) |

## Post-pipeline agents

| Variable | Default | Purpose |
|----------|---------|---------|
| `LAB2STARTUP_THESIS_FIT_ENABLED` | `true` | Fund thesis-fit assessment after each run |
| `LAB2STARTUP_DIFF_ENABLED` | `true` | Run-to-run diff vs prior complete run |
| `LAB2STARTUP_THESIS_SONAR_MIN_SCORE` | `60` | Min score for Sonar-assisted thesis checks |
| `LAB2STARTUP_THESIS_SONAR_MAX_CALLS` | `30` | Cap Sonar calls for thesis fit |

## Pipeline disk cache (development mode)

| Variable | Default | Purpose |
|----------|---------|---------|
| `LAB2STARTUP_PIPELINE_CACHE_ENABLED` | `true` | Cache dev-mode pipeline results in `.cache/` |
| `LAB2STARTUP_PIPELINE_CACHE_TTL_HOURS` | `168` | Cache TTL |
