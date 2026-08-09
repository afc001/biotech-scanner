"""SQLite-backed run history for the scanner (stdlib sqlite3, no ORM).

All reads/writes to data/scanner.db go through this module -- run.py,
migrate_history.py, stats.py, and label_history.py import from here rather
than opening their own connection with ad-hoc SQL, so the schema and the
brief-dict -> row mapping have exactly one source of truth.

Three tables:
  runs      -- one row per pipeline run (live or migrated from history)
  companies -- one row per company ever seen, keyed by Companies House number
  scores    -- one row per (company, run) score -- supports a company being
               re-scored under a later run_id/prompt_version without losing
               its original row

NULL vs 0 matters throughout this schema: NULL means "we don't know" (e.g.
a signal that was never checked for a historical/migrated row), 0 means "we
checked, and it was absent." Collapsing the two would misrepresent history
that predates a signal being tracked at all.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
from pathlib import Path

from . import config

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS runs (
    run_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date          TEXT NOT NULL,
    prompt_version    TEXT,
    git_sha           TEXT,
    n_fetched         INTEGER,
    n_sic_matched     INTEGER,
    n_address_matched INTEGER,
    n_scored          INTEGER,
    n_surfaced        INTEGER,
    source            TEXT NOT NULL DEFAULT 'live' CHECK (source IN ('live', 'migrated')),
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_runs_run_date ON runs(run_date);

CREATE TABLE IF NOT EXISTS companies (
    company_number     TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    incorporated_on     TEXT,
    sic_codes           TEXT,
    registered_address  TEXT,
    website             TEXT,
    website_status      TEXT
);

CREATE TABLE IF NOT EXISTS scores (
    score_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    company_number    TEXT NOT NULL REFERENCES companies(company_number),
    run_id            INTEGER NOT NULL REFERENCES runs(run_id),
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
    analyst_verdict   TEXT CHECK (analyst_verdict IN ('relevant', 'noise') OR analyst_verdict IS NULL),
    raw_response      TEXT,
    prompt_version    TEXT,
    UNIQUE(company_number, run_id)
);
CREATE INDEX IF NOT EXISTS idx_scores_run_id ON scores(run_id);
CREATE INDEX IF NOT EXISTS idx_scores_company_number ON scores(company_number);
CREATE INDEX IF NOT EXISTS idx_scores_analyst_verdict ON scores(analyst_verdict);
CREATE INDEX IF NOT EXISTS idx_scores_interest_score ON scores(interest_score);
"""

# Sentinel distinguishing "caller didn't pass this" (NULL -- unknown/never
# checked, e.g. a migrated historical row) from "caller passed None" (0 --
# checked, and there was no match). See module docstring.
_UNSET = object()


