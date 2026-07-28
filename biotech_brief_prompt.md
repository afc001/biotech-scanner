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

`scanner/generate.py`'s `load_prompt()` appends two more rules to the
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

See `generate.py::load_prompt()` for the exact wording actually sent —
treat that function as the source of truth if this doc and the code
ever disagree.

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
