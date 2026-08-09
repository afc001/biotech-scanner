"""Entry point for the UK Biotech Deal-Flow Scanner.

Run locally:   python run.py
In CI:         invoked by .github/workflows/scan.yml on a daily schedule.

Flow: fetch new Companies House incorporations -> generate JSON briefs via the
Claude API -> render dated md/html digests -> record run history in
data/scanner.db -> print an alert summary. Exits non-zero on failure so the
scheduled workflow surfaces the error by email.
"""

from __future__ import annotations

import sys
from datetime import date

from scanner import config, db, fetch, generate, render


def _record_run(conn, records: list[dict], briefs: list[dict], hot: list[dict],
                 fetch_counts: dict, run_date: str) -> int:
    """Write this run's history (one runs row, one companies/scores row per
    brief) to data/scanner.db. `hot` is the same alert-threshold subset of
    `briefs` the caller already computed for the console summary -- reused
    here rather than re-filtering, so "surfaced" can never disagree between
    the two.
    """
    system, _ = generate.load_prompt()  # local file read, no API call
    prompt_version = db.get_prompt_version(system)
    git_sha = db.get_git_sha()

    n_address_matched = sum(1 for r in records if r.get("incubator_match"))
    hot_numbers = {b.get("company_number") for b in hot}

    run_id = db.insert_run(
        conn,
        run_date=run_date,
        prompt_version=prompt_version,
        git_sha=git_sha,
        n_fetched=fetch_counts.get("n_fetched"),
        n_sic_matched=fetch_counts.get("n_sic_matched"),
        n_address_matched=n_address_matched,
        n_scored=len(briefs),
        n_surfaced=len(hot),
        source="live",
    )

    by_number = {r.get("company_number"): r for r in records}
    for brief in briefs:
        number = brief.get("company_number")
        record = by_number.get(number, {})
        website = record.get("website") or {}
        db.upsert_company(
            conn,
            company_number=number,
            name=brief.get("company_name") or record.get("company_name", ""),
            incorporated_on=record.get("date_of_creation") or brief.get("incorporated"),
            sic_codes=brief.get("sic_codes") or record.get("sic_codes"),
            registered_address=record.get("registered_address"),
            website=website.get("url"),
            website_status=website.get("status"),
        )
        db.insert_score(
            conn,
            company_number=number,
            run_id=run_id,
            brief=brief,
            prompt_version=prompt_version,
            surfaced=number in hot_numbers,
            incubator_match=record.get("incubator_match"),
            website_status=website.get("status"),
        )

    conn.commit()
    return run_id


def main() -> int:
    today = date.today().isoformat()
    print(f"=== Biotech scanner run {today} ===")

    conn = db.get_connection()
    db.init_db(conn)

    records, fetch_counts = fetch.get_new_companies()
    if not records:
        print("No new companies. Writing an empty digest and exiting cleanly.")
        render.render_digest([])
        try:
            _record_run(conn, [], [], [], fetch_counts, today)
        except Exception as exc:
            print(f"DB WRITE FAILED (non-fatal, run already succeeded): {exc}")
        conn.close()
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

    # Record run history in data/scanner.db, in that same "already safely on
    # disk" window. Non-fatal: a bug in this newer, less-trusted code path
    # shouldn't be able to fail an otherwise-successful run or cause
    # companies to be reprocessed (they're already marked seen above).
    try:
        _record_run(conn, records, briefs, hot, fetch_counts, today)
    except Exception as exc:
        print(f"DB WRITE FAILED (non-fatal, run already succeeded): {exc}")
    conn.close()

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
