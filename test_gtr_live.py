"""One-off diagnostic: verify GtR enrichment against REAL director names
from companies already processed today, without re-running the whole
pipeline or touching data/seen.json. Costs nothing -- Companies House and
GtR are both free, no auth needed for either -- and makes zero Anthropic
API calls.

Usage:
    python test_gtr_live.py                    # uses today's data/briefs/*.json
    python test_gtr_live.py --date 2026-07-28
"""
from __future__ import annotations

import argparse
import json
from datetime import date

from scanner import config, fetch, generate, gtr


def main(target_date: str) -> None:
    briefs_path = config.BRIEFS_DIR / f"{target_date}.json"
    if not briefs_path.exists():
        print(f"No briefs file found at {briefs_path}")
        return

    briefs = json.loads(briefs_path.read_text())
    numbers = [b.get("company_number") for b in briefs if b.get("company_number")]
    print(f"Found {len(numbers)} company numbers in {briefs_path.name}\n")

    any_officers = False
    for number in numbers:
        officers = fetch.fetch_officers(number)
        if not officers:
            print(f"{number}: no officers returned by Companies House")
            continue
        any_officers = True
        enriched = gtr.enrich_officers(officers)

        # Collapse the same person appearing twice (e.g. director + company
        # secretary) into one line, same as test_orcid_live.py.
        by_name: dict[str, dict] = {}
        for o in enriched:
            name = o.get("name", "")
            entry = by_name.setdefault(name, {"roles": [], "gtr": o.get("gtr")})
            if o.get("role") and o["role"] not in entry["roles"]:
                entry["roles"].append(o["role"])

        for name, info in by_name.items():
            roles = "/".join(info["roles"]) or "role unknown"
            # Reuse generate.py's own formatter so this diagnostic can never
            # drift out of sync with what the model actually sees in a brief.
            note = generate._format_gtr(info["gtr"])
            if note is None:
                status = (info["gtr"] or {}).get("status", "n/a")
                note = f"status: {status}"
            print(f"{number} — {name} ({roles}): {note}")

    if not any_officers:
        print("\nNo officer records found for any of today's companies -- nothing to look up.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=date.today().isoformat(), help="YYYY-MM-DD, defaults to today")
    args = parser.parse_args()
    main(args.date)
