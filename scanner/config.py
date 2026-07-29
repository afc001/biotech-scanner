"""Central configuration for the UK Biotech Deal-Flow Scanner.

Everything tunable lives here so the pipeline behaviour can be changed
without touching logic. Values can be overridden with environment
variables (useful in GitHub Actions) — see the os.getenv calls below.
"""

import os
from pathlib import Path

# --- SIC codes to sweep -----------------------------------------------------
# 72110 = biotech R&D, 72190 = other natural-sciences R&D. Pharma-manufacture
# codes 21100/21200 were tried and dropped: persistent Companies House 404s
# on 21100, and both diluted signal quality more than they added -- see
# .env.example for how to add them back as an override if you want to retest.
SIC_CODES = os.getenv("SCANNER_SIC_CODES", "72110,72190").split(",")

# --- How far back to look ---------------------------------------------------
# The cron runs daily; we look back a couple of days so a missed run or a
# late-appearing incorporation is not lost. The seen-store prevents duplicates.
LOOKBACK_DAYS = int(os.getenv("SCANNER_LOOKBACK_DAYS", "2"))

# --- Fixed calendar window override (for historical backfills) --------------
# LOOKBACK_DAYS is always relative to today, so it can't target a specific
# past window (e.g. "30 June to 14 July"). Setting BOTH of these to
# YYYY-MM-DD dates overrides LOOKBACK_DAYS entirely and sweeps that exact
# window instead. Leave both blank for normal day-to-day operation -- if only
# one is set, fetch.py raises rather than silently guessing the other end.
INCORPORATED_FROM = os.getenv("SCANNER_INCORPORATED_FROM", "")
INCORPORATED_TO = os.getenv("SCANNER_INCORPORATED_TO", "")

# --- Model ------------------------------------------------------------------
# Sonnet is a good quality/cost default for briefs; swap to a cheaper model
# for high volume, or a stronger one for a final polish pass.
MODEL = os.getenv("SCANNER_MODEL", "claude-sonnet-5")
MAX_TOKENS = int(os.getenv("SCANNER_MAX_TOKENS", "1500"))

# --- Alerting ---------------------------------------------------------------
# Companies scoring at or above this are worth surfacing loudly.
INTEREST_ALERT_THRESHOLD = int(os.getenv("SCANNER_ALERT_THRESHOLD", "4"))

# --- Companies House API ----------------------------------------------------
CH_API_BASE = "https://api.company-information.service.gov.uk"
CH_API_KEY = os.getenv("CH_API_KEY", "")
FETCH_OFFICERS = os.getenv("SCANNER_FETCH_OFFICERS", "1") == "1"

# --- Anthropic API ----------------------------------------------------------
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# --- ORCID API (optional director-credibility enrichment) -------------------
# Requires a free Public API client -- ORCID's search API needs OAuth client
# credentials even on the free tier, there's no fully open endpoint. Register
# at https://orcid.org (sign in -> Developer Tools) or see
# https://info.orcid.org/documentation/integration-guide/registering-a-public-api-client/
# Worth checking ORCID's Public API Terms of Service for commercial-use
# considerations before relying on this: https://info.orcid.org/public-client-terms-of-service/
# Leave blank to skip enrichment entirely -- it degrades gracefully.
ORCID_CLIENT_ID = os.getenv("ORCID_CLIENT_ID", "")
ORCID_CLIENT_SECRET = os.getenv("ORCID_CLIENT_SECRET", "")
FETCH_ORCID = os.getenv("SCANNER_FETCH_ORCID", "1") == "1"

# --- UKRI Gateway to Research (optional director funding-history enrichment)-
# No API key needed -- GtR's API is fully open. Toggle off if you'd rather
# not make the extra calls.
FETCH_GTR = os.getenv("SCANNER_FETCH_GTR", "1") == "1"

# --- Company website check (optional, cheapest form of "more context") -----
# No API key needed -- just guesses likely domains and does a plain HTTP GET.
# Helps some briefs a lot (a real product description) and does nothing for
# others (brand-new shells with no site yet) -- that's expected. Toggle off
# if you'd rather not make the extra HTTP calls.
FETCH_WEBSITE = os.getenv("SCANNER_FETCH_WEBSITE", "1") == "1"

# --- Paths ------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
PROMPT_FILE = ROOT / "biotech_brief_prompt.md"
DATA_DIR = ROOT / "data"
SEEN_STORE = DATA_DIR / "seen.json"
BRIEFS_DIR = DATA_DIR / "briefs"          # raw JSON briefs, one file per run
DIGESTS_DIR = ROOT / "digests"            # rendered md + html digests (served by Pages)

# Known incubator / cluster addresses — a positive location signal.
# Substrings are matched case-insensitively against the registered address.
INCUBATOR_SIGNALS = [
    "bioescalator",
    "stevenage bioscience catalyst",
    "babraham",
    "alderley park",
    "white city",
    "milner therapeutics",
    "granta park",
    "harwell",
    "norwich research park",
    "wellcome genome campus",
    "chesterford research park",
    "cambridge science park",
    "melbourn science park",
    "oxford north",
    "fallaize street",
    "the red hall",
    "thomas white street",
    "oxford science park",
    "magdalen centre",
    "milton park",
    "begbroke",
    "victoria house",
    "royal college street",
    "one portal way",
    "here east",
    "plexal",
    "canada water",
    "tribeca",
    "gridiron",
    "king's cross",
    "london cancer hub",
    "sutton",
    "edinburgh bioquarter",
    "roslin innovation centre",
    "science creates",
    "discovery park",
    "biocity",
    "medicity",

]
