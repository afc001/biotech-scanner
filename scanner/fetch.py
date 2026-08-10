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

from . import config, gtr, orcid, repeat_founder, website

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
                # Path to this officer's own appointment history across
                # every company they've ever directed -- see
                # repeat_founder.py, which follows this link.
                "appointments_link": o.get("links", {}).get("officer", {}).get("appointments", ""),
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


def _match_incubator(address: str) -> str | None:
    """Check a registered address against config.INCUBATOR_SIGNALS.

    Returns the matching signal string, or None. Matching is a
    case-insensitive substring check, same as the list's own doc comment
    always promised (the list existed but was never actually consulted by
    the pipeline until now)."""
    addr_lower = address.lower()
    for signal in config.INCUBATOR_SIGNALS:
        if signal in addr_lower:
            return signal
    return None


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
        "incubator_match": _match_incubator(address),
        "officers": [],
    }


def get_new_companies() -> tuple[list[dict], dict]:
    """Sweep all SIC codes, drop anything already seen, enrich with officers.

    NOTE: this does NOT mark companies as seen — that only happens once a
    brief has actually been generated and rendered (see mark_seen()). If we
    marked them seen here, a crash in generate.py or render.py would
    permanently drop companies that were fetched but never actually
    processed into a digest.

    Returns (new_companies, fetch_counts). fetch_counts is
    {"n_fetched": int, "n_sic_matched": int} -- raw API hits summed across
    SIC codes, before the seen-store/cross-SIC dedup below. The two keys are
    equal by construction: the Companies House API filters by SIC code
    server-side (via the sic_codes search param), so there is no separate
    "matched by SIC" stage distinct from "fetched" in this pipeline -- both
    are still returned so the runs table's funnel shape stays meaningful if
    a future version of the pipeline ever fetches more broadly and filters
    locally.
    """
    if config.INCORPORATED_FROM or config.INCORPORATED_TO:
        if not (config.INCORPORATED_FROM and config.INCORPORATED_TO):
            raise RuntimeError(
                "SCANNER_INCORPORATED_FROM and SCANNER_INCORPORATED_TO must both be set "
                "for a fixed-window run -- only one was provided."
            )
        incorporated_from = config.INCORPORATED_FROM
        incorporated_to = config.INCORPORATED_TO
        print(f"Fixed-window override active: {incorporated_from} -> {incorporated_to} "
              f"(SCANNER_LOOKBACK_DAYS ignored)")
    else:
        today = date.today()
        incorporated_from = (today - timedelta(days=config.LOOKBACK_DAYS)).isoformat()
        incorporated_to = today.isoformat()

    seen = _load_seen()
    by_number: dict[str, dict] = {}
    n_fetched = 0

    for sic in config.SIC_CODES:
        sic = sic.strip()
        print(f"searching SIC {sic} ({incorporated_from} -> {incorporated_to})")
        try:
            sic_results = search_sic(sic, incorporated_from, incorporated_to)
        except requests.HTTPError as exc:
            print(f"  SIC {sic} failed after retries, skipping this run: {exc}")
            continue
        n_fetched += len(sic_results)
        for item in sic_results:
            number = item.get("company_number")
            if not number or number in seen or number in by_number:
                continue
            by_number[number] = _normalise(item)

    new_companies = list(by_number.values())
    print(f"{len(new_companies)} new companies after dedupe")

    if config.FETCH_OFFICERS:
        if config.FETCH_ORCID:
            print(f"  ORCID enrichment: {'enabled' if orcid.enabled() else 'skipped (ORCID_CLIENT_ID/SECRET not set)'}")
        if config.FETCH_GTR:
            print("  GtR enrichment: enabled")
        if config.FETCH_REPEAT_FOUNDER:
            print("  Repeat-founder enrichment: enabled")
        for record in new_companies:
            record["officers"] = fetch_officers(record["company_number"])
            if config.FETCH_ORCID:
                record["officers"] = orcid.enrich_officers(record["officers"])
                for o in record["officers"]:
                    if "orcid" in o:
                        print(f"    ORCID lookup: {o.get('name', '')} -> {o['orcid'].get('status')}")
            if config.FETCH_GTR:
                record["officers"] = gtr.enrich_officers(record["officers"])
                for o in record["officers"]:
                    if "gtr" in o:
                        print(f"    GtR lookup: {o.get('name', '')} -> {o['gtr'].get('status')}")
            if config.FETCH_REPEAT_FOUNDER:
                record["officers"] = repeat_founder.enrich_officers(
                    record["officers"], record["company_number"]
                )
                for o in record["officers"]:
                    if "repeat_founder" in o:
                        print(f"    Repeat-founder lookup: {o.get('name', '')} -> {o['repeat_founder'].get('status')}")

    if config.FETCH_WEBSITE:
        # Independent of FETCH_OFFICERS -- this is a company-level check
        # (guessed domain + plain HTTP GET), not an officer-level lookup.
        print("  Website check: enabled")
        for record in new_companies:
            record["website"] = website.check_company_website(record.get("company_name", ""))
            if record["website"].get("status") == "found":
                print(f"    Website found: {record['company_name']} -> {record['website'].get('url')}")

    return new_companies, {"n_fetched": n_fetched, "n_sic_matched": n_fetched}


def mark_seen(records: list[dict], run_date: date | None = None) -> None:
    """Persist company numbers as seen. Call ONLY after their briefs have
    been successfully generated and rendered, so a mid-pipeline failure
    leaves them eligible for retry on the next run instead of being
    silently dropped."""
    if not records:
        return
    run_date = run_date or date.today()
    seen = _load_seen()
    for record in records:
        number = record.get("company_number")
        if number:
            seen[number] = run_date.isoformat()
    _save_seen(seen)
