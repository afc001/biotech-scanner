"""Generate structured JSON briefs by passing records through the Claude API.

The prompt lives in biotech_brief_prompt.md — the SAME file used
interactively — so the pipeline and manual use never drift apart. We parse the
system prompt and the user-message template out of that file at runtime.
"""

from __future__ import annotations

import json
import re

from anthropic import Anthropic

from . import config

REQUIRED_KEYS = {
    "company_name",
    "incorporated",
    "one_liner",
    "science",
    "stage_signal",
    "team_provenance",
    "location_signal",
    "funding",
    "flags_positive",
    "flags_negative",
    "interest_score",
    "unknowns",
}


def _fenced_blocks(text: str) -> list[str]:
    """Return the contents of every ``` fenced block in the markdown file."""
    return re.findall(r"```[a-zA-Z]*\n(.*?)```", text, re.DOTALL)


def load_prompt() -> tuple[str, str]:
    """Extract (system_prompt, user_template) from the prompt markdown file.

    The markdown file has three fenced blocks: the system prompt, the JSON
    schema, and the user-message template. The system prompt tells the model
    to "respond with valid JSON matching the schema below" — but that schema
    lives in its own separate fenced block, so it must be folded into the
    system prompt here or the model never actually sees the required shape
    (it'll happily invent its own JSON structure instead).
    """
    text = config.PROMPT_FILE.read_text()
    blocks = _fenced_blocks(text)
    system = next((b for b in blocks if b.strip().startswith("You are an analyst")), None)
    schema = next((b for b in blocks if b.strip().startswith("{") and '"company_name"' in b), None)
    template = next((b for b in blocks if "COMPANIES HOUSE RECORD" in b), None)
    if not system or not schema or not template:
        raise RuntimeError("Could not parse system prompt / schema / user template from prompt file.")
    full_system = (
        f"{system.strip()}\n\n"
        f"Respond with a single JSON object matching EXACTLY this schema "
        f"(same keys, no additions, no omissions, no nesting):\n{schema.strip()}\n\n"
        f"Cost-control rule: if interest_score is below {config.INTEREST_ALERT_THRESHOLD}, "
        f'set "unknowns" to an empty list [] — do not spend words speculating open questions '
        f"for low-signal, low-interest companies. Only populate \"unknowns\" with genuine, "
        f"specific open questions once interest_score reaches {config.INTEREST_ALERT_THRESHOLD} or higher.\n\n"
        f"ORCID / GtR scoring rule: a director note reading \"ORCID CONFIRMED\" or \"GtR CONFIRMED\" "
        f"means we independently verified a genuine, unique match to a real academic publication "
        f"record (ORCID) or UKRI/Innovate UK grant history (GtR) — treat this as a real, meaningful "
        f"positive credibility signal. It MUST be named in flags_positive and MUST raise "
        f"interest_score versus an otherwise-identical company with no such confirmation. Conversely: "
        f"do NOT lower interest_score or add a flags_negative entry just because a director has NO "
        f"ORCID/GtR record — most legitimate founders have neither, so absence is not evidence of "
        f"anything. And a note reading \"possible matches, unverified\" is inconclusive by "
        f"construction (the name was too common to confirm) — do not treat it as either a positive "
        f"or a negative signal; do not mention it in flags_positive or flags_negative at all.\n\n"
        f"Repeat-founder scoring rule (same sector): a director note reading \"REPEAT FOUNDER "
        f"CONFIRMED (same sector)\" means we independently verified, via Companies House's own "
        f"officer-appointment records (not a name guess), that this director holds or held another "
        f"directorship at a company in the same target sector (SIC {', '.join(config.SIC_CODES)}) "
        f"this scanner covers, excluding the company being scanned itself. Treat this as a real, "
        f"meaningful positive credibility signal — prior experience actually building a company in "
        f"this exact sector. It MUST be named in flags_positive and MUST raise interest_score versus "
        f"an otherwise-identical company with no such confirmation. Conversely: do NOT lower "
        f"interest_score or add a flags_negative entry just because a director has no other "
        f"appointments, or only appointments outside the target sector — most legitimate first-time "
        f"founders genuinely have no prior directorships at all, and Companies House can split the "
        f"same real person across multiple different officer records for reasons that are not fully "
        f"documented, so a \"no other appointments found\" note is not proof of first-time-founder "
        f"status, only that none were found under this specific officer record. A note reading \"N "
        f"other appointment(s), none confirmed in target sector\" is informational only — do not "
        f"treat it as either a positive or a negative signal, and do not mention it in flags_positive "
        f"or flags_negative.\n\n"
        f"Repeat-founder scoring rule (advisor/portfolio pattern): a director note reading \"ADVISOR "
        f"PATTERN\" means this director currently holds {config.REPEAT_FOUNDER_ADVISOR_THRESHOLD} or "
        f"more other active directorships at once — a genuinely two-sided signal, NOT a simple "
        f"positive or negative, and NOT a flags_positive/flags_negative mandate like the rule above. "
        f"It suggests the director is well-connected and well-regarded (worth naming positively) but "
        f"also unlikely to be closely involved in this company's day-to-day running (a caveat worth "
        f"naming alongside it, not a red flag on its own) — weave both sides into team_provenance or "
        f"stage_signal, in whichever direction the rest of the evidence points, rather than adding it "
        f"to either flags list. As above, a director with fewer other active appointments than this "
        f"threshold is not evidence of anything — most legitimate founders simply have few or no "
        f"other directorships.\n\n"
        f"Website rule: if a COMPANY WEBSITE section is present, treat it as genuine first-party "
        f"company communication (not hearsay, not to be second-guessed) and USE it substantively in "
        f"one_liner/science/stage_signal/funding wherever it actually answers that field -- do NOT "
        f"default to \"Not observable at this stage\" for something the excerpt already states. "
        f"Conversely, do not extrapolate beyond what the excerpt actually says; summarize only what's "
        f"there, and if the excerpt is itself vague or marketing fluff, say so plainly rather than "
        f"inventing specifics it doesn't contain."
    )
    return full_system, template.strip()


