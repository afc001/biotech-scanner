# Biotech Scanner — Brief Template & Claude API Prompt

## The system prompt

```
You are an analyst at a UK life-sciences venture fund writing one-page
screening briefs on early-stage biotech companies. You will receive raw
data about a company (Companies House record, Innovate UK grant data,
or both). Write a concise, sceptical brief for an investor deciding
whether the company merits a closer look.

Rules:
- Use ONLY the data provided. Never invent facts. If a section has no
  supporting data, write "Not observable at this stage" — do not pad.
- Be direct and plain-English. No hype, no hedging filler.
- Flag signals explicitly (e.g. incubator address, academic founder,
  ORCID/GtR-confirmed investigator, specialist SIC code, non-dilutive
  funding).
- Keep the whole brief under 300 words.
- Respond ONLY with valid JSON matching the schema below. No markdown
  fences, no preamble.
```

## Rules appended at runtime (NOT in the fenced block above)

`scanner/generate.py`'s `load_prompt()` appends several more rules to the
system prompt in code, after the schema. They're not in the fenced
block above because they reference config values / exact enrichment
wording that only make sense assembled in Python — but they ARE sent
to the model on every real run, so if you're ever pasting this prompt
manually (e.g. to test in the API console), add these too or your
manual run will silently behave differently from the pipeline:

- **Cost-control rule:** if `interest_score` is below
  `config.INTEREST_ALERT_THRESHOLD` (currently 4), set `"unknowns"` to
  `[]` — don't spend words speculating open questions for low-signal,
  low-interest companies.
- **ORCID / GtR scoring rule:** a director note reading "ORCID
  CONFIRMED" or "GtR CONFIRMED" means we independently verified a
  genuine, unique match (real academic publication record, or UKRI/
  Innovate UK grant history) — this MUST be named in `flags_positive`
  and MUST raise `interest_score` vs. an otherwise-identical company
  with no such confirmation. Conversely: absence of a match must NOT
  lower the score or add a `flags_negative` entry (most legitimate
  founders have neither), and an ambiguous "possible matches,
  unverified" note (common name, too many candidates to confirm) must
  be treated as inconclusive — neither a positive nor a negative
  signal, and not mentioned in either flags list at all.
- **Repeat-founder scoring rule (same sector):** a director note reading
  "REPEAT FOUNDER CONFIRMED (same sector)" means Companies House's own
  officer-appointment records confirm this director holds/held another
  directorship at a company in the same target sector — MUST be named
  in `flags_positive` and MUST raise `interest_score`, same strength as
  the ORCID/GtR rule above. Absence of other appointments, or
  appointments outside the target sector, must NOT lower the score —
  most first-time founders genuinely have none.
- **Repeat-founder scoring rule (advisor/portfolio pattern):** a director
  note reading "ADVISOR PATTERN" means this director holds
  `config.REPEAT_FOUNDER_ADVISOR_THRESHOLD` (currently 4) or more other
  *active* directorships at once — an explicitly two-sided signal
  (well-connected, but likely not day-to-day operational here), woven
  into `team_provenance`/`stage_signal` rather than either flags list.
- **Website rule:** if a COMPANY WEBSITE section is present (a real
  live site was found — see `scanner/website.py`), treat it as
  genuine first-party company communication and use it substantively
  in `one_liner`/`science`/`stage_signal`/`funding` — do NOT default
  to "Not observable at this stage" for something the excerpt already
  states. Do not extrapolate beyond what the excerpt actually says,
  and call out vague marketing fluff as such rather than inventing
  specifics.

See `generate.py::load_prompt()` for the exact wording actually sent —
treat that function as the source of truth if this doc and the code
ever disagree.

A fifth rule (`generate._web_search_rule()`) is appended only for the
gated second (web-search) pass, not on every call — see "Signal
status" below and `WEB_SEARCH_SPEC.md` for the full design.

## The JSON schema (structured output)

