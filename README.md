# UK Biotech Deal-Flow Scanner

Automated screening briefs on newly incorporated UK biotech companies, generated
from public filings and cross-referenced against academic/funding records. Built
as an internal ops tool: it sweeps Companies House each morning, enriches
directors against ORCID and UKRI Gateway to Research, generates a structured
screening brief for every new company, and publishes a dated digest to GitHub
Pages.

Live site: https://afc001.github.io/biotech-scanner/

---

## How it works

```
Companies House  ->  fetch.py     new incorporations (SIC 72110/72190),
(advanced search)                 deduped against data/seen.json, enriched with
                                   officers + incubator-address matching
                     |
                     v
ORCID / GtR /    ->  orcid.py,    director academic-credibility + UKRI funding-
website.py /         gtr.py,      history lookup (common-name-safe, never
repeat_founder.py    website.py,  guesses), a live-website check, and a
                     repeat_      Companies House officer-appointments check
                     founder.py   for a prior/current directorship at another
                                  company in this same target sector
                     |
                     v
Claude API       ->  generate.py  pass 1: each record -> structured JSON brief,
(pass 1)                          driven by biotech_brief_prompt.md (the same
                                  prompt used interactively); ORCID/GtR/repeat-
                                  founder confirmations explicitly move
                                  interest_score, and a found website is used
                                  substantively rather than defaulting to
                                  generic filler
                     |
                     v
Claude API +     ->  generate.py  pass 2 (opt-in, SCANNER_FETCH_WEB_SEARCH=1):
web_search           enrich_with_ any brief scoring 2+ gets re-run with a real
(pass 2, gated)      web_search   web search attached, re-checking the company
                                  and its directors; falls back to the pass-1
                                  brief unchanged on any failure -- see
                                  WEB_SEARCH_SPEC.md
                     |
                     v
                     render.py     JSON -> dated markdown + HTML digest in
                                  digests/, with a score-filter / group-by-week
                                  toolbar; archive index rebuilt
                     |
                     v
                     db.py         Every run also lands in data/scanner.db
                                  (SQLite) -- run/company/score history,
                                  queryable with scanner/stats.py or live in
                                  the browser via sql_explorer.html
                     |
                     v
GitHub Actions   ->  scan.yml      runs the above daily at 06:00 UTC (or on
                                  demand via workflow_dispatch, with an optional
                                  lookback-window override for backfills),
                                  commits the digest + database back to the
                                  repo, Pages serves it
```

Design principle: **generation (JSON) is separate from presentation (markdown/HTML).**
The raw briefs live in `data/briefs/`; the archive can be re-rendered any time
(new CSS, a toolbar feature, a typo fix) without re-calling the model — see
`rerender_digest.py`.

## Layout

