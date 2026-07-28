"""Optional enrichment: check company directors against UKRI's Gateway to
Research (GtR) database of publicly-funded research investigators, to
surface prior UKRI/Innovate UK grant history -- a complementary signal to
ORCID's publication history (this covers funding track record, ORCID
covers papers; some people have one but not the other).

Unlike ORCID, GtR needs NO authentication or registration at all -- it's a
fully open public API: https://gtr.ukri.org/resources/api.html

IMPORTANT: GtR's basic "term=" search is a loose relevance search across
many fields, NOT an exact person-name filter -- verified live: searching
"David Johnson" with fields restricted to just the name field still
surfaced "Paul Johnson", "Mark Johnson", "Louise Johnson", etc. To get a
genuine exact-name match we instead combine two facet filters (exact
family name + exact first name) -- the same mechanism GtR's own search UI
uses internally. Facet IDs are base64("<field>|<value>|string"); this was
reverse-engineered from the live API response (not documented anywhere),
so it could silently break if GtR ever changes their facet encoding --
worth spot-checking if lookups start looking wrong.

Verified live: combining facets for "David Johnson" (a genuinely common
name) narrowed GtR's loose 3195-result search down to an exact count of 8
-- meaningfully smaller than ORCID's 202 for the identical name, since
GtR only indexes UK publicly-funded research investigators, not the
global research population.
"""

from __future__ import annotations

import base64
import time

import requests

SEARCH_URL = "https://gtr.ukri.org/api/search/person"
REQUEST_DELAY_SECONDS = 0.3  # be a polite public-API citizen


def _split_name(name: str) -> tuple[str, str] | None:
    """Companies House officer names come as 'SURNAME, Given Names[, Title]'
    (e.g. 'HESSEL, Edith Margarethe, Dr'). Returns (given_names, family_name),
    or None if the name can't be confidently split. Same convention as
    orcid.py's _split_name -- kept independent rather than shared since
    these two modules should be able to evolve without coupling."""
    parts = [p.strip() for p in name.split(",") if p.strip()]
    if len(parts) < 2:
        return None
    family, given = parts[0], parts[1]
    if not family or not given:
        return None
    return given, family


def _facet_id(field: str, value: str) -> str:
    """Reconstruct a GtR facet ID: base64("<field>|<value>|string"),
    lower-cased to match how GtR normalises facet values."""
    raw = f"{field}|{value.lower()}|string"
    return base64.b64encode(raw.encode()).decode()


def lookup_director(name: str) -> dict:
    """Best-effort GtR lookup for one director, using exact-match facets
    (not GtR's loose default search) so 'confirmed' only ever means a
    genuinely unique full-name match in GtR's investigator index -- never
    a guess. No auth required, so no 'not configured' state exists here
    unlike orcid.py.

    NOTE: this is a conservative, exact-string match on GtR's stored given
    name -- a director recorded as "Edith Margarethe" on Companies House
    but "Edith" in GtR would come back no_match rather than a false
    confirm. That's intentional: under-matching is preferable to guessing."""
    parsed = _split_name(name)
    if not parsed:
        return {"status": "unparseable_name"}
    given, family = parsed

    surname_facet = _facet_id("surname", family)
    firstname_facet = _facet_id("firstName", given)

    try:
        resp = requests.get(
            SEARCH_URL,
            params={
                "term": f"{given} {family}",
                "fields": "per.fnsn",
                "selectedFacets": f"{surname_facet},{firstname_facet}",
                "page": 1,
            },
            headers={"Accept": "application/json"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        return {"status": "lookup_failed", "error": str(exc)}
    finally:
        time.sleep(REQUEST_DELAY_SECONDS)

    bean = data.get("facetedSearchResultBean", {}) or {}
    total = bean.get("totalResults", 0)
    results = bean.get("results") or []

    if total == 0:
        return {"status": "no_match"}
    if total > 1:
        return {"status": "ambiguous", "candidate_count": total}
    if not results:
        # total == 1 but the result list is empty -- inconsistent
        # response, treat conservatively as no match rather than guessing.
        return {"status": "no_match"}

    hit = results[0]
    person = hit.get("person", {}) or {}
    org = hit.get("organisation", {}) or {}
    return {
        "status": "confirmed",
        "gtr_person_id": person.get("id", ""),
        "organisation": org.get("name", ""),
    }


def enrich_officers(officers: list[dict]) -> list[dict]:
    """Attach a GtR lookup result to each officer dict (returns new dicts,
    doesn't mutate input). Caches lookups by name within this call, same
    reasoning as orcid.py's enrich_officers -- the same person sometimes
    appears twice in one company's officer list (e.g. director + company
    secretary)."""
    if not officers:
        return officers
    cache: dict[str, dict] = {}
    enriched = []
    for o in officers:
        o = dict(o)
        name = o.get("name", "")
        if name not in cache:
            cache[name] = lookup_director(name)
        o["gtr"] = cache[name]
        enriched.append(o)
    return enriched
