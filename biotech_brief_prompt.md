# Biotech Scanner — Brief Template & Claude API Prompt

## The system prompt (paste into your API call)

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
  specialist SIC code, non-dilutive funding).
- Keep the whole brief under 300 words.
- Respond ONLY with valid JSON matching the schema below. No markdown
  fences, no preamble.
```

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

## The user message template (your script fills this in)

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

Your script takes the JSON and renders it to markdown for the daily
digest — score and one-liner as the heading, flags as bullets,
unknowns at the bottom. Keeping generation (JSON) separate from
presentation (markdown) means you can re-render the archive any time
without re-running the API.

## Signals worth encoding in the prompt over time (V2 notes)

- Incubator/cluster addresses: BioEscalator, Stevenage BioScience
  Catalyst, Babraham, Alderley Park, White City, Milner Therapeutics
- Director cross-references: same person appearing at a university
  tech-transfer office (Oxford University Innovation, Cambridge
  Enterprise) = likely spinout
- SIC combinations: 72110 + 21100 together suggests therapeutics
  ambition rather than services
- Innovate UK competition names: "Biomedical Catalyst" awards are a
  strong quality filter