| Path | Purpose |
|---|---|
| `biotech_brief_prompt.md` | System prompt + JSON schema + user template (used by the pipeline *and* interactively) |
| `scanner/config.py` | SIC codes, model, thresholds, incubator signals, feature toggles — all tunables |
| `scanner/fetch.py` | Companies House pull + seen-store dedupe + officer/incubator enrichment |
| `scanner/orcid.py` | Director academic-credibility lookup (ORCID Public API, OAuth client-credentials) |
| `scanner/gtr.py` | Director funding-history lookup (UKRI Gateway to Research, no auth needed) |
| `scanner/website.py` | Guessed-domain live-website check, no auth needed |
| `scanner/repeat_founder.py` | Director repeat-founder check via Companies House's own officer-appointments API, no separate auth needed |
| `scanner/generate.py` | Claude API call (pass 1), JSON parsing + validation, ORCID/GtR/repeat-founder badge computation, gated web-search enrichment (pass 2) |
| `scanner/render.py` | JSON -> dated md/html digest + archive index + score/week toolbar |
| `scanner/db.py` | SQLite schema + all reads/writes to `data/scanner.db` (stdlib `sqlite3`, no ORM) |
| `scanner/stats.py` | Funnel / precision / per-signal component-mean queries + CLI over `data/scanner.db` |
| `run.py` | Orchestrator / entry point |
| `.github/workflows/scan.yml` | Daily cron + manual trigger with backfill option |
| `data/seen.json` | Company numbers already processed (never reprocessed) |
| `data/briefs/*.json` | Raw JSON briefs, one file per run — re-renderable without re-calling the model |
| `data/scanner.db` | Queryable run/company/score history — see [`sql_explorer.html`](https://afc001.github.io/biotech-scanner/sql_explorer.html) |
| `digests/*.html` | Rendered daily digests (served by Pages) |
| `sql_explorer.html`, `vendor/` | Client-side SQL browser over `data/scanner.db` (SQLite compiled to WebAssembly via vendored `sql.js`) — read-only, runs entirely in the visitor's own browser, no backend |

### Utility scripts (all free — no Anthropic API calls)

| Script | Purpose |
|---|---|
| `dry_run_count.py --days N` | Count how many companies a longer lookback window would sweep up, and estimate cost, before spending anything on a backfill |
| `rerender_digest.py --date YYYY-MM-DD` (or `--all`) | Re-render an existing digest from its saved JSON, for free — use after any `render.py` change |
| `test_orcid_live.py` / `test_gtr_live.py` / `test_website_live.py` / `test_repeat_founder_live.py` | Diagnostic: run real ORCID/GtR/website/repeat-founder lookups against today's already-fetched companies, without touching `data/seen.json` |
| `migrate_history.py` | One-off, idempotent: import all historical `data/briefs/*.json` into `data/scanner.db` |
| `label_history.py` | Interactive CLI: label surfaced companies relevant/noise for the precision stats in `scanner/stats.py` |

## Running it

### Local
```bash
pip install -r requirements.txt
cp .env.example .env      # then fill in your API keys
set -a; source .env; set +a
python run.py
```

### Keys
- **Companies House** (required): register an app at
  https://developer.company-information.service.gov.uk/ and create a key.
- **Anthropic** (required): https://console.anthropic.com/
- **ORCID** (optional — enables director academic-credibility enrichment):
  register a Public API client at https://orcid.org (Developer Tools) and set
  `ORCID_CLIENT_ID` / `ORCID_CLIENT_SECRET`. Degrades gracefully if unset —
  ORCID enrichment is just skipped, everything else runs as normal.
- **UKRI Gateway to Research**: no key needed at all — it's a fully open API.
  Set `SCANNER_FETCH_GTR=0` if you'd rather not make the extra calls.
- **Website check**: no key needed — plain HTTP GETs to guessed domains.
  Set `SCANNER_FETCH_WEBSITE=0` if you'd rather not make the extra calls.
- **Web-search enrichment** (optional, real cost, off by default): uses
  Claude's own web_search tool, no separate key needed. Set
  `SCANNER_FETCH_WEB_SEARCH=1` to enable a second pass on any brief scoring
  2+ — see [`WEB_SEARCH_SPEC.md`](WEB_SEARCH_SPEC.md) for how it works, real
  test findings, and cost estimate before you turn it on.

### In GitHub Actions
Add repo secrets (Settings -> Secrets and variables -> Actions): `CH_API_KEY`,
`ANTHROPIC_API_KEY`, and optionally `ORCID_CLIENT_ID` / `ORCID_CLIENT_SECRET`
(without these, ORCID silently no-ops on Actions runs even though it works
locally). The workflow reads them at run time; they are never committed.

To trigger a one-off backfill instead of waiting for the daily cron: Actions
tab -> "Daily biotech scan" -> "Run workflow" -> set `lookback_days` to the
window you want. Run `dry_run_count.py --days N` locally first to see the
cost before you do.

To backfill a specific past calendar window instead of "N days before today"
(e.g. the fortnight before your last backfill), fill in `incorporated_from`
and `incorporated_to` on the same "Run workflow" form instead of
`lookback_days` — both must be set together (`YYYY-MM-DD`), and they override
`lookback_days` entirely if present. Check the cost first with
`dry_run_count.py --from YYYY-MM-DD --to YYYY-MM-DD`.

To turn on web-search enrichment for a single manual run, set
`enable_web_search` to `1` on the same "Run workflow" form. To leave it on
permanently once you trust it, add a repository variable (Settings ->
Secrets and variables -> Actions -> Variables tab, not Secrets) named
`SCANNER_FETCH_WEB_SEARCH` set to `1` — the workflow checks that on every
scheduled run too. Read [`WEB_SEARCH_SPEC.md`](WEB_SEARCH_SPEC.md) first;
this one has a real, non-trivial cost.

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
- **A backfill lands 100+ companies under one date** — expected: `render_digest()`
  always stamps with the run date, so a wide lookback window produces one large
  digest rather than one per day. Use the digest page's score filter / group-by-
  incorporation-week toolbar to make it navigable.

## Signal status

**Built and live:**
- Incubator/cluster address matching (`config.INCUBATOR_SIGNALS`, 38 entries)
- Director academic-credibility check via ORCID, with common-name-safe severity
  tiering (never guesses — an ambiguous match is reported as inconclusive, not
  confirmed)
- Director funding-history check via UKRI Gateway to Research, same tiering, no
  auth required
- Both enrichment sources surface a visible ORCID/GtR badge on the digest page
  *and* explicitly move `interest_score` per an explicit scoring rule in the
  prompt — confirmed match required to raise the score and appear in
  `flags_positive`; absence of a match is never penalized; an ambiguous match is
  treated as inconclusive, not evidence either way
- Score-filter + group-by-incorporation-week toolbar on every digest page
  (client-side, no rebuild needed)
- Company website check (`scanner/website.py`) — guesses likely domains, does
  a plain HTTP GET, and feeds a real excerpt into the prompt when a live
  (non-parked) site is found; a system-prompt rule requires the model to use
  it substantively rather than defaulting to "Not observable at this stage."
  No search API, near-zero added cost. Helps some briefs a lot, does nothing
  for brand-new shells with no site yet — expected, not a gap.
- `rerender_digest.py` / `dry_run_count.py` — free utilities for re-rendering
  the archive and estimating backfill cost before spending anything
- Web-search enrichment (`scanner/generate.enrich_with_web_search`), gated to
  any brief scoring 2+ (opt-in via `SCANNER_FETCH_WEB_SEARCH=1`) — a second,
  real Claude web_search pass that re-checks the company and its directors.
  This is what actually caught Vectis Biosciences (pass-1 score 2/5, missed
  by every free signal) and, in real testing, correctly downgraded a
  false-positive "academic founder" signal on another company once search
  revealed the directors were the host organisation's own executives. Full
  design, config, and real test findings in
  [`WEB_SEARCH_SPEC.md`](WEB_SEARCH_SPEC.md); example output on
  [`examples.html`](examples.html).
- Repeat-founder check (`scanner/repeat_founder.py`) — follows each
  director's own Companies House officer-appointments link (no separate
  registration, reuses `CH_API_KEY`) to check for a prior/current
  directorship at another company in this same target sector. A confirmed
  same-sector match raises `interest_score` and must appear in
  `flags_positive`, the same rule strength as ORCID/GtR. A director holding
  several other *active* directorships at once is flagged separately as an
  "advisor pattern" — an explicitly two-sided signal (well-connected, but
  likely not day-to-day operational here), woven into the narrative fields
  rather than either flags list.
