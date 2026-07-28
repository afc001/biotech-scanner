"""Optional enrichment: check whether a newly incorporated company already
has a live website, and if so pull a short text excerpt from it.

This exists to address the "Non-Oxide Ceramics Limited" problem: for a
genuinely day-1/day-2 incorporation, Companies House often has nothing
beyond a name, an address, and a director list -- no amount of prompt
tuning fixes an absence of source material, and the model correctly (if
unhelpfully) falls back to "Not observable at this stage" every time. A
live company website, when one already exists, is often the single
highest-value piece of context available (see the manually-enriched Vectis
Biosciences example in demo_briefs.md, where a real product description
took the interest score from 2 to 4).

Deliberately the CHEAPEST, most deterministic form of "search the internet
for more info": guess likely domains from the company name (name.co.uk,
name.com), do a lightweight HTTP GET, and extract the <title> + a short
text excerpt IF the page looks like a real live site rather than a parked-
domain placeholder. No search API, no added Claude tokens for the search
itself -- brand-new shell companies frequently have nothing online yet
regardless of how hard you look, so this helps some briefs a lot and does
nothing for others; that's expected, not a bug.
"""

from __future__ import annotations

import html.parser
import re
import time

import requests

USER_AGENT = "biotech-scanner/1.0 (+https://github.com/afc001/biotech-scanner; company-website-check)"
REQUEST_TIMEOUT = 6
REQUEST_DELAY_SECONDS = 0.3
MAX_EXCERPT_CHARS = 600
MIN_BODY_CHARS = 80  # below this, treat the page as too thin to be real content

# Parked/for-sale domain pages are common noise when guessing at domains --
# if any of these phrases appear, treat the "hit" as not a real company site.
PARKING_MARKERS = [
    "domain is for sale", "buy this domain", "domain may be for sale",
    "this domain is parked", "godaddy.com", "namecheap.com", "sedo.com",
    "this web page is parked", "future home of something quite cool",
    "related searches", "this account has been suspended",
]

COMPANY_SUFFIXES = [" limited", " ltd", " plc", " llp", " lp", " cic"]


class _TextExtractor(html.parser.HTMLParser):
    """Minimal HTML -> text extractor: pulls <title> and visible body text,
    skipping script/style/noscript. Deliberately crude (stdlib only, no new
    dependency) -- this only needs a readable excerpt, not a faithful
    render."""

    def __init__(self):
        super().__init__()
        self.title_parts: list[str] = []
        self.body_parts: list[str] = []
        self._in_title = False
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript") and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._skip_depth:
            return
        text = data.strip()
        if not text:
            return
        (self.title_parts if self._in_title else self.body_parts).append(text)

    @property
    def title(self) -> str:
        return " ".join(self.title_parts).strip()

    @property
    def body_text(self) -> str:
        return " ".join(self.body_parts).strip()


def _candidate_slugs(company_name: str) -> list[str]:
    """Turn a Companies House name into a plausible domain slug: strip the
    corporate suffix, lower-case, drop everything that isn't alphanumeric.
    Best-effort -- a wrong guess just means not_found later, no harm done."""
    name = company_name.lower()
    for suffix in COMPANY_SUFFIXES:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    slug = re.sub(r"[^a-z0-9]", "", name)
    return [slug] if slug else []


def _candidate_urls(company_name: str) -> list[str]:
    return [
        f"https://{slug}.{tld}"
        for slug in _candidate_slugs(company_name)
        for tld in ("co.uk", "com")
    ]


def _looks_like_parking_page(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in PARKING_MARKERS)


def check_company_website(company_name: str) -> dict:
    """Best-effort check for a live company website. Tries a small set of
    guessed domains (name.co.uk, then name.com); the first one that
    responds with a real-looking page (not a parking/for-sale placeholder,
    not too thin to be meaningful content) wins. Returns a dict, never
    raises -- a DNS failure, timeout, or SSL error just means "not_found",
    exactly like a genuine absence of a website."""
    if not company_name:
        return {"status": "not_found"}

    for url in _candidate_urls(company_name):
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": USER_AGENT},
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
            )
        except requests.RequestException:
            time.sleep(REQUEST_DELAY_SECONDS)
            continue
        time.sleep(REQUEST_DELAY_SECONDS)

        if resp.status_code != 200:
            continue

        parser = _TextExtractor()
        try:
            parser.feed(resp.text)
        except Exception:
            continue

        body = parser.body_text
        if len(body) < MIN_BODY_CHARS:
            continue  # too thin to be real content -- likely a placeholder
        if _looks_like_parking_page(resp.text) or _looks_like_parking_page(body):
            continue

        return {
            "status": "found",
            "url": resp.url,
            "title": parser.title[:200],
            "excerpt": body[:MAX_EXCERPT_CHARS],
        }

    return {"status": "not_found"}
