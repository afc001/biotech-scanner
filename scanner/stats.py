"""Funnel, precision, and component-signal statistics over data/scanner.db.

Every query below is commented as if you've never written SQL before --
each function's docstring explains what each clause does and what would go
wrong (silently, not with an error) if it were written differently.

Usage:
    python -m scanner.stats
    python -m scanner.stats --from 2026-07-01 --to 2026-08-01 --report precision
"""

from __future__ import annotations

import argparse

from . import db


def funnel(conn, date_from: str | None = None, date_to: str | None = None) -> dict:
    """Total pipeline funnel over a date range: how many companies made it
    through each stage, from raw API hits down to surfaced (alert-worthy).

    SUM(column) adds up that column across every matching row. In SQL,
    SUM() silently SKIPS NULL values rather than treating them as zero or
    raising an error -- so a date range that includes any of the migrated
    historical runs (whose n_fetched/n_sic_matched/n_address_matched are
    NULL, because that data was never captured for them -- see
    migrate_history.py) will under-report those three totals with no
    warning. n_scored/n_surfaced ARE backfilled for every historical run,
    so those two totals are always complete.
    """
    date_from = date_from or "0000-01-01"
    date_to = date_to or "9999-12-31"
    row = conn.execute(
        """
        SELECT
            COUNT(*)                AS n_runs,             -- one row per matching run
            SUM(n_fetched)           AS n_fetched,           -- NULL-skipping SUM, see docstring
            SUM(n_sic_matched)        AS n_sic_matched,
            SUM(n_address_matched)     AS n_address_matched,
            SUM(n_scored)                AS n_scored,
            SUM(n_surfaced)                AS n_surfaced
        FROM runs
        WHERE run_date BETWEEN ? AND ?   -- BETWEEN is inclusive on both ends
        """,
        (date_from, date_to),
    ).fetchone()
    return dict(row)


def precision(conn, date_from: str | None = None, date_to: str | None = None) -> dict:
    """Precision over hand-labelled rows: of the surfaced companies you've
    actually reviewed (analyst_verdict IS NOT NULL), what fraction did you
    mark 'relevant'?

    GROUP BY analyst_verdict collapses the matching rows into one summary
    row per distinct verdict value ('relevant', 'noise') -- COUNT(*) inside
    each group counts how many rows landed in that group. Get the GROUP BY
    column wrong (e.g. group by interest_score instead) and you'd silently
    get one row per SCORE rather than per verdict -- no error, just a
    result that answers a different question than the one you asked.
    """
    date_from = date_from or "0000-01-01"
    date_to = date_to or "9999-12-31"
    rows = conn.execute(
        """
        SELECT analyst_verdict, COUNT(*) AS n
        FROM scores
        JOIN runs USING (run_id)            -- USING(run_id) joins scores to runs on
                                              -- the column of that name present in
                                              -- both tables; equivalent to writing
                                              -- ON scores.run_id = runs.run_id
        WHERE analyst_verdict IS NOT NULL    -- only rows you've actually labelled
          AND run_date BETWEEN ? AND ?
        GROUP BY analyst_verdict
        """,
        (date_from, date_to),
    ).fetchall()
    counts = {r["analyst_verdict"]: r["n"] for r in rows}
    relevant, noise = counts.get("relevant", 0), counts.get("noise", 0)
    total = relevant + noise
    return {
        "relevant": relevant,
        "noise": noise,
        "total_labelled": total,
        "precision": (relevant / total) if total else None,
    }