def _format_orcid(orcid_result: dict | None) -> str | None:
    """Render an orcid.lookup_director() result as a short factual note, or
    None if there's nothing worth saying (unparseable name / lookup failed /
    ORCID not configured -- silence is better than clutter for those)."""
    if not orcid_result:
        return None
    status = orcid_result.get("status")
    if status == "confirmed":
        insts = ", ".join(orcid_result.get("institutions", [])) or "no institution listed"
        return f"ORCID CONFIRMED ({orcid_result.get('orcid_id', '')}) — affiliations: {insts}"
    if status == "ambiguous":
        count = orcid_result.get("candidate_count")
        if isinstance(count, int) and count <= 10:
            return (f"ORCID: {count} possible matches — common name but few enough that a human "
                     f"could plausibly resolve it (e.g. via LinkedIn); do not treat as confirmed")
        return f"ORCID: {count} possible matches — name too common to verify via ORCID search, do not pursue"
    if status == "no_match":
        return "ORCID: no matching record found"
    return None  # unparseable_name / lookup_failed — not worth surfacing


def _format_gtr(gtr_result: dict | None) -> str | None:
    """Render a gtr.lookup_director() result as a short factual note, or
    None if there's nothing worth saying. Mirrors _format_orcid()'s
    severity tiering for ambiguous (common-name) matches."""
    if not gtr_result:
        return None
    status = gtr_result.get("status")
    if status == "confirmed":
        org = gtr_result.get("organisation") or "no organisation listed"
        return f"GtR CONFIRMED (UKRI-funded investigator) — organisation: {org}"
    if status == "ambiguous":
        count = gtr_result.get("candidate_count")
        if isinstance(count, int) and count <= 10:
            return (f"GtR: {count} possible matches — common name but few enough that a human "
                     f"could plausibly resolve it (e.g. via LinkedIn); do not treat as confirmed")
        return f"GtR: {count} possible matches — name too common to verify via GtR search, do not pursue"
    if status == "no_match":
        return "GtR: no UKRI grant investigator record found"
    return None  # unparseable_name / lookup_failed — not worth surfacing


