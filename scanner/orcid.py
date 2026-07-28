"""Optional enrichment: check company directors against ORCID's public
registry to surface real academic affiliation signal, instead of relying
on the model to guess credibility from a bare name + "Dr." title on a
Companies House filing.

IMPORTANT: ORCID's search API -- even the free public tier -- requires
OAuth client credentials. There is no fully open/unauthenticated search
endpoint. To use this module:

  1. Sign in (or register) at https://orcid.org
  2. Register a free Public API client -- see
     https://info.orcid.org/documentation/integration-guide/registering-a-public-api-client/
  3. Set ORCID_CLIENT_ID and ORCID_CLIENT_SECRET in your environment / .env

Without those two values this module quietly no-ops everywhere it's
called -- ORCID enrichment is an enhancement, not a requirement, so its
absence must never break a run.

Also worth checking before relying on this in production: ORCID's Public
API Terms of Service (https://info.orcid.org/public-client-terms-of-service/)
for anything relevant to commercial/for-profit use -- not legal advice,
just worth a read given this is a fund's internal tool.
"""

from __future__ import annotations

import time

import requests

from . import config

TOKEN_URL = "https://orcid.org/oauth/token"
SEARCH_URL = "https://pub.orcid.org/v3.0/expanded-search/"
REQUEST_DELAY_SECONDS = 0.3  # be a polite public-API citizen

_cached_token: str | None = None


def enabled() -> bool:
    return bool(config.ORCID_CLIENT_ID and config.ORCID_CLIENT_SECRET)


def get_access_token() -> str | None:
    """Fetch (and cache for the life of this process) a /read-public OAuth
    token via the client-credentials flow. Returns None if not configured
    or if the token request fails -- callers must treat that as
    "enrichment unavailable", never as a fatal error. ORCID says this
    token is long-lived (~20 years), so one fetch per run is plenty."""
    global _cached_token
    if _cached_token:
        return _cached_token
    if not enabled():
        return None
    try:
        resp = requests.post(
            TOKEN_URL,
            data={
                "client_id": config.ORCID_CLIENT_ID,
                "client_secret": config.ORCID_CLIENT_SECRET,
                "grant_type": "client_credentials",
                "scope": "/read-public",
            },
            headers={"Accept": "application/json"},
            timeout=15,
        )
        resp.raise_for_status()
        _cached_token = resp.json().get("access_token")
        return _cached_token
    except requests.RequestException as exc:
        print(f"  ORCID: could not obtain access token, skipping enrichment ({exc})")
        return None


def _split_name(name: str) -> tuple[str, str] | None:
    """Companies House officer names come as 'SURNAME, Given Names[, Title]'
    (e.g. 'HESSEL, Edith Margarethe, Dr'). Returns (given_names, family_name),
    or None if the name can't be confidently split."""
    parts = [p.strip() for p in name.split(",") if p.strip()]
    if len(parts) < 2:
        return None
    family, given = parts[0], parts[1]
    if not family or not given:
        return None
    return given, family


def lookup_director(name: str, token: str) -> dict:
    """Best-effort ORCID lookup for one director. Only ever reports a
    'confirmed' match when the name search returns exactly one hit --
    ambiguous (common-name) matches are reported as such rather than
    guessed, consistent with the project's "never invent facts" rule."""
    parsed = _split_name(name)
    if not parsed:
        return {"status": "unparseable_name"}
    given, family = parsed

    query = f'given-names:"{given}" AND family-name:"{family}"'
    try:
        resp = requests.get(
            SEARCH_URL,
            params={"q": query},
            headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        return {"status": "lookup_failed", "error": str(exc)}
    finally:
        time.sleep(REQUEST_DELAY_SECONDS)

    num_found = data.get("num-found", 0)
    results = data.get("expanded-result") or []
    if num_found == 0:
        return {"status": "no_match"}
    if num_found > 1:
        return {"status": "ambiguous", "candidate_count": num_found}
    if not results:
        # num_found == 1 but the result list is empty -- inconsistent
        # response, treat conservatively as no match rather than guessing.
        return {"status": "no_match"}

    hit = results[0]
    return {
        "status": "confirmed",
        "orcid_id": hit.get("orcid-id", ""),
        "institutions": hit.get("institution-name", []) or [],
    }


def enrich_officers(officers: list[dict]) -> list[dict]:
    """Attach an ORCID lookup result to each officer dict (returns new
    dicts, doesn't mutate input). No-ops and returns officers unchanged if
    ORCID isn't configured or a token can't be obtained -- enrichment
    failures must never break the pipeline.

    Caches lookups by name within this call: the same person sometimes
    appears twice in one company's officer list (e.g. director + company
    secretary), and there's no reason to fire two identical ORCID searches
    for that."""
    if not officers or not enabled():
        return officers
    token = get_access_token()
    if not token:
        return officers
    cache: dict[str, dict] = {}
    enriched = []
    for o in officers:
        o = dict(o)
        name = o.get("name", "")
        if name not in cache:
            cache[name] = lookup_director(name, token)
        o["orcid"] = cache[name]
        enriched.append(o)
    return enriched
