import sqlite3

import pytest

from scanner import db


def test_init_db_idempotent(db_conn):
    db.init_db(db_conn)  # calling a second time must not raise
    tables = {r[0] for r in db_conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"runs", "companies", "scores"} <= tables


def test_get_connection_creates_file_at_given_path(tmp_path):
    db_path = tmp_path / "scanner.db"
    conn = db.get_connection(db_path)
    db.init_db(conn)
    conn.close()
    assert db_path.exists()


def test_insert_run_returns_increasing_ids(db_conn):
    r1 = db.insert_run(db_conn, run_date="2026-08-01", source="live")
    r2 = db.insert_run(db_conn, run_date="2026-08-02", source="live")
    assert r2 > r1


def test_upsert_company_updates_rather_than_duplicates(db_conn):
    db.upsert_company(db_conn, company_number="1", name="Old Name",
                       incorporated_on="2026-08-01", sic_codes=["72110"])
    db.upsert_company(db_conn, company_number="1", name="New Name",
                       incorporated_on="2026-08-01", sic_codes=["72110"])
    rows = db_conn.execute("SELECT * FROM companies WHERE company_number = '1'").fetchall()
    assert len(rows) == 1
    assert rows[0]["name"] == "New Name"


def test_upsert_company_coalesces_address_instead_of_erasing_it(db_conn):
    # A migration import never has an address; a later live run does.
    db.upsert_company(db_conn, company_number="1", name="Co",
                       incorporated_on="2026-08-01", sic_codes=["72110"])
    db.upsert_company(db_conn, company_number="1", name="Co", incorporated_on="2026-08-01",
                       sic_codes=["72110"], registered_address="1 Science Park")
    row = db_conn.execute(
        "SELECT registered_address FROM companies WHERE company_number = '1'"
    ).fetchone()
    assert row["registered_address"] == "1 Science Park"

    # A second migration-style upsert (no address) must not blank it back out.
    db.upsert_company(db_conn, company_number="1", name="Co",
                       incorporated_on="2026-08-01", sic_codes=["72110"])
    row = db_conn.execute(
        "SELECT registered_address FROM companies WHERE company_number = '1'"
    ).fetchone()
    assert row["registered_address"] == "1 Science Park"


def test_scores_unique_constraint_allows_new_run_but_rejects_duplicate(db_conn):
    run1 = db.insert_run(db_conn, run_date="2026-08-01", source="live")
    run2 = db.insert_run(db_conn, run_date="2026-08-02", source="live")
    db.upsert_company(db_conn, company_number="1", name="Co",
                       incorporated_on="2026-08-01", sic_codes=["72110"])
    brief = {"interest_score": "3 — ok"}

    db.insert_score(db_conn, company_number="1", run_id=run1, brief=brief,
                     prompt_version="v1", surfaced=False)
    # Same company, a later run_id (e.g. re-scored under a new prompt) -- must succeed.
    db.insert_score(db_conn, company_number="1", run_id=run2, brief=brief,
                     prompt_version="v2", surfaced=False)

    # Same company, same run_id again -- must be rejected by UNIQUE(company_number, run_id).
    with pytest.raises(sqlite3.IntegrityError):
        db.insert_score(db_conn, company_number="1", run_id=run1, brief=brief,
                         prompt_version="v1", surfaced=False)


def test_insert_score_derives_confirmed_flags_from_badges(db_conn):
    run_id = db.insert_run(db_conn, run_date="2026-08-01", source="live")
    db.upsert_company(db_conn, company_number="1", name="Co",
                       incorporated_on="2026-08-01", sic_codes=["72110"])
    brief = {
        "interest_score": "5 — x",
        "orcid_badge": {"status": "confirmed"},
        "gtr_badge": {"status": "no_match"},
    }
    sid = db.insert_score(db_conn, company_number="1", run_id=run_id, brief=brief,
                           prompt_version="v1", surfaced=True)
    row = db_conn.execute("SELECT * FROM scores WHERE score_id = ?", (sid,)).fetchone()
    assert row["interest_score"] == 5
    assert row["orcid_confirmed"] == 1
    assert row["gtr_confirmed"] == 0