def _format_repeat_founder(rf_result: dict | None) -> str | None:
    """Render a repeat_founder.lookup_director() result as a short factual
    note, or None if there's nothing worth saying (no appointments link /
    lookup failed -- a data-quality gap, not a fact about the director).
    Composes the sector-match note and the advisor-pattern note into one
    string when both apply, since they're independent facts about the same
    officer."""
    if not rf_result:
        return None
    status = rf_result.get("status")
    if status in ("no_appointments_link", "lookup_failed"):
        return None

    parts = []
    if status == "same_sector_confirmed":
        matches = rf_result.get("same_sector_matches") or []
        best_match = next((m for m in matches if not m.get("resigned_on")), matches[0]) if matches else None
        if best_match:
            state = "active" if not best_match.get("resigned_on") else f"resigned {best_match['resigned_on']}"
            sic = "/".join(best_match.get("sic_codes") or [])
            extra = f", {len(matches) - 1} more" if len(matches) > 1 else ""
            parts.append(
                f"REPEAT FOUNDER CONFIRMED (same sector) — director at "
                f"{best_match.get('company_name', '')} (SIC {sic}, {state}){extra}"
            )
    elif status == "other_sector_only":
        n = rf_result.get("total_results", 0)
        cap_note = " (not all checked — high appointment count)" if rf_result.get("sic_lookup_capped") else ""
        parts.append(f"Repeat founder: {n} other appointment(s), none confirmed in target sector{cap_note}")
    elif status == "no_other_appointments_found":
        parts.append("Repeat founder: no other appointments found under this officer record")

    active = rf_result.get("active_count", 0)
    if active >= config.REPEAT_FOUNDER_ADVISOR_THRESHOLD:
        parts.append(f"ADVISOR PATTERN: {active} other active director appointments")

    return " | ".join(parts) if parts else None


def _badge_summary(officers: list[dict]) -> dict:
    """Compute one best-case badge status per source across all of a
    company's officers, for the visible badges on the rendered digest page.

    Derived directly from the enrichment results attached to each officer
    (not the model's prose) so a badge can never hallucinate or drift out
    of sync with what orcid.py/gtr.py/repeat_founder.py actually found --
    same reasoning as the _format_*() functions being the single source of
    truth for the prompt text.
    """

    def best(source: str, rank_fn) -> dict | None:
        best_result, best_rank = None, -1
        for o in officers or []:
            result = o.get(source)
            if not result:
                continue
            rank = rank_fn(result)
            if rank > best_rank:
                best_rank, best_result = rank, result
        return best_result if best_rank > 0 else None

    def orcid_gtr_rank(result: dict) -> int:
        # confirmed > ambiguous with <=10 candidates (still "a human could
        # resolve it") > everything else, which collapses to "no badge" --
        # silence over clutter for weak signals.
        status = result.get("status")
        if status == "confirmed":
            return 2
        if status == "ambiguous" and isinstance(result.get("candidate_count"), int) \
                and result["candidate_count"] <= 10:
            return 1
        return 0

    def repeat_founder_rank(result: dict) -> int:
        if result.get("status") == "same_sector_confirmed":
            return 2
        if (result.get("active_count") or 0) >= config.REPEAT_FOUNDER_ADVISOR_THRESHOLD:
            return 1
        return 0

    return {
        "orcid": best("orcid", orcid_gtr_rank),
        "gtr": best("gtr", orcid_gtr_rank),
        "repeat_founder": best("repeat_founder", repeat_founder_rank),
        # Company-wide, not "the one officer the badge picked" -- any
        # officer meeting the threshold makes this true.
        "advisor_pattern": any(
            (o.get("repeat_founder") or {}).get("active_count", 0) >= config.REPEAT_FOUNDER_ADVISOR_THRESHOLD
            for o in officers or []
        ),
    }


def _format_officers(officers: list[dict]) -> str:
    if not officers:
        return "None listed / not fetched"
    parts = []
    for o in officers:
        bits = [o.get("name", "")]
        if o.get("role"):
            bits.append(o["role"])
        if o.get("occupation"):
            bits.append(f"occupation: {o['occupation']}")
        if o.get("nationality"):
            bits.append(o["nationality"])
        orcid_note = _format_orcid(o.get("orcid"))
        if orcid_note:
            bits.append(orcid_note)
        gtr_note = _format_gtr(o.get("gtr"))
        if gtr_note:
            bits.append(gtr_note)
        repeat_founder_note = _format_repeat_founder(o.get("repeat_founder"))
        if repeat_founder_note:
            bits.append(repeat_founder_note)
        parts.append(" — ".join(b for b in bits if b))
    return "; ".join(parts)