- Queryable run history (`scanner/db.py`, `data/scanner.db`) — every run's
  fetch/company/score data lands in a committed SQLite database (stdlib
  `sqlite3`, no ORM), migrated from the full pre-database history via
  `migrate_history.py`. `scanner/stats.py` computes funnel, precision, and
  per-signal component-mean statistics (which signals actually separate
  hand-labelled relevant companies from noise — `label_history.py` is the
  CLI for producing those labels). [`sql_explorer.html`](https://afc001.github.io/biotech-scanner/sql_explorer.html)
  runs real, read-only SQL against the same database entirely client-side
  (SQLite compiled to WebAssembly via a vendored `sql.js`) — no backend,
  nothing leaves the visitor's browser.

**Not yet built (future ideas):**
- Director cross-references beyond ORCID/GtR: e.g. same person also appearing
  at a university tech-transfer office (Oxford University Innovation, Cambridge
  Enterprise) = likely spinout signal
- Persons with Significant Control (PSC) — Companies House exposes actual
  equity/control ownership via a separate, free endpoint, not used yet; would
  reveal prior institutional backing (a VC fund already listed as a PSC) or a
  nominee/shell structure
- SIC combinations as a signal — e.g. an R&D code alongside a manufacturing
  code might suggest therapeutics ambition rather than services; not pursued
  yet
- Project-level Innovate UK / UKRI grant data (title, abstract, award amount) —
  only the director-*name* lookup via GtR is wired in today; the user message
  template has a conditional section ready for this the day it's built
- Thesis-configurable scoring — externalise the rubric so "top scorers" and
  "credible-founder wildcards" become separate filtered views
- Real alerting — email/Slack the daily hot-list, not just a console summary