def get_connection(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Open a connection to the scanner DB, creating its parent dir if needed.

    Pass db_path=":memory:" for tests. Row access is by column name
    (sqlite3.Row) and foreign keys are enforced (off by default in sqlite3
    unless set per-connection)."""
    path = str(db_path) if db_path is not None else str(config.DB_PATH)
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create the schema if it doesn't exist yet. Safe to call every run."""
    conn.executescript(SCHEMA_SQL)
    conn.commit()


def _score_int(interest_score) -> int:
    """Pull the leading integer out of an interest_score string like
    '4 — ...'. Duplicated from render.score_int()/generate._score_int()
    rather than imported -- same reasoning as those two: not worth a
    cross-module dependency for one regex."""
    m = re.match(r"\s*(\d)", str(interest_score))
    return int(m.group(1)) if m else 0


def get_prompt_version(full_system: str) -> str:
    """Short, stable, deterministic identifier for the assembled system
    prompt actually sent to the model (not just biotech_brief_prompt.md --
    generate.load_prompt() appends extra rules in code, so the assembled
    string is the only thing that fully determines behaviour)."""
    return hashlib.sha256(full_system.encode("utf-8")).hexdigest()[:12]


def get_git_sha() -> str | None:
    """GITHUB_SHA in CI, else `git rev-parse HEAD` locally. None if neither
    is available (e.g. not a git checkout)."""
    sha = os.environ.get("GITHUB_SHA")
    if sha:
        return sha
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=config.ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        return result.stdout.strip() or None
    except (subprocess.SubprocessError, OSError):
        return None


def insert_run(
    conn: sqlite3.Connection,
    *,
    run_date: str,
    prompt_version: str | None = None,
    git_sha: str | None = None,
    n_fetched: int | None = None,
    n_sic_matched: int | None = None,
    n_address_matched: int | None = None,
    n_scored: int | None = None,
    n_surfaced: int | None = None,
    source: str = "live",
) -> int:
    cur = conn.execute(
        """
        INSERT INTO runs (
            run_date, prompt_version, git_sha, n_fetched, n_sic_matched,
            n_address_matched, n_scored, n_surfaced, source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (run_date, prompt_version, git_sha, n_fetched, n_sic_matched,
         n_address_matched, n_scored, n_surfaced, source),
    )
    return cur.lastrowid


def upsert_company(
    conn: sqlite3.Connection,
    *,
    company_number: str,
    name: str,
    incorporated_on: str | None = None,
    sic_codes: list[str] | str | None = None,
    registered_address: str | None = None,
    website: str | None = None,
    website_status: str | None = None,
) -> None:
    """Insert a company, or update it if already known. COALESCE on the
    fields that are only ever available from a live run's fetch-time record
    (registered_address/website/website_status) so a later migration import
    (which never has them) can't blank out a value a live run already
    populated, and vice versa."""
    sic_text = ",".join(sic_codes) if isinstance(sic_codes, list) else sic_codes
    conn.execute(
        """
        INSERT INTO companies (
            company_number, name, incorporated_on, sic_codes,
            registered_address, website, website_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(company_number) DO UPDATE SET
            name = excluded.name,
            incorporated_on = excluded.incorporated_on,
            sic_codes = excluded.sic_codes,
            registered_address = COALESCE(excluded.registered_address, companies.registered_address),
            website = COALESCE(excluded.website, companies.website),
            website_status = COALESCE(excluded.website_status, companies.website_status)
        """,
        (company_number, name, incorporated_on, sic_text,
         registered_address, website, website_status),
    )


def insert_score(
    conn: sqlite3.Connection,
    *,
    company_number: str,
    run_id: int,
    brief: dict,
    prompt_version: str | None,
    surfaced: bool,
    incubator_match=_UNSET,
    website_status=_UNSET,
) -> int:
    """Turn a brief dict (plus the couple of in-run-only signals that never
    made it into the brief dict itself) into one scores row.

    incubator_match/website_status default to _UNSET, not None: a live run
    always passes the record's real value (a string or None -- "checked, no
    match"), while migrate_history.py never passes them at all, since that
    information was never persisted for historical runs -- see module
    docstring for why NULL and 0 mean different things here.
    """
    interest_score_raw = str(brief.get("interest_score", ""))
    interest_score = _score_int(interest_score_raw)

    orcid_badge = brief.get("orcid_badge")
    orcid_status = orcid_badge.get("status") if orcid_badge else None
    orcid_confirmed = 1 if orcid_status == "confirmed" else 0

    gtr_badge = brief.get("gtr_badge")
    gtr_status = gtr_badge.get("status") if gtr_badge else None
    gtr_confirmed = 1 if gtr_status == "confirmed" else 0

    if incubator_match is _UNSET:
        incubator_match_val, incubator_matched = None, None
    else:
        incubator_match_val = incubator_match or None
        incubator_matched = 1 if incubator_match else 0

    if website_status is _UNSET:
        website_status_val, website_found = None, None
    else:
        website_status_val = website_status
        website_found = 1 if website_status == "found" else 0

    # A brief that never went through the (opt-in) web-search pass simply
    # lacks this key -- run.py itself treats that as False via setdefault(),
    # so we follow the same convention rather than leaving it NULL.
    search_enriched = 1 if brief.get("search_enriched") else 0

    cur = conn.execute(
        """
        INSERT INTO scores (
            company_number, run_id, interest_score, interest_score_raw,
            orcid_status, orcid_confirmed, gtr_status, gtr_confirmed,
            incubator_match, incubator_matched, website_status, website_found,
            search_enriched, surfaced, raw_response, prompt_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (company_number, run_id, interest_score, interest_score_raw,
         orcid_status, orcid_confirmed, gtr_status, gtr_confirmed,
         incubator_match_val, incubator_matched,
         website_status_val, website_found,
         search_enriched, int(bool(surfaced)), json.dumps(brief), prompt_version),
    )
    return cur.lastrowid