def _format_address(record: dict) -> str:
    address = record.get("registered_address", "")
    match = record.get("incubator_match")
    if match:
        address += f" — CONFIRMED MATCH against known incubator/cluster list: '{match}'"
    return address


def _format_innovate_uk_section(record: dict) -> str:
    """Return the 'INNOVATE UK / UKRI DATA' block for the user message, or ''
    if no project-level data was matched for this company.

    No code path in this repo currently fetches project-level Innovate UK
    data (scanner/gtr.py only does a director-NAME lookup, not a project
    match) -- so record.get("innovate_uk_project") is always None today.
    This function exists so that section activates automatically the day
    project-level fetching IS built, without sending the model six lines of
    literal unfilled '{title}'/'{abstract}'/etc. placeholders on every
    single call in the meantime (which is what a static template.replace()
    chain was doing before this fix)."""
    project = record.get("innovate_uk_project")
    if not project:
        return ""
    return (
        "\n\nINNOVATE UK / UKRI DATA (if matched):\n"
        f"Project title: {project.get('title', '')}\n"
        f"Abstract: {project.get('abstract', '')}\n"
        f"Award: £{project.get('amount', '')}, {project.get('start', '')} to {project.get('end', '')}\n"
        f"Lead organisation: {project.get('org', '')}"
    )


def _format_website(record: dict) -> str:
    """Return the 'COMPANY WEBSITE' block for the user message, or '' if no
    live site was found (or website checking is disabled/failed). See
    website.py's docstring for why this is the cheapest, most deterministic
    form of "search the internet for more info" -- helps some briefs a lot,
    does nothing for brand-new shells with no site yet, which is expected."""
    site = record.get("website")
    if not site or site.get("status") != "found":
        return ""
    return (
        f"\n\nCOMPANY WEBSITE (found live, {site.get('url', '')}):\n"
        f"Title: {site.get('title', '')}\n"
        f"Page text excerpt: {site.get('excerpt', '')}"
    )


def build_user_message(record: dict, template: str) -> str:
    # The static template includes a trailing "INNOVATE UK / UKRI DATA"
    # block with placeholders no code populates yet -- strip that static
    # block and replace it with the conditional version above, which sends
    # nothing at all until real project data exists (see its docstring).
    static_block_start = template.find("\n\nINNOVATE UK / UKRI DATA")
    base_template = template[:static_block_start] if static_block_start != -1 else template
    msg = (
        base_template.replace("{name}", record.get("company_name", ""))
        .replace("{date}", record.get("date_of_creation", ""))
        .replace("{sic_codes}", ", ".join(record.get("sic_codes", [])))
        .replace("{address}", _format_address(record))
        .replace("{officers with occupations and other appointments if fetched}",
                 _format_officers(record.get("officers", [])))
    )
    return msg + _format_innovate_uk_section(record) + _format_website(record)


def _extract_text(message) -> str:
    """Pull the text out of a Claude response, skipping non-text blocks
    (e.g. ThinkingBlock, which appears when extended thinking is enabled)."""
    text_parts = [
        block.text for block in message.content if getattr(block, "type", None) == "text"
    ]
    if not text_parts:
        raise RuntimeError(
            f"No text block in Claude response (block types: "
            f"{[getattr(b, 'type', type(b).__name__) for b in message.content]})"
        )
    return "".join(text_parts)


def _extract_final_text(message) -> str:
    """Pull ONLY the last text block out of a Claude response.

    Needed for web-search-enriched calls specifically: with the web_search
    tool attached, the response can interleave several text blocks (the
    model narrating "I'll search for..." between searches) with
    server_tool_use / web_search_tool_result blocks, and only the LAST text
    block is the actual final JSON brief. _extract_text() above joins ALL
    text blocks together, which is correct for a plain (no-tool) call but
    would concatenate narration text in front of the JSON here and break
    parsing."""
    text_blocks = [b.text for b in message.content if getattr(b, "type", None) == "text"]
    if not text_blocks:
        raise RuntimeError(
            f"No text block in Claude response (block types: "
            f"{[getattr(b, 'type', type(b).__name__) for b in message.content]})"
        )
    return text_blocks[-1]


