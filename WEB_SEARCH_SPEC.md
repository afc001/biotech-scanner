# Web-search enrichment, gated to 2+ scorers

**Status: built and live in the code, opt-in via config.** Implemented and
tested 29 July 2026, including three real (paid) end-to-end test calls
against real companies — see "Real test findings" below. Not yet enabled on
the daily cron (`FETCH_WEB_SEARCH` defaults to `0`) — deliberately, so you
can review a few runs' worth of output before trusting it unattended, same
caution as when ORCID/GtR were first added.

Companion to the long-term goal discussed on 29 July 2026: fully automated
daily runs that surface a small, credible shortlist (~5/month) to a VC,
rather than a long list of "Not observable at this stage" briefs.

## Why gate it to 2+ scorers, not 3+ or 4+

Vectis Biosciences — the company that started this whole conversation — got
an automated pass-1 score of **2/5**. It took a human manually checking the
company's own website to see the real product. If the gate were set at 3+ or
4+ (the original draft of this spec proposed 3+), the exact case that proved
the value of looking further would have been skipped by the automation. The
threshold is 2 because that's the real score of the real company that
justified building this feature — set any higher and you filter out the
target case.

In practice this means most companies that aren't outright empty shells
will trigger a search pass — noticeably more volume than the original 3+
draft assumed. See "Cost estimate" below for what that means in practice.

## What it uses: Claude's own web_search tool, not a separate search API

Anthropic's Messages API has a built-in server-side web search tool
(`web_search_20250305` at time of writing — check `platform.claude.com/docs`
for the current version string, this moves). Pricing: **$10 per 1,000
searches ($0.01/search)**, plus normal token costs for the results Claude
reads. No separate API key, no separate vendor — it's one more `tools` entry
on a `messages.create()` call, with built-in citations (url, title per
result), which is why every search-enriched brief now has a "Sources" line.

`scanner/generate.py`:
- `WEB_SEARCH_TOOL_TYPE = "web_search_20250305"`
- `_web_search_rule()` — the extra system-prompt instruction appended only
  for the search pass (search company + directors, use what's found, don't
  extrapolate, say so if a link is plausible but unconfirmed)
- `enrich_with_web_search(record, brief, client, system, template)` — the
  second-pass call for one company. Falls back to the unchanged pass-1
  brief with `search_enriched: False` on ANY failure.
- `enrich_all_with_web_search(records, briefs)` — filters by
  `config.WEB_SEARCH_ENRICH_THRESHOLD` and calls the above for each brief
  that clears it.
- `_extract_final_text()` — a web-search response can interleave several
  `text` blocks (the model narrating "I'll search for...") with
  `server_tool_use`/`web_search_tool_result` blocks; only the LAST text
  block is the actual JSON brief. This is a real, separate function from
  `_extract_text()` (used by pass 1), which joins ALL text blocks — correct
  for a no-tool call, wrong here.
- `_extract_sources()` — pulls cited URLs out of `web_search_tool_result`
  blocks, deduped, for the Sources line.

`run.py` calls `generate.enrich_all_with_web_search()` right after pass 1,
gated by `config.FETCH_WEB_SEARCH`. `render.py` shows a "🔍 Web-verified"
pill and a Sources line on any enriched brief (`_search_badge_html`,
`_sources_html`/`_sources_md`).

## Config (`scanner/config.py`)

```python
FETCH_WEB_SEARCH = os.getenv("SCANNER_FETCH_WEB_SEARCH", "0") == "1"  # opt-in
WEB_SEARCH_ENRICH_THRESHOLD = int(os.getenv("SCANNER_WEB_SEARCH_THRESHOLD", "2"))
WEB_SEARCH_MAX_USES = int(os.getenv("SCANNER_WEB_SEARCH_MAX_USES", "4"))
```

To turn it on for a real run: set `SCANNER_FETCH_WEB_SEARCH=1` in your `.env`
(local) or as an Actions env var/secret override (see `scan.yml`).

## Real test findings (three live calls, 29 July 2026)

Three real companies were run through `enrich_with_web_search()` for real —
not mocked — to check the feature actually works before enabling it. Full
output is on `examples.html`. Findings:

