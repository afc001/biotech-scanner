"""Entry point for the UK Biotech Deal-Flow Scanner.

Run locally:   python run.py
In CI:         invoked by .github/workflows/scan.yml on a daily schedule.

Flow: fetch new Companies House incorporations -> generate JSON briefs via the
Claude API -> render dated md/html digests -> print an alert summary. Exits
non-zero on failure so the scheduled workflow surfaces the error by email.
"""

from __future__ import annotations

import sys
from datetime import date

from scanner import config, fetch, generate, render


def main() -> int:
    today = date.today().isoformat()
    print(f"=== Biotech scanner run {today} ===")

    records = fetch.get_new_companies()
    if not records:
        print("No new companies. Writing an empty digest and exiting cleanly.")
        render.render_digest([], )
        return 0

    briefs = generate.generate_all(records)

    if config.FETCH_WEB_SEARCH:
        print(f"\nWeb-search enrichment: enabled (threshold {config.WEB_SEARCH_ENRICH_THRESHOLD}+)")
        briefs = generate.enrich_all_with_web_search(records, briefs)
    else:
        for b in briefs:
            b.setdefault("search_enriched", False)
            b.setdefault("search_sources", [])

    raw_path = render.save_raw_briefs(briefs)
    paths = render.render_digest(briefs)
    print(f"raw briefs: {raw_path}")
    print(f"digest:     {paths['html']}")

    # Only now that briefs are generated and safely written to disk do we
    # mark these companies as seen — if anything above raised, they stay
    # eligible for retry on the next run instead of being silently dropped.
    fetch.mark_seen(records)

    # Alert summary — the loud bit worth a human's attention.
    hot = [b for b in briefs
           if render.score_int(b.get("interest_score", "")) >= config.INTEREST_ALERT_THRESHOLD]
    print(f"\n{len(briefs)} briefs generated; {len(hot)} at/above alert threshold "
          f"({config.INTEREST_ALERT_THRESHOLD}):")
    for b in sorted(hot, key=lambda x: render.score_int(x.get("interest_score", "")), reverse=True):
        print(f"  [{render.score_int(b.get('interest_score',''))}] {b.get('company_name')} — {b.get('one_liner','')}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # surface any failure to CI (and its failure email)
        print(f"PIPELINE FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