def _extract_sources(message) -> list[str]:
    """Pull cited source URLs out of a response's web_search_tool_result
    blocks, deduped, in first-seen order. Used to show real, clickable
    sources under a search-enriched brief -- see WEB_SEARCH_SPEC.md."""
    seen: list[str] = []
    for block in message.content:
        if getattr(block, "type", None) != "web_search_tool_result":
            continue
        content = getattr(block, "content", None)
        if not isinstance(content, list):
            continue  # error result, not a list of hits -- nothing to add
        for item in content:
            url = getattr(item, "url", None)
            if url and url not in seen:
                seen.append(url)
    return seen


def _parse_json(raw: str) -> dict:
    """Pull the JSON object out of a model response, tolerating stray fences."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n|\n```$", "", raw)
    start, end = raw.find("{"), raw.rfind("}")
    return json.loads(raw[start : end + 1])


def generate_brief(record: dict, client: Anthropic, system: str, template: str) -> dict:
    """Produce one validated JSON brief for a company record."""
    user_message = build_user_message(record, template)
    last_error = None
    for attempt in range(2):
        message = client.messages.create(
            model=config.MODEL,
            max_tokens=config.MAX_TOKENS,
            system=system,
            thinking={"type": "disabled"},
            messages=[{"role": "user", "content": user_message}],
        )
        raw = _extract_text(message)
        try:
            brief = _parse_json(raw)
            if REQUIRED_KEYS - brief.keys():
                raise ValueError(f"missing keys: {REQUIRED_KEYS - brief.keys()}")
            # Carry the company number through for traceability / dedupe.
            brief["company_number"] = record.get("company_number", "")
            # Carry SIC codes through too -- "sic_codes" was never part of the
            # model's JSON schema (see REQUIRED_KEYS), so render.py's
            # b.get("sic_codes", []) was silently always falling back to []
            # -> the digest's "SIC: —" field was permanently empty, no matter
            # what Companies House actually returned.
            brief["sic_codes"] = record.get("sic_codes", [])
            # Attach a visible-badge summary computed from the raw enrichment
            # data (not the model's output) -- see _badge_summary() docstring.
            badges = _badge_summary(record.get("officers", []))
            brief["orcid_badge"] = badges["orcid"]
            brief["gtr_badge"] = badges["gtr"]
            brief["repeat_founder_badge"] = badges["repeat_founder"]
            brief["advisor_pattern"] = badges["advisor_pattern"]
            return brief
        except (ValueError, json.JSONDecodeError) as e:
            last_error = e
            print(f"  brief parse failed (attempt {attempt + 1}): {e}")
            print(f"    stop_reason={getattr(message, 'stop_reason', '?')} "
                  f"usage={getattr(message, 'usage', '?')}")
            print(f"    raw response ({len(raw)} chars): {raw!r}")
    raise RuntimeError(f"Failed to generate a valid brief for {record.get('company_name')}: {last_error}")


WEB_SEARCH_TOOL_TYPE = "web_search_20250305"


def _web_search_rule() -> str:
    return (
        "Web search rule: a web_search tool is attached to this request -- use it. "
        "Search for the company name together with \"UK biotech\" (to find or confirm "
        "a company website, in case one wasn't already supplied above), and for each "
        "named director together with terms like \"LinkedIn\", \"university\", "
        "\"research\", or \"spinout\" -- to check for academic staff pages, publication "
        "records, spinout ventures, or funding/accelerator news. Use what the search "
        "results actually say to revise one_liner, science, stage_signal, "
        "team_provenance, funding, flags_positive, flags_negative, interest_score and "
        "unknowns -- do not leave a field at \"Not observable at this stage\" if a "
        "search result already answers it. Do not extrapolate beyond what a result "
        "actually states: if a link between the company and something you found (e.g. "
        "a director's other project) is plausible but not confirmed by name-match, say "
        "so explicitly rather than asserting it as fact. Note vague marketing language "
        "as such rather than inventing specifics. A company's own small/new website is "
        "not always well-indexed by search -- if you can't find one, say so rather than "
        "assuming it doesn't exist."
    )


def enrich_with_web_search(record: dict, brief: dict, client: Anthropic, system: str, template: str) -> dict:
    """Re-run one company through the model with Claude's web_search tool
    attached, gated by the caller to companies whose pass-1 (no-search)
    interest_score already cleared config.WEB_SEARCH_ENRICH_THRESHOLD.

    Falls back to the original pass-1 brief -- unchanged, with
    search_enriched=False -- on ANY failure (bad JSON, API error, missing
    text block), so one company's search hiccup never takes down a run.
    See WEB_SEARCH_SPEC.md for the design rationale, cost estimate, and real
    test findings (it reliably surfaces academic/professional profiles and
    adjacent ventures, but is not a reliable substitute for the deterministic
    guessed-domain check in website.py when it comes to finding a brand-new
    small company's own site from a generic name search alone)."""
    user_message = build_user_message(record, template)
    search_system = system + "\n\n" + _web_search_rule()
    try:
        message = client.messages.create(
            model=config.MODEL,
            max_tokens=config.MAX_TOKENS,
            system=search_system,
            thinking={"type": "disabled"},
            tools=[{
                "type": WEB_SEARCH_TOOL_TYPE,
                "name": "web_search",
                "max_uses": config.WEB_SEARCH_MAX_USES,
            }],
            messages=[{"role": "user", "content": user_message}],
        )
        raw = _extract_final_text(message)
        enriched = _parse_json(raw)
        if REQUIRED_KEYS - enriched.keys():
            raise ValueError(f"missing keys: {REQUIRED_KEYS - enriched.keys()}")
        enriched["company_number"] = record.get("company_number", "")
        enriched["sic_codes"] = record.get("sic_codes", [])
        badges = _badge_summary(record.get("officers", []))
        enriched["orcid_badge"] = badges["orcid"]
        enriched["gtr_badge"] = badges["gtr"]
        enriched["repeat_founder_badge"] = badges["repeat_founder"]
        enriched["advisor_pattern"] = badges["advisor_pattern"]
        enriched["search_enriched"] = True
        enriched["search_sources"] = _extract_sources(message)
        return enriched
    except Exception as exc:
        print(f"  web-search enrichment failed for {record.get('company_name')}, "
              f"keeping pass-1 brief unchanged: {exc}")
        brief["search_enriched"] = False
        brief["search_sources"] = []
        return brief


def _score_int(interest_score: str) -> int:
    """Pull the leading integer out of an interest_score string like
    '4 — ...'. Duplicated from render.score_int() rather than imported, so
    generate.py doesn't take on a dependency on render.py for one regex."""
    m = re.match(r"\s*(\d)", str(interest_score))
    return int(m.group(1)) if m else 0


def enrich_all_with_web_search(records: list[dict], briefs: list[dict]) -> list[dict]:
    """Second pass: for every brief whose pass-1 interest_score is at or
    above config.WEB_SEARCH_ENRICH_THRESHOLD, re-run with web search
    attached and replace it in place. Briefs below the threshold are marked
    search_enriched=False and left untouched -- no second API call spent on
    companies pass 1 already wrote off."""
    if not briefs:
        return briefs
    if not config.ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY is not set.")
    client = Anthropic(api_key=config.ANTHROPIC_API_KEY)
    system, template = load_prompt()
    by_number = {r.get("company_number"): r for r in records}
    out = []
    for brief in briefs:
        score = _score_int(brief.get("interest_score", ""))
        if score < config.WEB_SEARCH_ENRICH_THRESHOLD:
            brief["search_enriched"] = False
            brief["search_sources"] = []
            out.append(brief)
            continue
        record = by_number.get(brief.get("company_number"), {})
        print(f"  web-search enrichment (score {score}): {brief.get('company_name')}")
        out.append(enrich_with_web_search(record, brief, client, system, template))
    return out


def generate_all(records: list[dict]) -> list[dict]:
    if not records:
        return []
    if not config.ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY is not set.")
    client = Anthropic(api_key=config.ANTHROPIC_API_KEY)
    system, template = load_prompt()
    briefs = []
    for record in records:
        print(f"  generating brief: {record.get('company_name')}")
        briefs.append(generate_brief(record, client, system, template))
    return briefs