- **A brand-new small company's own website is not reliably found by a
  generic search query alone.** A first test on Vectis, searching cold
  (no pre-supplied website), did not surface vectisbiosciences.com even
  though the site is live and fetchable directly — small/new sites often
  aren't well-indexed yet. This is why the existing deterministic
  guessed-domain check (`scanner/website.py`) stays as pass 1's job and
  isn't replaced by search — when pass 1 already found the site, its
  content is included in the pass-2 prompt too (via the same
  `_format_website()` used in pass 1), and the model DOES use it
  substantively (confirmed in the second, realistic test below).
- **Search is much better at verifying established academics than quieter
  founders.** For KYTFOX, search found a genuine, verifiable University of
  Leeds staff page for director Heiko Wurdak, plus his ResearchGate, PubMed
  and Google Scholar records, AND an adjacent real project ("Assemblify")
  that no other signal in the pipeline could have surfaced. For Vectis,
  search could NOT verify director Alice Condrat's claimed Oxford academic
  background — the score correctly stayed cautious rather than being talked
  up. Older, more publication-heavy academics leave more of a findable
  trail than newer or quieter founders; that's a real, useful thing to know
  about this feature's limits, not a bug.
- **Correction, important:** an earlier "manual analyst follow-up" for
  Vectis (written in a prior session, before this feature existed) asserted
  Alice Condrat was "an Oxford biochemistry graduate... ties to the
  University pharmacology department" based on a stated LinkedIn read. Real
  web search could not independently verify this. That earlier claim should
  be treated as unconfirmed, not fact — `examples.html` has been rewritten
  to use only what this session's real, tested search pass actually found,
  which is more cautious (score stays at 2/5, team provenance marked
  unverifiable).
- **Search can downgrade a score, correctly.** NON-OXIDE CERAMIC SYSTEMS
  looked promising on free signals alone (ORCID-confirmed academic,
  specialist Lucideon address) — pass-1 score 3. Real search found that
  both directors are very likely Lucideon's own CEO and Finance Director,
  meaning this reads as an internal corporate spin-out vehicle rather than
  an independent investable startup. The enriched score correctly dropped
  to 2. This is the single best argument for the feature: it caught a
  false-positive "academic founder" signal a VC could otherwise have acted
  on.

## Cost estimate (revised for the 2+ threshold)

The 2+ threshold means most companies that aren't outright empty shells will
trigger a search pass — this is deliberately much wider than the original
3+/4+ draft. At roughly $0.01-0.03 per company in search + token cost, a
normal day (10-20 new incorporations) costs perhaps $0.20-0.60 extra; a wide
2-week backfill (100-200 companies) costs a few dollars extra on top of the
existing pass-1 generation cost. Still small in absolute terms, but a real
step up from the original 3+/4+-gated design — worth watching actual spend
for a week or two with `FETCH_WEB_SEARCH=1` before treating it as
set-and-forget.

## Connecting to the "~5 credible companies/month" goal

This feature only improves and re-scores briefs — it doesn't by itself cap
what gets shown to a VC. That's a separate, smaller step once you're happy
with the search quality: define a final "alert" tier as
`interest_score >= config.INTEREST_ALERT_THRESHOLD` (4) *after* search
enrichment, and only forward those to whatever the eventual "real alerting"
mechanism turns out to be (see README's "Not yet built" list — email/Slack
digest is the obvious next step, not yet built either). Two independently
gated stages — 2+ triggers a search look, 4+ (post-search) triggers a VC
alert — keeps the VC's attention on the minority of companies actually worth
their time, even though search itself now runs on the majority.

## Not addressed by this feature (separate future work)

- LinkedIn specifically is still not directly queryable — Claude's web
  search only surfaces LinkedIn results that are publicly indexed and come
  up in a general search, same limitation as before, just now backed by a
  real search instead of a guessed-domain HTTP GET.
- Director cross-references against specific university tech-transfer
  offices (Oxford University Innovation, Cambridge Enterprise) aren't
  explicitly prompted for, though the KYTFOX/Assemblify result shows the
  model will sometimes surface adjacent spinout ventures unprompted anyway.
- No `allowed_domains`/`blocked_domains` restriction yet — unrestricted
  search worked fine in testing; revisit only if results turn out noisy at
  scale.
