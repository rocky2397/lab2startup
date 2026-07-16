# Founder-detection eval

Measures the core quality metric of the product: **given only what a conference exposes (name, affiliation, paper title), does the pipeline detect researchers who verifiably founded companies — without flagging those who didn't?**

## Method

- `golden_set.json` holds ~24 researchers who published at NeurIPS in the last 10 years: half **verified founders** (stratified from famous — Mistral, Cohere — down to seed-stage, where detection is genuinely hard), half **verified non-founders** with matched profiles (professors and lab researchers, including deliberate hard negatives like prolific startup *advisors*).
- The harness builds `Paper` objects from the golden set and runs the **unmodified production pipeline** on them — same ingestion, identity gating, Perplexity signal search, and scoring as a real conference run. No golden-set metadata other than name/affiliation/paper reaches the pipeline.
- A researcher counts as "predicted founder" at two thresholds: **strict** (a `confirmed_founder` signal) and **lenient** (`confirmed_founder` or `possible_founder`).
- Reported: precision, recall, false-positive rate, accuracy at both thresholds, per-researcher outcomes, and named failure modes.

## What this does and doesn't measure

- It measures **detection of public evidence**, not prophecy: the pipeline searches the live web, so it can only find foundings that left a public trace. That matches the product claim ("surface founder signals early"), not "predict foundings before any signal exists."
- **Temporal leakage is inherent and acknowledged, not hidden.** Golden-set papers date back to 2016, and the agent investigating their authors sees the 2026 web — including companies founded years after the paper. That is fine for a detection eval, but it means these numbers cannot be read as a backtest of *prediction* ("could the system have foreseen this founding from the paper alone?"). A true backtest is not achievable with this architecture: even date-restricted search can't remove the outcome from the LLM's training data, and a hindsight-selected golden set encodes the future in its sampling. The only clean predictive test is a forward test — score a current cohort, freeze, grade in 2–3 years.
- Famous founders (Mistral tier) are near-trivial for a web-search agent; the informative cases are the seed-stage founders and the hard negatives. Read the per-researcher table, not just the headline number.
- Ground truth is time-sensitive (people found companies, return to big labs, companies get acquired). `as_of` in the golden set records when labels were checked.

## Verification protocol

Every entry starts as `verified: false`. Before publishing results, a human must check each entry and flip the flag:

1. **Founders**: company exists, this person is publicly named a co-founder, `evidence_url` works.
2. **Non-founders**: no public founding evidence as of the `as_of` date (advisor/angel roles don't count).
3. **Papers**: the title is a real accepted paper at that venue/year with this person as an author (check OpenReview / proceedings).

The report banner warns when unverified entries remain.

## Prefilter note (agentic mode)

The eval runs agentic mode with `prefilter_min_score=0` so every golden-set researcher is investigated. With production defaults (threshold 20), all 24 golden-set researchers would be *skipped*: their papers carry generic ML/RL topics that score low against the fund's infra-weighted topic scores, and the synthetic papers have no citations or abstracts. Worth knowing when interpreting production recall: the deterministic prefilter is a real gate, and topic weights directly trade cost against recall.

## Running

```bash
python run_eval.py --dry-run          # who would be investigated; no API calls
python run_eval.py                    # one-shot Sonar mode (default)
python run_eval.py --agentic          # LangGraph + Agent API investigation path
```

Requires `LAB2STARTUP_PERPLEXITY_API_KEY`. Sonar mode makes ~1 query per researcher (~24 calls); agentic mode uses tiered multi-step budgets and costs more. Results land in `evals/results/` as markdown + JSON.
