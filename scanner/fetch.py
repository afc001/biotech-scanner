"""Fetch newly incorporated companies from Companies House and dedupe them.

Uses the Advanced Company Search endpoint to pull recent incorporations
filtered by SIC code, then optionally fetches officers for each new company
so the brief has director data. A JSON seen-store guarantees each company is
only ever processed once.

Auth: Companies House uses HTTP Basic with the API key as the username and an
empty password. Register a key at
https://developer.company-information.service.gov.uk/
"""

from __future__ import annotations

import json
import time
from datetime import date, timedelta

import requests

from . import config

SEARCH_URL = f"{config.CH_API_BASE}/advanced-search/companies"
PAGE_SIZE = 100  # advanced search allows up to 5000; 100 keeps responses small


def _auth() -> tuple[str, str]:
    if not config.CH_API_KEY:
        raise RuntimeError("CH_API_KEY is not set. Add it to your environment / Actions secrets.")
    return (config.CH_API_KEY, "")


RETRYABLE_STATUS = {429, 404, 500, 502, 503, 504}


def _get(url: str, params: dict | None = None) -> dict:
    """GET with basic auth, retry on rate-limits and transient errors."""
    for attempt in range(4):
        resp = requests.get(url, params=params, auth=_auth(), timeout=30)
        if resp.status_code in RETRYABLE_STATUS:
            wait = 2 ** attempt * 5
            print(f"  got {resp.status_code}, retrying in {wait}s (attempt {attempt + 1}/4)")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()
    resp.raise_for_status()
    return {}


def search_sic(sic_code: str, incorporated_from: str, incorporated_to: str) -> list[dict]:
    """Return all companies for one SIC code incorporated in the date window."""
    results: list[dict] = []
    start_index = 0
    while True:
        params = {
            "sic_codes": sic_code,
            "incorporated_from": incorporated_from,
            "incorporated_to": incorporated_to,
            "size": PAGE_SIZE,
            "start_index": start_index,
        }
        data = _get(SEARCH_URL, params)
        items = data.get("items", [])
        results.extend(items)
        hits = data.get("hits", 0)
        start_index += PAGE_SIZE
        if start_index >= hits or not items:
            break
    return results


def fetch_officers(company_number: str) -> list[dict]:
    """Return a compact list of officers for a company."""
    url = f"{config.CH_API_BASE}/company/{company_number}/officers"
    try:
        data = _get(url)
    except requests.HTTPError:
        return []
    officers = []
    for o in data.get("items", []):
        dob = o.get("date_of_birth") or {}
        officers.append(
            {
                "name": o.get("name", ""),
                "role": o.get("officer_role", ""),
                "appointed_on": o.get("appointed_on", ""),
                "occupation": o.get("occupation", ""),
                "nationality": o.get("nationality", ""),
                "dob": f"{dob.get('month', '')}/{dob.get('year', '')}".strip("/"),
            }
        )
    return officers


def _load_seen() -> dict:
    if config.SEEN_STORE.exists():
        return json.loads(config.SEEN_STORE.read_text())
    return {}


def _save_seen(seen: dict) -> None:
    config.SEEN_STORE.parent.mkdir(parents=True, exist_ok=True)
    config.SEEN_STORE.write_text(json.dumps(seen, indent=2, sort_keys=True))


def _normalise(item: dict) -> dict:
    """Flatten a Companies House search item into the record our prompt expects."""
    addr = item.get("registered_office_address", {}) or {}
    address = ", ".join(
        v
        for v in [
            addr.get("address_line_1"),
            addr.get("address_line_2"),
            addr.get("locality"),
            addr.get("region"),
            addr.get("postal_code"),
            addr.get("country"),
        ]
        if v
    )
    return {
        "company_name": item.get("company_name", ""),
        "company_number": item.get("company_number", ""),
        "date_of_creation": item.get("date_of_creation", ""),
        "sic_codes": item.get("sic_codes", []),
        "registered_address": address,
        "officers": [],
    }


def get_new_companies() -> list[dict]:
    """Sweep all SIC codes, drop anything already seen, enrich with officers."""
    today = date.today()
    incorporated_from = (today - timedelta(days=config.LOOKBACK_DAYS)).isoformat()
    incorporated_to = today.isoformat()

    seen = _load_seen()
    by_number: dict[str, dict] = {}

    for sic in config.SIC_CODES:
        sic = sic.strip()
        print(f"searching SIC {sic} ({incorporated_from} -> {incorporated_to})")
        try:
            sic_results = search_sic(sic, incorporated_from, incorporated_to)
        except requests.HTTPError as exc:
            print(f"  SIC {sic} failed after retries, skipping this run: {exc}")
            continue
        for item in sic_results:
            number = item.get("company_number")
            if not number or number in seen or number in by_number:
                continue
            by_number[number] = _normalise(item)

    new_companies = list(by_number.values())
    print(f"{len(new_companies)} new companies after dedupe")

    if config.FETCH_OFFICERS:
        for record in new_companies:
            record["officers"] = fetch_officers(record["company_number"])

    # Mark everything seen so the next run skips it.
    for record in new_companies:
        seen[record["company_number"]] = today.isoformat()
    _save_seen(seen)

    return new_companies
