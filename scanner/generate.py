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
    """Extract (system_prompt, user_template) from the prompt markdown file."""
    text = config.PROMPT_FILE.read_text()
    blocks = _fenced_blocks(text)
    system = next((b for b in blocks if b.strip().startswith("You are an analyst")), None)
    template = next((b for b in blocks if "COMPANIES HOUSE RECORD" in b), None)
    if not system or not template:
        raise RuntimeError("Could not parse system prompt / user template from prompt file.")
    return system.strip(), template.strip()


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
        parts.append(" — ".join(b for b in bits if b))
    return "; ".join(parts)


def build_user_message(record: dict, template: str) -> str:
    return (
        template.replace("{name}", record.get("company_name", ""))
        .replace("{date}", record.get("date_of_creation", ""))
        .replace("{sic_codes}", ", ".join(record.get("sic_codes", [])))
        .replace("{address}", record.get("registered_address", ""))
        .replace("{officers with occupations and other appointments if fetched}",
                 _format_officers(record.get("officers", [])))
    )


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
            messages=[{"role": "user", "content": user_message}],
        )
        raw = _extract_text(message)
        try:
            brief = _parse_json(raw)
            if REQUIRED_KEYS - brief.keys():
                raise ValueError(f"missing keys: {REQUIRED_KEYS - brief.keys()}")
            # Carry the company number through for traceability / dedupe.
            brief["company_number"] = record.get("company_number", "")
            return brief
        except (ValueError, json.JSONDecodeError) as e:
            last_error = e
            print(f"  brief parse failed (attempt {attempt + 1}): {e}")
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
