"""One-off diagnostic: check real company names from companies already
processed today against real (guessed) domains, without re-running the
whole pipeline or touching data/seen.json. Costs nothing -- just plain HTTP
GETs to guessed domains -- and makes zero Anthropic API calls.

Usage:
    python test_website_live.py                    # uses today's data/briefs/*.json
    python test_website_live.py --date 2026-07-28
"""
from __future__ import annotations

import argparse
import json
from datetime import date

from scanner import config, website


def main(target_date: str) -> None:
    briefs_path = config.BRIEFS_DIR / f"{target_date}.json"
    if not briefs_path.exists():
        print(f"No briefs file found at {briefs_path}")
        return

    briefs = json.loads(briefs_path.read_text())
    names = [b.get("company_name") for b in briefs if b.get("company_name")]
    print(f"Found {len(names)} companies in {briefs_path.name}\n")

    found_count = 0
    for name in names:
        result = website.check_company_website(name)
        if result.get("status") == "found":
            found_count += 1
            print(f"{name}: FOUND -> {result.get('url')}")
            print(f"    title: {result.get('title')}")
            print(f"    excerpt: {result.get('excerpt', '')[:200]}...")
        else:
            print(f"{name}: not_found")

    print(f"\n{found_count} of {len(names)} companies have a detectable live website "
          f"(guessed domain, name.co.uk / name.com only -- a real absence is expected "
          f"for brand-new shells and companies using a non-obvious domain).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=date.today().isoformat(), help="YYYY-MM-DD, defaults to today")
    args = parser.parse_args()
    main(args.date)