def test_insert_score_unset_incubator_and_website_stay_null(db_conn):
    """migrate_history.py never passes incubator_match/website_status --
    those columns must be NULL (unknown), not 0 (checked, no match)."""
    run_id = db.insert_run(db_conn, run_date="2026-08-01", source="migrated")
    db.upsert_company(db_conn, company_number="1", name="Co",
                       incorporated_on="2026-08-01", sic_codes=["72110"])
    sid = db.insert_score(db_conn, company_number="1", run_id=run_id,
                           brief={"interest_score": "2 — x"}, prompt_version=None, surfaced=False)
    row = db_conn.execute("SELECT * FROM scores WHERE score_id = ?", (sid,)).fetchone()
    assert row["incubator_match"] is None
    assert row["incubator_matched"] is None
    assert row["website_status"] is None
    assert row["website_found"] is None


def test_insert_score_explicit_none_incubator_is_zero_not_null(db_conn):
    """A live run's record.get('incubator_match') can genuinely be None
    (checked, no match) -- that must store as 0, not NULL."""
    run_id = db.insert_run(db_conn, run_date="2026-08-01", source="live")
    db.upsert_company(db_conn, company_number="1", name="Co",
                       incorporated_on="2026-08-01", sic_codes=["72110"])
    sid = db.insert_score(db_conn, company_number="1", run_id=run_id,
                           brief={"interest_score": "2 — x"}, prompt_version="v1",
                           surfaced=False, incubator_match=None, website_status="not_found")
    row = db_conn.execute("SELECT * FROM scores WHERE score_id = ?", (sid,)).fetchone()
    assert row["incubator_matched"] == 0
    assert row["website_found"] == 0


def test_analyst_verdict_check_constraint_rejects_invalid_value(db_conn):
    run_id = db.insert_run(db_conn, run_date="2026-08-01", source="live")
    db.upsert_company(db_conn, company_number="1", name="Co",
                       incorporated_on="2026-08-01", sic_codes=["72110"])
    sid = db.insert_score(db_conn, company_number="1", run_id=run_id,
                           brief={"interest_score": "2 — x"}, prompt_version="v1", surfaced=False)
    with pytest.raises(sqlite3.IntegrityError):
        db_conn.execute("UPDATE scores SET analyst_verdict = 'maybe' WHERE score_id = ?", (sid,))


def test_get_prompt_version_is_deterministic_and_distinguishing(db_conn):
    assert db.get_prompt_version("hello") == db.get_prompt_version("hello")
    assert db.get_prompt_version("hello") != db.get_prompt_version("world")


def test_insert_score_repeat_founder_confirmed_derives_from_badge(db_conn):
    run_id = db.insert_run(db_conn, run_date="2026-08-01", source="live")
    db.upsert_company(db_conn, company_number="1", name="Co",
                       incorporated_on="2026-08-01", sic_codes=["72110"])

    confirmed_brief = {"interest_score": "5 — x",
                        "repeat_founder_badge": {"status": "same_sector_confirmed"}}
    sid1 = db.insert_score(db_conn, company_number="1", run_id=run_id,
                            brief=confirmed_brief, prompt_version="v1", surfaced=True)
    row1 = db_conn.execute("SELECT * FROM scores WHERE score_id = ?", (sid1,)).fetchone()
    assert row1["repeat_founder_status"] == "same_sector_confirmed"
    assert row1["repeat_founder_confirmed"] == 1

    db.upsert_company(db_conn, company_number="2", name="Co2",
                       incorporated_on="2026-08-01", sic_codes=["72110"])
    other_sector_brief = {"interest_score": "2 — x",
                           "repeat_founder_badge": {"status": "other_sector_only"}}
    sid2 = db.insert_score(db_conn, company_number="2", run_id=run_id,
                            brief=other_sector_brief, prompt_version="v1", surfaced=False)
    row2 = db_conn.execute("SELECT * FROM scores WHERE score_id = ?", (sid2,)).fetchone()
    assert row2["repeat_founder_status"] == "other_sector_only"
    assert row2["repeat_founder_confirmed"] == 0


