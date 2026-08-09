import pytest

from scanner import db


@pytest.fixture
def db_conn():
    """A fresh, empty, in-memory database with the schema applied."""
    conn = db.get_connection(":memory:")
    db.init_db(conn)
    yield conn
    conn.close()


@pytest.fixture
def seeded_db(db_conn):
    """db_conn plus 2 runs / 3 companies / 3 labelled scores rows with
    known, hand-computable values, so stats tests can assert exact numbers
    rather than just "doesn't crash".

    run1 (2026-08-01, live, n_fetched=10):
      1001: interest_score=5, orcid confirmed, incubator matched, website found -> relevant
      1002: interest_score=1, orcid not confirmed, incubator not matched, website found -> noise
    run2 (2026-08-02, migrated, n_fetched=NULL -- never captured historically):
      1003: interest_score=4, orcid confirmed, incubator/website never recorded (NULL) -> relevant
    """
    run1 = db.insert_run(
        db_conn, run_date="2026-08-01", n_fetched=10, n_sic_matched=10,
        n_address_matched=1, n_scored=2, n_surfaced=2, source="live",
    )
    run2 = db.insert_run(
        db_conn, run_date="2026-08-02", n_scored=1, n_surfaced=1, source="migrated",
    )

    for number, name, date_ in [
        ("1001", "Co1001", "2026-08-01"),
        ("1002", "Co1002", "2026-08-01"),
        ("1003", "Co1003", "2026-08-02"),
    ]:
        db.upsert_company(db_conn, company_number=number, name=name,
                           incorporated_on=date_, sic_codes=["72110"])

    sid1 = db.insert_score(
        db_conn, company_number="1001", run_id=run1, prompt_version="v1", surfaced=True,
        brief={"interest_score": "5 — strong", "orcid_badge": {"status": "confirmed"}, "gtr_badge": None},
        incubator_match="babraham", website_status="found",
    )
    sid2 = db.insert_score(
        db_conn, company_number="1002", run_id=run1, prompt_version="v1", surfaced=True,
        brief={"interest_score": "1 — weak", "orcid_badge": None, "gtr_badge": None},
        incubator_match=None, website_status="found",
    )
    sid3 = db.insert_score(
        db_conn, company_number="1003", run_id=run2, prompt_version=None, surfaced=True,
        brief={"interest_score": "4 — decent", "orcid_badge": {"status": "confirmed"}, "gtr_badge": None},
        # incubator_match/website_status omitted: matches how migrate_history.py
        # imports a historical row -- stays NULL, not 0.
    )

    db_conn.execute("UPDATE scores SET analyst_verdict = 'relevant' WHERE score_id = ?", (sid1,))
    db_conn.execute("UPDATE scores SET analyst_verdict = 'noise' WHERE score_id = ?", (sid2,))
    db_conn.execute("UPDATE scores SET analyst_verdict = 'relevant' WHERE score_id = ?", (sid3,))
    db_conn.commit()

    return db_conn
