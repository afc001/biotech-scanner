# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Portfolio project note

This is a portfolio project. Clarity and demonstrable methodology (why a signal is scored the way it is, how failures are handled) matter more than feature completeness.

## Pipeline (end to end)

Scheduled GitHub Actions run (`.github/workflows/scan.yml`, daily 06:00 UTC, or manual `workflow_dispatch`) invokes `run.py`, which:

1. Fetches recently incorporated companies from the Companies House Advanced Search API for the configured SIC codes and date window.
2. Filters/enriches by SIC code, incubator address match, and director credentials (ORCID, UKRI GtR) and live website.
3. Sends each candidate to Claude for a structured relevance/interest score and screening brief (JSON), with an optional gated second web-search pass for higher-scoring companies.
4. Renders the JSON briefs into a dated HTML/markdown digest and rebuilds the archive index.
5. Commits `data/` and `digests/` back to the repo; GitHub Pages serves `digests/`.

`package.json` in the repo root is unrelated to the app (just the `@anthropic-ai/claude-code` CLI dependency) — this is a pure Python project.

## Module roles (`scanner/`)

- **`fetch.py`** — Companies House client: SIC-code search, officer lookup, incubator address matching, `data/seen.json` dedupe store.
- **`orcid.py`** — director academic-credibility lookup via ORCID Public API (OAuth client credentials; no-ops if unset).
- **`gtr.py`** — director funding-history lookup via UKRI Gateway to Research (no auth needed).
- **`website.py`** — guessed-domain live-website check via plain HTTP GET.
- **`generate.py`** — builds prompts from `biotech_brief_prompt.md`, calls the Claude API (pass 1: scoring/brief; pass 2, opt-in: web-search re-check), parses/validates JSON.
- **`render.py`** — turns brief JSON into dated md/html digests plus the archive index.
- **`config.py`** — all tunables (SIC codes, model, thresholds, feature toggles, incubator list), overridable via `SCANNER_*` env vars.
- **`run.py`** — orchestrator: fetch → generate → render → mark-seen → print alert summary. Companies are only written to `data/seen.json` *after* a brief is generated and saved, so a crash mid-run leaves them eligible for retry rather than silently dropped.

## Running locally

```bash
pip install -r requirements.txt
cp .env.example .env      # then fill in keys below
set -a; source .env; set +a

python run.py                                # full run, real Anthropic API cost
python dry_run_count.py --days N             # cost/volume estimate before a backfill
python rerender_digest.py --date YYYY-MM-DD  # free re-render from saved JSON
python test_orcid_live.py / test_gtr_live.py / test_website_live.py   # free diagnostics
```

Required: `CH_API_KEY` (Companies House), `ANTHROPIC_API_KEY`. Optional: `ORCID_CLIENT_ID`/`ORCID_CLIENT_SECRET` (else ORCID enrichment is skipped), plus `SCANNER_*` overrides in `config.py`.

## Known constraints

- **SIC 21100 returns a persistent 404** from Companies House. Dropped from default `SIC_CODES`; if re-added via `SCANNER_SIC_CODES`, `fetch.get_new_companies()` catches the failure per-SIC-code, logs it, and continues to the next code rather than aborting the run.
- **Extended thinking is disabled** (`thinking={"type": "disabled"}`) on the JSON-extraction calls in `generate.py` — a `ThinkingBlock` in the response breaks naive parsing, so content blocks are filtered by `block.type == "text"` before parsing (`_extract_text` / `_extract_final_text`).
- **Companies House auth** is HTTP Basic with the API key as username and an empty password (`fetch._auth`) — not a bearer token.
