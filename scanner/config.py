"""Central configuration for the UK Biotech Deal-Flow Scanner.

Everything tunable lives here so the pipeline behaviour can be changed
without touching logic. Values can be overridden with environment
variables (useful in GitHub Actions) — see the os.getenv calls below.
"""

import os
from pathlib import Path

# --- SIC codes to sweep -----------------------------------------------------
# 72110 = biotech R&D, 72190 = other natural-sciences R&D, 21100 = pharma manufacture.
SIC_CODES = os.getenv("SCANNER_SIC_CODES", "72110,72190,21100").split(",")

# --- How far back to look ---------------------------------------------------
# The cron runs daily; we look back a couple of days so a missed run or a
# late-appearing incorporation is not lost. The seen-store prevents duplicates.
LOOKBACK_DAYS = int(os.getenv("SCANNER_LOOKBACK_DAYS", "2"))

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
]
