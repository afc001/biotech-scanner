"""One-off utility: count how many companies a longer lookback window would
sweep up, WITHOUT calling the Anthropic API and WITHOUT touching the
seen-store. Use this before any historical backfill to see the scale (and
estimate cost) before spending a single Claude token.

Usage:
    python dry_run_count.py --days 30
    python dry_run_count.py --from 2026-06-30 --to 2026-07-14

Only hits the free Companies House search endpoint. No briefs are
generated, no files are written, data/seen.json is left untouched.
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta

import requests

from scanner import config, fetch


def count_for_window(days: int | None = None, date_from: str | None = None, date_to: str | None = None) -> dict:
    if date_from or date_to:
        if not (date_from and date_to):
            raise ValueError("Provide both --from and --to, or neither.")
        incorporated_from, incorporated_to = date_from, date_to
        days = None
    else:
        today = date.today()
        incorporated_from = (today - timedelta(days=days)).isoformat()
        incorporated_to = today.isoformat()

    by_number: dict[str, str] = {}
    per_sic: dict[str, int] = {}

    for sic in config.SIC_CODES:
        sic = sic.strip()
        print(f"searching SIC {sic} ({incorporated_from} -> {incorporated_to})")
        try:
            results = fetch.search_sic(sic, incorporated_from, incorporated_to)
        except requests.HTTPError as exc:
            print(f"  SIC {sic} failed: {exc}")
            per_sic[sic] = 0
            continue
        per_sic[sic] = len(results)
        for item in results:
            number = item.get("company_number")
            if number:
                by_number[number] = item.get("company_name", "")

    already_seen = fetch._load_seen()
    not_yet_processed = sum(1 for n in by_number if n not in already_seen)

    return {
        "days": days,
        "window": f"{incorporated_from} -> {incorporated_to}",
        "sic_codes": config.SIC_CODES,
        "per_sic_raw_counts": per_sic,
        "unique_companies": len(by_number),
        "already_processed": len(by_number) - not_yet_processed,
        "would_actually_generate": not_yet_processed,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30, help="lookback window in days")
    parser.add_argument("--from", dest="date_from", default=None, help="fixed window start, YYYY-MM-DD (use with --to)")
    parser.add_argument("--to", dest="date_to", default=None, help="fixed window end, YYYY-MM-DD (use with --from)")
    args = parser.parse_args()

    result = count_for_window(days=args.days, date_from=args.date_from, date_to=args.date_to)

    print()
    print(f"=== DRY RUN: {result['window']}, SIC codes {result['sic_codes']} ===")
    for sic, n in result["per_sic_raw_counts"].items():
        print(f"  SIC {sic}: {n} raw hits")
    print(f"  unique companies in window (deduped across SIC codes): {result['unique_companies']}")
    print(f"  already processed (in seen.json, won't be regenerated): {result['already_processed']}")
    print(f"  WOULD ACTUALLY GENERATE BRIEFS FOR: {result['would_actually_generate']}")
    print()
    print("No Anthropic API calls were made. No files were written. seen.json was read but not modified.")

    n = result["would_actually_generate"]
    est_low, est_high = n * 0.010, n * 0.020
    print(f"Rough cost estimate at ~$0.010-0.020/brief (based on your own observed real-world "
          f"cost so far): ${est_low:.2f} - ${est_high:.2f}")
