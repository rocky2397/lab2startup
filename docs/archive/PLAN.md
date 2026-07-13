# Lab2Startup — Production Roadmap

Move from prototype (mock JSON + dashboard-as-batch-job) to a **monthly conference sourcing tool** you run before NeurIPS, ICML, etc.

## Target workflow

```bash
# Once a month, before a conference
lab2startup-run --conference NeurIPS --year 2025 --paper-source openreview

# Review results (reads from SQLite, instant)
python run_dashboard.py
```

Mock data stays in `tests/fixtures/` for CI and docs — not used in production.

---

## Steps

| Step | Status | Description |
|------|--------|-------------|
| **11** | **Done** | Run model + SQLite persistence |
| **12** | **Done** | CLI `run_pipeline.py` / `lab2startup-run` |
| **13** | **Done** | Production config (`LAB2STARTUP_MODE=production`) |
| **14** | **Done** | Dashboard reads stored runs (selector) |
| **15** | **Done** | Backtrace fund profile (`funds/backtrace.yaml`) |
| **16** | Planned | **Diff Agent** — run vs prior run (no LLM) — see [PLAN_DIFF_THESIS.md](PLAN_DIFF_THESIS.md) |
| **17** | Planned | **Thesis Fit Agent** — Backtrace EU + infra fit (rules + gated Sonar) — see [PLAN_DIFF_THESIS.md](PLAN_DIFF_THESIS.md) |

---

## Step 11 — Run model + SQLite

- `PipelineRun` metadata: conference, year, status, config, timestamps
- Full pipeline snapshot stored as JSON per run
- DB path: `LAB2STARTUP_DB_PATH` (default `.cache/lab2startup.db`)

## Step 12 — CLI orchestrator

```
lab2startup-run \
  --conference NeurIPS \
  --year 2025 \
  --paper-source openreview \
  [--fund backtrace] \
  [--topics "AI agents,ML systems"]
```

Stages logged: ingest → enrich → signals → score → persist.

## Step 13 — Production mode

| | Development | Production |
|---|-------------|------------|
| `LAB2STARTUP_MODE` | `development` | `production` |
| Paper source default | `json` | `openreview` |
| Mock signals | yes (tests) | **never** |
| Dashboard data | live pipeline or cache | **SQLite runs** |

## Step 14 — Dashboard as viewer

- Sidebar: select run (`NeurIPS 2025 — May 24`)
- **Refresh live data** → runs CLI pipeline in-process (or link to CLI)
- No mock JSON on production path

## Step 15 — Backtrace fund profile

`funds/backtrace.yaml` defines:

- **Conferences in scope:** NeurIPS, ICML, MLSys, OSDI, SOSP, NSDI, USENIX Security, ICSE
- **Topic scoring:** boosts AI agents / ML systems; penalizes biotech AI
- **Perplexity context:** Backtrace infrastructure thesis injected into founder search
- **Paper filter:** excludes biotech/drug-discovery papers after fetch

Set `LAB2STARTUP_FUND=backtrace` (default).

## Step 16 — Diff Agent

Compare run N vs the prior complete run for the same conference, year, and fund profile. Detect new researchers, score changes, new signals, affiliation moves, and recommendation upgrades. **No LLM** — pure snapshot diff stored in SQLite.

See [PLAN_DIFF_THESIS.md](PLAN_DIFF_THESIS.md) for schema, dashboard UX, and implementation phases.

## Step 17 — Thesis Fit Agent

Backtrace-specific post-scoring pass: **European nexus** + **infrastructure layer** fit. Rules for all candidates; optional **Perplexity Sonar** only for high-score or `unclear` cases. Separate badge from startup likelihood score.

See [PLAN_DIFF_THESIS.md](PLAN_DIFF_THESIS.md) for fund YAML extensions, cost caps, and orchestration.

---

## Architecture

```
CLI run ──► OpenReview/OpenAlex ──► Pipeline ──► Thesis Fit ──► Diff ──► SQLite
                                              │
Dashboard ◄───────────────────────────────────┘
```

Integrations (no HTML scraping):

- **Papers/authors:** OpenReview (primary), OpenAlex (fallback)
- **Affiliations:** OpenReview profiles
- **Citations:** Semantic Scholar
- **OSS signals:** GitHub
- **Founder signals:** Perplexity Sonar

## Cost control (production)

1. Fetch all papers/authors (cheap)
2. GitHub on all papers (moderate)
3. Perplexity on capped subset (`LAB2STARTUP_PERPLEXITY_MAX_RESEARCHERS`)
