# UK Biotech Deal-Flow Scanner

Automated screening briefs on newly incorporated UK biotech companies, generated
from public filings. Built as an internal ops tool: it sweeps Companies House
each morning, generates a structured screening brief for every new company, and
publishes a dated digest to GitHub Pages.

Live site: https://afc001.github.io/biotech-scanner/

---

## How it works

```
Companies House  ->  fetch.py     new incorporations (SIC 72110/72190/21100),
(advanced search)                 deduped against data/seen.json, enriched with officers
                     |
                     v
Claude API       ->  generate.py  each record -> structured JSON brief,
                                  driven by biotech_brief_prompt.md (the same
                                  prompt used interactively)
                     |
                     v
                     render.py     JSON -> dated markdown + HTML digest in digests/,
                                  archive index rebuilt
                     |
                     v
GitHub Actions   ->  scan.yml      runs the above daily at 06:00 UTC, commits the
                                  digest back to the repo, Pages serves it
```

Design principle: **generation (JSON) is separate from presentation (markdown/HTML).**
The raw briefs live in `data/briefs/`; the archive can be re-rendered any time
without re-calling the model.

## Layout

| Path | Purpose |
|---|---|
| `biotech_brief_prompt.md` | System prompt + JSON schema + user template (used by the pipeline *and* interactively) |
| `scanner/config.py` | SIC codes, model, thresholds, paths — all tunables |
| `scanner/fetch.py` | Companies House pull + seen-store dedupe + officer enrichment |
| `scanner/generate.py` | Claude API call, JSON parsing + validation |
| `scanner/render.py` | JSON -> dated md/html digest + archive index |
| `run.py` | Orchestrator / entry point |
| `.github/workflows/scan.yml` | Daily cron |
| `data/seen.json` | Company numbers already processed (never reprocessed) |
| `data/briefs/*.json` | Raw JSON briefs, one file per run |
| `digests/*.html` | Rendered daily digests (served by Pages) |

## Running it

### Local
```bash
pip install -r requirements.txt
cp .env.example .env      # then fill in your two API keys
set -a; source .env; set +a
python run.py
```

### Keys
- **Companies House**: register an app at
  https://developer.company-information.service.gov.uk/ and create a key.
- **Anthropic**: https://console.anthropic.com/

### In GitHub Actions
Add both keys as repository secrets (Settings -> Secrets and variables ->
Actions): `CH_API_KEY` and `ANTHROPIC_API_KEY`. The workflow reads them at run
time; they are never committed.

## Failure modes

- **A run fails** — `run.py` exits non-zero; GitHub emails the repo owner
  automatically for failed scheduled workflows.
- **Companies House rate limit (600 req / 5 min)** — `fetch.py` backs off and
  retries; officer enrichment is the main request multiplier, toggle it off with
  `SCANNER_FETCH_OFFICERS=0` if needed.
- **Model returns malformed JSON** — `generate.py` retries once, then raises with
  the offending company named.
- **A missed day** — `LOOKBACK_DAYS=2` gives a one-day overlap; the seen-store
  prevents duplicates, so a skipped run self-heals on the next.

## Roadmap (V2)

1. **Founder-credibility enrichment** — cross-reference directors against ORCID,
   university tech-transfer (Oxford University Innovation, Cambridge Enterprise),
   GitHub, and UKRI grant records to produce a *traceable* credibility score.
   This powers a "wildcard" filter: weak company signal, strong verifiable founder.
2. **Thesis-configurable scoring** — externalise the rubric so "top scorers" and
   "credible-founder wildcards" are separate filtered views.
3. **More sources** — Innovate UK / UKRI Gateway to Research grants, incubator
   portfolio pages, accelerator cohort lists.
4. **Real alerting** — email/Slack the daily hot-list, not just a console summary.
