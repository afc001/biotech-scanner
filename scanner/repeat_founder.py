"""Optional enrichment: check whether a director has previously (or
currently also) held a directorship at ANOTHER company in this scanner's
target sector (SIC_CODES) -- a repeat-founder-in-this-sector signal,
distinct from and complementary to ORCID (publication record) and GtR
(grant history).

Unlike ORCID, this needs NO separate registration -- it reuses the same
CH_API_KEY basic auth as everything else in fetch.py. Every officer
already returned by GET /company/{number}/officers carries a
links.officer.appointments URL (confirmed live, e.g.
"/officers/XdPks0JEoCmmkXiAkEzm9uOm7r8/appointments") pointing to that
person's full appointment history across every company they've ever
directed. Following it costs one extra request per officer; confirming a
same-sector match costs one more request per distinct prior company (to
read its sic_codes, which aren't in the appointments response) -- capped
at REPEAT_FOUNDER_SIC_LOOKUP_CAP to avoid a request-volume spike on a
professional/nominee director listed at dozens or hundreds of shell
companies.

Real example found while building this (KYTFOX LIMITED / ECMERA
THERAPEUTICS LIMITED era briefs): Prof. Melinda Duer, newly appointed to
a scanned company, was previously a director of Cambridge Oncology Ltd
(2018-2024) -- itself a SIC 72110 company. That appointment's
company_status/appointed_on/resigned_on were all present in the SAME
appointments response, at no extra cost.

IMPORTANT CAVEAT (Companies House community forum -- no official CH
confirmation exists): officer-ID matching is reliable in ONE direction
only. If two officer records share an officer_id they are confirmed the
same person -- but the same real person can sometimes be split across
multiple different officer_ids by Companies House's own system, for
reasons nobody outside CH has documented ("we sometimes come across the
same director having multiple IDs; we're not sure why" -- a community
member, not CH staff). Practical effect: a "nothing found" result here
must be treated as "no information," never as "confirmed first-time
director" -- the same absence-is-not-evidence philosophy already applied
to ORCID/GtR. We are only ever surfacing POSITIVELY confirmed prior
appointments under one specific officer_id, never trying to prove a
negative.
"""

from __future__ import annotations

import time

import requests

from . import config

REQUEST_DELAY_SECONDS = 0.3  # be a polite API citizen, same pacing as orcid.py/gtr.py
# Not 404 -- unlike fetch.py's search endpoint (which transiently 404s),
# a missing officer/company record here is a genuine "gone" response, not
# worth retrying.
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _auth() -> tuple[str, str]:
    if not config.CH_API_KEY:
        raise RuntimeError("CH_API_KEY is not set.")
    return (config.CH_API_KEY, "")


def _get(url: str) -> dict:
    """GET with basic auth, brief retry on rate-limit/5xx. Raises on a
    404 or exhausted retries -- callers catch requests.RequestException,
    same convention as orcid.py/gtr.py's lookup_director()."""
    resp = None
    for attempt in range(3):
        resp = requests.get(url, auth=_auth(), timeout=15)
        if resp.status_code in RETRYABLE_STATUS:
            time.sleep(2 ** attempt * 2)
            continue
        resp.raise_for_status()
        return resp.json()
    resp.raise_for_status()
    return {}


def _is_active(item: dict) -> bool:
    return not item.get("resigned_on")


def lookup_director(appointments_link: str, exclude_company_number: str) -> dict:
    """Follow one officer's Companies House appointments link and check
    for a prior/current directorship in the target sector, excluding the
    company currently being scanned (which is always one of the
    appointments returned).

    total_results/active_count/resigned_count are recomputed locally from
    the filtered other-companies list rather than trusted from Companies
    House's own pre-computed fields -- those include the appointment being
    scanned itself, and naively subtracting 1 is fragile (what if the
    officer appears twice, or the scanned appointment isn't first). Other
    companies are sorted active-first, then most-recent appointed_on, so
    the SIC-lookup cap prioritises "is this person currently running
    something else" over pure recency.
    """
    if not appointments_link:
        return {"status": "no_appointments_link"}

    try:
        data = _get(f"{config.CH_API_BASE}{appointments_link}")
    except requests.RequestException as exc:
        return {"status": "lookup_failed", "error": str(exc)}
    finally:
        time.sleep(REQUEST_DELAY_SECONDS)

    other_items = [
        item for item in data.get("items", [])
        if item.get("appointed_to", {}).get("company_number") != exclude_company_number
    ]

    if not other_items:
        return {
            "status": "no_other_appointments_found",
            "total_results": 0,
            "active_count": 0,
            "resigned_count": 0,
            "other_companies": [],
            "same_sector_matches": [],
            "sic_lookup_capped": False,
            "sic_lookups_performed": 0,
        }

    other_items.sort(key=lambda it: (_is_active(it), it.get("appointed_on") or ""), reverse=True)

    target_sics = {c.strip() for c in config.SIC_CODES}
    other_companies: list[dict] = []
    same_sector_matches: list[dict] = []
    sic_lookups_performed = 0

    for idx, item in enumerate(other_items):
        to = item.get("appointed_to", {})
        company = {
            "company_name": to.get("company_name", ""),
            "company_number": to.get("company_number", ""),
            "company_status": to.get("company_status", ""),
            "appointed_on": item.get("appointed_on", ""),
            "resigned_on": item.get("resigned_on"),
            "sic_codes": None,  # None = not looked up (beyond the cap), [] = looked up, no codes
        }
        if idx < config.REPEAT_FOUNDER_SIC_LOOKUP_CAP:
            try:
                profile = _get(f"{config.CH_API_BASE}/company/{company['company_number']}")
                company["sic_codes"] = profile.get("sic_codes", [])
                sic_lookups_performed += 1
            except requests.RequestException:
                company["sic_codes"] = None
            finally:
                time.sleep(REQUEST_DELAY_SECONDS)
            if company["sic_codes"] and target_sics & set(company["sic_codes"]):
                same_sector_matches.append(company)
        other_companies.append(company)

    active_count = sum(1 for c in other_companies if not c["resigned_on"])
    resigned_count = len(other_companies) - active_count

    return {
        "status": "same_sector_confirmed" if same_sector_matches else "other_sector_only",
        "total_results": len(other_companies),
        "active_count": active_count,
        "resigned_count": resigned_count,
        "other_companies": other_companies,
        "same_sector_matches": same_sector_matches,
        "sic_lookup_capped": len(other_items) > config.REPEAT_FOUNDER_SIC_LOOKUP_CAP,
        "sic_lookups_performed": sic_lookups_performed,
    }


def enrich_officers(officers: list[dict], exclude_company_number: str) -> list[dict]:
    """Attach a repeat-founder lookup result to each officer dict (returns
    new dicts, doesn't mutate input). Caches by the officer's own
    appointments-link path -- effectively their officer_id -- rather than
    by name: unlike orcid.py/gtr.py, two officers sharing a name here are
    NOT guaranteed to be the same person, so name-keying would be wrong in
    the opposite direction of this module's whole caveat (see module
    docstring). Falls back to a per-name cache key only when
    appointments_link is genuinely absent, purely to avoid repeating
    identical no_appointments_link work for a duplicate-name officer."""
    if not officers:
        return officers
    cache: dict[str, dict] = {}
    enriched = []
    for o in officers:
        o = dict(o)
        link = o.get("appointments_link") or f"__no_link__:{o.get('name', '')}"
        if link not in cache:
            cache[link] = lookup_director(o.get("appointments_link", ""), exclude_company_number)
        o["repeat_founder"] = cache[link]
        enriched.append(o)
    return enriched
