"""Interactive CLI: label surfaced companies 'relevant' or 'noise', for the
precision / component-mean stats in scanner/stats.py.

Shows one surfaced-but-unlabelled company at a time (name, one-liner, flags,
interest score) and records your verdict against its scores row.

Usage:
    python label_history.py

Controls: y = relevant, n = noise, s = skip (ask again next session), q = quit.
"""

from __future__ import annotations

import json

from scanner import db

VERDICT_MAP = {"y": "relevant", "n": "noise"}


def next_unlabelled(conn, skip_ids: set[int]):
    """One surfaced row with no verdict yet, excluding this session's
    skips. `skip_ids` is tracked in-process (not persisted) so 'skip' never
    writes anything but also never re-shows the same row in this run."""
    if skip_ids:
        placeholders = ",".join("?" * len(skip_ids))
        query = f"""
            SELECT s.*, c.name AS company_name
            FROM scores s
            JOIN companies c USING (company_number)
            WHERE s.surfaced = 1 AND s.analyst_verdict IS NULL
              AND s.score_id NOT IN ({placeholders})
            ORDER BY s.score_id
            LIMIT 1
        """
        return conn.execute(query, tuple(skip_ids)).fetchone()
    query = """
        SELECT s.*, c.name AS company_name
        FROM scores s
        JOIN companies c USING (company_number)
        WHERE s.surfaced = 1 AND s.analyst_verdict IS NULL
        ORDER BY s.score_id
        LIMIT 1
    """
    return conn.execute(query).fetchone()


def label_one(conn, score_id: int, verdict: str) -> None:
    conn.execute("UPDATE scores SET analyst_verdict = ? WHERE score_id = ?", (verdict, score_id))
    conn.commit()


def _show(row) -> None:
    brief = json.loads(row["raw_response"] or "{}")
    print()
    print(f"=== {row['company_name']}  (score_id={row['score_id']}, interest_score={row['interest_score']}) ===")
    print(f"  {brief.get('one_liner', '')}")
    if brief.get("flags_positive"):
        print("  + " + "; ".join(brief["flags_positive"]))
    if brief.get("flags_negative"):
        print("  - " + "; ".join(brief["flags_negative"]))


def main() -> None:
    conn = db.get_connection()
    db.init_db(conn)

    skip_ids: set[int] = set()
    labelled_this_session = 0

    while True:
        row = next_unlabelled(conn, skip_ids)
        if row is None:
            print("\nNo more surfaced, unlabelled companies.")
            break
        _show(row)
        choice = input("  relevant / noise / skip / quit [y/n/s/q]: ").strip().lower()
        if choice == "q":
            break
        if choice == "s":
            skip_ids.add(row["score_id"])
            continue
        if choice in VERDICT_MAP:
            label_one(conn, row["score_id"], VERDICT_MAP[choice])
            labelled_this_session += 1
            continue
        print("  (unrecognized input, treating as skip)")
        skip_ids.add(row["score_id"])

    conn.close()
    print(f"\nLabelled {labelled_this_session} companies this session.")


if __name__ == "__main__":
    main()
