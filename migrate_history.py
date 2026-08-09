"""One-off migration: load historical scan output into data/scanner.db.

Imports every data/briefs/{date}.json file (one runs row + one companies/
scores row per brief) plus any digest-only "zero new companies" date (a
digests/{date}.html with no matching briefs file — the pipeline skips
save_raw_briefs() on an empty fetch, so those days would otherwise be
invisible to the funnel stats).

Idempotent — safe to re-run. Each run_date is only ever imported once: if a
`runs` row with source='migrated' already exists for that date, it's
skipped.

Usage:
    python migrate_history.py

No Anthropic/Companies House API calls are made — this only reads files
already on disk.
"""

from __future__ import annotations

import json

from scanner import config, db, render

THRESHOLD = config.INTEREST_ALERT_THRESHOLD


def _already_migrated(conn, run_date: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM runs WHERE run_date = ? AND source = 'migrated' LIMIT 1",
        (run_date,),
    ).fetchone()
    return row is not None


def import_briefs_file(conn, run_date: str, briefs: list[dict]) -> tuple[int, int]:
    """Import one data/briefs/{run_date}.json file. Returns
    (n_companies_touched, n_scores_imported)."""
    n_surfaced = sum(
        1 for b in briefs
        if render.score_int(b.get("interest_score", "")) >= THRESHOLD
    )

    run_id = db.insert_run(
        conn,
        run_date=run_date,
        prompt_version=None,       # no historical version was ever captured
        git_sha=None,
        n_fetched=None,             # raw pre-dedup fetch counts were never persisted
        n_sic_matched=None,
        n_address_matched=None,     # incubator_match was never persisted in the brief
        n_scored=len(briefs),
        n_surfaced=n_surfaced,
        source="migrated",
    )

    n_companies = 0
    for brief in briefs:
        number = brief.get("company_number")
        if not number:
            continue
        db.upsert_company(
            conn,
            company_number=number,
            name=brief.get("company_name", ""),
            incorporated_on=brief.get("incorporated"),
            sic_codes=brief.get("sic_codes"),
            # registered_address/website/website_status: the fetch-time
            # record that held these was never written to disk historically
            # -- left NULL (unknown), not derivable from what's left.
        )
        n_companies += 1
        surfaced = render.score_int(brief.get("interest_score", "")) >= THRESHOLD
        db.insert_score(
            conn,
            company_number=number,
            run_id=run_id,
            brief=brief,
            prompt_version=None,
            surfaced=surfaced,
            # incubator_match/website_status intentionally omitted (stay
            # _UNSET inside db.insert_score -> NULL): "never checked"
            # historically, not "checked, no match" -- see scanner/db.py.
        )

    conn.commit()
    return n_companies, len(briefs)


def import_zero_record_date(conn, run_date: str) -> None:
    db.insert_run(
        conn,
        run_date=run_date,
        n_fetched=None,
        n_sic_matched=None,
        n_address_matched=None,
        n_scored=0,
        n_surfaced=0,
        source="migrated",
    )
    conn.commit()


def main() -> None:
    conn = db.get_connection()
    db.init_db(conn)

    briefs_dates = sorted(p.stem for p in config.BRIEFS_DIR.glob("*.json"))
    digest_dates = sorted(
        p.stem for p in config.DIGESTS_DIR.glob("*.html") if p.stem != "index"
    )
    zero_record_dates = sorted(set(digest_dates) - set(briefs_dates))

    runs_imported = 0
    runs_skipped = 0
    total_companies = 0
    total_scores = 0

    for run_date in briefs_dates:
        if _already_migrated(conn, run_date):
            print(f"  {run_date}: already migrated, skipping")
            runs_skipped += 1
            continue
        briefs = json.loads((config.BRIEFS_DIR / f"{run_date}.json").read_text())
        n_companies, n_scores = import_briefs_file(conn, run_date, briefs)
        total_companies += n_companies
        total_scores += n_scores
        runs_imported += 1
        print(f"  {run_date}: {n_scores} briefs imported")

    for run_date in zero_record_dates:
        if _already_migrated(conn, run_date):
            print(f"  {run_date}: already migrated, skipping")
            runs_skipped += 1
            continue
        import_zero_record_date(conn, run_date)
        runs_imported += 1
        print(f"  {run_date}: 0 companies (digest-only zero-record day)")

    conn.close()

    print()
    print("=== Migration complete ===")
    print(f"runs imported:              {runs_imported} ({runs_skipped} already migrated, skipped)")
    print(f"companies imported/updated: {total_companies}")
    print(f"score rows imported:        {total_scores}")
    print()
    print("For all migrated rows, the following are NULL and cannot be backfilled --")
    print("this data was only ever computed in-memory during the original run and")
    print("was never written to disk by the pipeline that produced it:")
    print("  runs:      n_fetched, n_sic_matched, n_address_matched, prompt_version, git_sha")
    print("  companies: registered_address, website, website_status")
    print("  scores:    incubator_match, incubator_matched, website_status, website_found")


if __name__ == "__main__":
    main()