def component_means(conn, date_from: str | None = None, date_to: str | None = None) -> list[dict]:
    """Mean of each score component, grouped by your manual verdict -- the
    "which signals actually separate relevant from noise" query.

    orcid_confirmed / gtr_confirmed / incubator_matched / website_found /
    search_enriched are stored as 0/1 integers, not booleans (see
    scanner/db.py) precisely so AVG() works on them: AVG() of a 0/1 column
    is exactly the FRACTION of rows where that signal was present. E.g.
    AVG(orcid_confirmed) = 0.8 for the 'relevant' group means 80% of the
    companies you labelled relevant had a confirmed ORCID match. AVG(),
    like SUM(), skips NULLs -- so a row where incubator_matched is NULL (a
    migrated historical row, where that signal was never recorded at all)
    is correctly excluded from the average rather than dragging it toward 0
    as if "not matched" were a known fact.

    AVG(interest_score) is included as a fourth, non-derived comparison:
    it's a real numeric column already in the schema, and it answers the
    most direct question of all -- does the model's own composite score
    actually track your judgement?
    """
    date_from = date_from or "0000-01-01"
    date_to = date_to or "9999-12-31"
    rows = conn.execute(
        """
        SELECT
            analyst_verdict,
            COUNT(*)                     AS n,
            AVG(interest_score)           AS mean_interest_score,
            AVG(orcid_confirmed)           AS mean_orcid_confirmed,
            AVG(gtr_confirmed)              AS mean_gtr_confirmed,
            AVG(incubator_matched)           AS mean_incubator_matched,
            AVG(website_found)                AS mean_website_found,
            AVG(search_enriched)               AS mean_search_enriched
        FROM scores
        JOIN runs USING (run_id)
        WHERE analyst_verdict IS NOT NULL
          AND run_date BETWEEN ? AND ?
        GROUP BY analyst_verdict
        ORDER BY analyst_verdict
        """,
        (date_from, date_to),
    ).fetchall()
    return [dict(r) for r in rows]


def _fmt(value, pct: bool = False) -> str:
    if value is None:
        return "n/a"
    return f"{value:.1%}" if pct else f"{value:.2f}"


def _print_funnel(result: dict) -> None:
    print("--- Funnel ---")
    print(f"  runs in range:        {result['n_runs']}")
    print(f"  n_fetched (raw hits): {result['n_fetched']}")
    print(f"  n_sic_matched:        {result['n_sic_matched']}")
    print(f"  n_address_matched:    {result['n_address_matched']}")
    print(f"  n_scored:             {result['n_scored']}")
    print(f"  n_surfaced:           {result['n_surfaced']}")
    print("  (a NULL stage total means at least one run in range never captured")
    print("   that stage -- true for every migrated historical run.)")


def _print_precision(result: dict) -> None:
    print("--- Precision (hand-labelled rows) ---")
    print(f"  relevant: {result['relevant']}")
    print(f"  noise:    {result['noise']}")
    print(f"  labelled: {result['total_labelled']}")
    if result["precision"] is None:
        print("  precision: n/a (nothing labelled yet -- run label_history.py)")
    else:
        print(f"  precision: {result['precision']:.1%}")


def _print_component_means(rows: list[dict]) -> None:
    print("--- Component means by verdict ---")
    if not rows:
        print("  n/a (nothing labelled yet -- run label_history.py)")
        return
    for r in rows:
        print(f"  {r['analyst_verdict']} (n={r['n']}):")
        print(f"    mean interest_score:  {_fmt(r['mean_interest_score'])}")
        print(f"    orcid_confirmed:      {_fmt(r['mean_orcid_confirmed'], pct=True)}")
        print(f"    gtr_confirmed:        {_fmt(r['mean_gtr_confirmed'], pct=True)}")
        print(f"    incubator_matched:    {_fmt(r['mean_incubator_matched'], pct=True)}")
        print(f"    website_found:        {_fmt(r['mean_website_found'], pct=True)}")
        print(f"    search_enriched:      {_fmt(r['mean_search_enriched'], pct=True)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="date_from", default=None, help="range start, YYYY-MM-DD")
    parser.add_argument("--to", dest="date_to", default=None, help="range end, YYYY-MM-DD")
    parser.add_argument(
        "--report", choices=["funnel", "precision", "components", "all"], default="all",
    )
    args = parser.parse_args()

    conn = db.get_connection()
    db.init_db(conn)

    if args.report in ("funnel", "all"):
        _print_funnel(funnel(conn, args.date_from, args.date_to))
        print()
    if args.report in ("precision", "all"):
        _print_precision(precision(conn, args.date_from, args.date_to))
        print()
    if args.report in ("components", "all"):
        _print_component_means(component_means(conn, args.date_from, args.date_to))

    conn.close()