```json
{
  "company_name": "",
  "incorporated": "YYYY-MM-DD",
  "one_liner": "What the company does in one plain-English sentence",
  "science": "Mechanism/platform/modality if inferable; else not observable",
  "stage_signal": "e.g. day-one incorporation / grant-funded project / unknown",
  "team_provenance": "Directors, likely academic links, prior companies",
  "location_signal": "Registered address — incubator/cluster/generic",
  "funding": "Grant amounts, source, dilutive vs non-dilutive",
  "flags_positive": ["list of promising signals"],
  "flags_negative": ["list of concerns or gaps"],
  "interest_score": "1-5 with one-line justification",
  "unknowns": ["key questions a first call would need to answer"]
}
```

## The user message template

Filled in per-company by `generate.py::build_user_message()`.

```
COMPANIES HOUSE RECORD:
Name: {name}
Incorporated: {date}
SIC codes: {sic_codes}
Registered address: {address}
Directors: {officers with occupations and other appointments if fetched}

INNOVATE UK / UKRI DATA (if matched):
Project title: {title}
Abstract: {abstract}
Award: £{amount}, {start} to {end}
Lead organisation: {org}
```

## Rendering to the digest page

`render.py` takes the JSON and renders it to markdown + HTML for the
daily digest — score and one-liner as the heading, flags as bullets,
unknowns at the bottom. The HTML digest also gets a score-filter /
group-by-incorporation-week toolbar (client-side JS, no rebuild
needed). Keeping generation (JSON) separate from presentation (`render.py`)
means the whole archive can be re-rendered any time — new CSS, a typo
fix, a new toolbar feature — without re-calling the model. See
`rerender_digest.py` for a zero-cost way to do exactly that against
already-saved briefs.

## Signal status

**Built and live:**
- Incubator/cluster addresses — `config.INCUBATOR_SIGNALS` (38 entries:
  BioEscalator, Stevenage Bioscience Catalyst, Babraham, Alderley Park,
  White City, Milner Therapeutics, and more), matched in `fetch.py`'s
  `_match_incubator()` and surfaced in the prompt via
  `generate._format_address()`.
- Director academic credibility — ORCID lookup (`scanner/orcid.py`),
  confirmed/ambiguous/no_match, with common-name severity tiering.
- Director funding history — UKRI Gateway to Research lookup
  (`scanner/gtr.py`), same tiering, no auth needed.
- Both enrichment sources feed a visible ORCID/GtR pill badge on the
  rendered digest page (`render.py`) AND explicitly move
  `interest_score` per the scoring rule above — not just descriptive
  text a model might or might not act on.
- Company website check (`scanner/website.py`) — guesses likely
  domains from the company name, does a plain HTTP GET, and feeds a
  real excerpt into the prompt if a live (non-parked) site is found.
  Addresses the "Non-Oxide Ceramics Limited" problem: for a genuinely
  day-1 incorporation there's often nothing beyond the name to work
  with, and this is the cheapest, most deterministic way to check for
  more without a search API. Helps some briefs a lot, does nothing for
  brand-new shells with no site yet — that's expected, not a gap.
- Web-search enrichment (`generate.enrich_with_web_search`) — a gated
  second Claude pass with the real `web_search` tool attached, for any
  brief scoring 2+ (opt-in via `SCANNER_FETCH_WEB_SEARCH=1`, off by
  default). Real-tested 29 July 2026: reliably surfaces established
  academics' staff pages/publication records and can find adjacent real
  ventures search wouldn't otherwise catch, but does not reliably find
  a brand-new small company's own website from a cold generic query
  (that stays website.py's job — its result is fed into the pass-2
  prompt too). Also caught a real false-positive: a company whose
  ORCID-confirmed "academic founder" turned out, per search, to likely
  be the host organisation's own CEO. Full design, findings, and cost
  estimate in [`WEB_SEARCH_SPEC.md`](WEB_SEARCH_SPEC.md); example
  output in `examples.html`.

**Not yet built (future ideas):**
- Director cross-references beyond ORCID/GtR: e.g. same person also
  appearing at a university tech-transfer office (Oxford University
  Innovation, Cambridge Enterprise) = likely spinout signal.
- SIC combinations: 72110 + 21100 together suggests therapeutics
  ambition rather than services.
- Innovate UK competition names: "Biomedical Catalyst" awards are a
  strong quality filter, but Innovate UK/GtR project-level data isn't
  currently fetched at all (only the director-level GtR person lookup
  is wired in) — the {title}/{abstract}/{amount}/{org} placeholders in
  the user message template above are aspirational, not yet populated
  by any code.