def test_insert_score_advisor_pattern_missing_key_stays_null(db_conn):
    """A migrated historical brief predates this feature entirely -- the
    key is simply absent, which must mean NULL (never checked), not 0."""
    run_id = db.insert_run(db_conn, run_date="2026-08-01", source="migrated")
    db.upsert_company(db_conn, company_number="1", name="Co",
                       incorporated_on="2026-08-01", sic_codes=["72110"])
    sid = db.insert_score(db_conn, company_number="1", run_id=run_id,
                           brief={"interest_score": "2 — x"}, prompt_version=None, surfaced=False)
    row = db_conn.execute("SELECT * FROM scores WHERE score_id = ?", (sid,)).fetchone()
    assert row["advisor_pattern"] is None
    assert row["repeat_founder_status"] is None
    assert row["repeat_founder_confirmed"] is None


def test_insert_score_advisor_pattern_explicit_false_is_zero_not_null(db_conn):
    run_id = db.insert_run(db_conn, run_date="2026-08-01", source="live")
    db.upsert_company(db_conn, company_number="1", name="Co",
                       incorporated_on="2026-08-01", sic_codes=["72110"])
    brief = {"interest_score": "2 — x", "advisor_pattern": False}
    sid = db.insert_score(db_conn, company_number="1", run_id=run_id,
                           brief=brief, prompt_version="v1", surfaced=False)
    row = db_conn.execute("SELECT * FROM scores WHERE score_id = ?", (sid,)).fetchone()
    assert row["advisor_pattern"] == 0


def test_init_db_adds_missing_scores_columns_to_pre_existing_table():
    """Simulates the real production scenario this feature landed into:
    data/scanner.db already existed with a populated scores table before
    repeat_founder_status/repeat_founder_confirmed/advisor_pattern existed.
    CREATE TABLE IF NOT EXISTS alone would silently no-op and never add
    them -- this confirms init_db()'s ALTER TABLE step does."""
    conn = db.get_connection(":memory:")
    # The exact pre-repeat-founder schema (everything SCHEMA_SQL's scores
    # table + indexes need EXCEPT the three new columns) -- mirrors the
    # real data/scanner.db this migration actually landed on this session.
    conn.execute(
        """
        CREATE TABLE scores (
            score_id          INTEGER PRIMARY KEY AUTOINCREMENT,
            company_number    TEXT NOT NULL,
            run_id            INTEGER NOT NULL,
            interest_score    INTEGER,
            interest_score_raw TEXT,
            orcid_status      TEXT,
            orcid_confirmed   INTEGER,
            gtr_status        TEXT,
            gtr_confirmed     INTEGER,
            incubator_match   TEXT,
            incubator_matched INTEGER,
            website_status    TEXT,
            website_found     INTEGER,
            search_enriched   INTEGER NOT NULL DEFAULT 0,
            surfaced          INTEGER NOT NULL,
            analyst_verdict   TEXT,
            raw_response      TEXT,
            prompt_version    TEXT,
            UNIQUE(company_number, run_id)
        )
        """
    )
    conn.execute(
        "INSERT INTO scores (company_number, run_id, interest_score, surfaced) VALUES (?, ?, ?, ?)",
        ("1", 1, 3, 0),
    )
    conn.commit()

    db.init_db(conn)

    cols = {row[1] for row in conn.execute("PRAGMA table_info(scores)")}
    assert {"repeat_founder_status", "repeat_founder_confirmed", "advisor_pattern"} <= cols
    row = conn.execute("SELECT * FROM scores WHERE company_number = '1'").fetchone()
    assert row["interest_score"] == 3  # pre-existing data untouched
    assert row["repeat_founder_status"] is None  # new column, correctly NULL
    conn.close()
