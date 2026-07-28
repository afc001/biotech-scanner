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
        f"or a negative signal; do not mention it in flags_positive or flags_negative at all."
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


def _badge_summary(officers: list[dict]) -> dict:
    """Compute one best-case ORCID/GtR badge status across all of a company's
    officers, for the visible badge on the rendered digest page.

    Derived directly from the enrichment results attached to each officer
    (not the model's prose) so the badge can never hallucinate or drift out
    of sync with what orcid.py/gtr.py actually found -- same reasoning as
    _format_orcid()/_format_gtr() being the single source of truth for the
    prompt text.

    Priority per source: confirmed > ambiguous with <=10 candidates (still
    "a human could resolve it") > everything else (no_match / a too-common
    ambiguous match / unparseable / lookup failed / no result at all), which
    all collapse to "no badge" -- matches the existing philosophy of silence
    over clutter for weak signals.
    """

    def best(source: str) -> dict | None:
        best_result, best_rank = None, -1
        for o in officers or []:
            result = o.get(source)
            if not result:
                continue
            status = result.get("status")
            if status == "confirmed":
                rank = 2
            elif status == "ambiguous" and isinstance(result.get("candidate_count"), int) \
                    and result["candidate_count"] <= 10:
                rank = 1
            else:
                rank = 0
            if rank > best_rank:
                best_rank, best_result = rank, result
        return best_result if best_rank > 0 else None

    return {"orcid": best("orcid"), "gtr": best("gtr")}


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
    return msg + _format_innovate_uk_section(record)


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
            # Attach a visible-badge summary computed from the raw enrichment
            # data (not the model's output) -- see _badge_summary() docstring.
            badges = _badge_summary(record.get("officers", []))
            brief["orcid_badge"] = badges["orcid"]
            brief["gtr_badge"] = badges["gtr"]
            return brief
        except (ValueError, json.JSONDecodeError) as e:
            last_error = e
            print(f"  brief parse failed (attempt {attempt + 1}): {e}")
            print(f"    stop_reason={getattr(message, 'stop_reason', '?')} "
                  f"usage={getattr(message, 'usage', '?')}")
            print(f"    raw response ({len(raw)} chars): {raw!r}")
    raise RuntimeError(f"Failed to generate a valid brief for {record.get('company_name')}: {last_error}")


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
