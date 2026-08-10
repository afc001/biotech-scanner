"""Render JSON briefs into dated markdown + HTML digests.

Generation (JSON) is deliberately separate from presentation (this module),
so the whole archive can be re-rendered at any time — restyle, fix a typo,
change ordering — without re-running the model.
"""

from __future__ import annotations

import html
import json
import re
from datetime import date

from . import config

HTML_HEAD = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  body {{ font-family:"Times New Roman",Times,serif; color:#1a1a1a; background:#fff;
         margin:0; padding:48px 20px 80px; line-height:1.55; font-size:18px; }}
  .page {{ max-width:740px; margin:0 auto; }}
  h1 {{ font-size:30px; margin:0 0 6px; }}
  .meta {{ color:#555; font-size:15px; margin:0 0 22px; }}
  hr {{ border:none; border-top:1px solid #ddd; margin:34px 0; }}
  h2 {{ font-size:23px; margin:0 0 4px; padding-bottom:6px; border-bottom:2px solid #222;
        display:flex; justify-content:space-between; align-items:baseline; gap:12px; }}
  .badge {{ font-size:15px; font-weight:normal; white-space:nowrap; color:#333; }}
  .pill {{ font-size:13px; font-weight:600; padding:2px 9px; border-radius:10px;
           margin-left:5px; white-space:nowrap; cursor:default; }}
  .pill-confirmed {{ background:#d7f0d9; color:#1a5c22; }}
  .pill-ambiguous {{ background:#fff2cc; color:#7a5c00; }}
  .pill-search {{ background:#dce7f7; color:#1a3e6e; }}
  .sources {{ color:#777; font-size:14px; margin:10px 0 0; }}
  .sources a {{ color:#555; }}
  .legend {{ color:#777; font-size:13px; margin:0 0 22px; }}
  .legend summary {{ cursor:pointer; font-weight:bold; }}
  .legend p {{ margin:8px 0 0; }}
  .oneliner {{ font-style:italic; font-size:18px; margin:12px 0 16px; color:#222; }}
  .field {{ margin:9px 0; }} .field .label {{ font-weight:bold; }}
  .flags-title {{ font-weight:bold; margin:16px 0 4px; }}
  ul {{ margin:4px 0 10px; padding-left:24px; }} li {{ margin:3px 0; }}
  a {{ color:#1a3e6e; }}
  .toolbar {{ display:flex; align-items:center; flex-wrap:wrap; gap:14px; font-size:15px;
              font-family:Arial,Helvetica,sans-serif; margin:0 0 22px; padding:12px 14px;
              background:#f6f6f4; border:1px solid #e2e2df; border-radius:6px; }}
  .toolbar label {{ display:flex; align-items:center; gap:6px; white-space:nowrap; }}
  .toolbar select {{ font-size:15px; padding:2px 4px; }}
  .result-count {{ margin-left:auto; color:#777; font-size:13px; font-family:Arial,Helvetica,sans-serif; }}
  .company {{ border-top:1px solid #ddd; padding-top:28px; margin-top:28px; }}
  .week-header {{ font-size:17px; font-weight:bold; margin:0 0 6px; color:#333;
                   border-bottom:1px solid #ccc; padding-bottom:6px;
                   font-family:Arial,Helvetica,sans-serif; }}
  .archive-summary {{ color:#777; font-size:15px; }}
</style></head><body><div class="page">
"""

TOOLBAR_HTML = """<div class="toolbar">
  <label>Minimum score:
    <select id="scoreFilter">
      <option value="0">All</option>
      <option value="2">2+</option>
      <option value="3">3+</option>
      <option value="4">4+</option>
      <option value="5">5 only</option>
    </select>
  </label>
  <label><input type="checkbox" id="groupByWeek"> Group by incorporation week</label>
  <span class="result-count" id="resultCount"></span>
</div>
<script>
(function () {
  var page = document.querySelector('.page');
  var cards = Array.prototype.slice.call(document.querySelectorAll('.company'));
  var originalOrder = cards.slice();
  var scoreFilter = document.getElementById('scoreFilter');
  var groupByWeek = document.getElementById('groupByWeek');
  var resultCount = document.getElementById('resultCount');
  if (!cards.length) { return; }

  function isoWeekLabel(dateStr) {
    if (!dateStr) { return 'Unknown incorporation date'; }
    var d = new Date(dateStr + 'T00:00:00Z');
    if (isNaN(d.getTime())) { return 'Unknown incorporation date'; }
    var dayIndex = (d.getUTCDay() + 6) % 7; // 0 = Monday
    var monday = new Date(d);
    monday.setUTCDate(d.getUTCDate() - dayIndex);
    var sunday = new Date(monday);
    sunday.setUTCDate(monday.getUTCDate() + 6);
    var fmt = function (x) { return x.toISOString().slice(0, 10); };
    return 'Week of ' + fmt(monday) + ' to ' + fmt(sunday);
  }

  function clearWeekHeaders() {
    Array.prototype.slice.call(document.querySelectorAll('.week-header'))
      .forEach(function (h) { h.remove(); });
  }

  function render() {
    var minScore = parseInt(scoreFilter.value, 10) || 0;
    clearWeekHeaders();

    var order;
    if (groupByWeek.checked) {
      order = originalOrder.slice().sort(function (a, b) {
        return (a.dataset.incorporated || '').localeCompare(b.dataset.incorporated || '');
      });
    } else {
      order = originalOrder;
    }
    order.forEach(function (card) { page.appendChild(card); });

    if (groupByWeek.checked) {
      var lastWeek = null;
      order.forEach(function (card) {
        var week = isoWeekLabel(card.dataset.incorporated);
        if (week !== lastWeek) {
          var header = document.createElement('div');
          header.className = 'week-header';
          header.textContent = week;
          page.insertBefore(header, card);
          lastWeek = week;
        }
      });
    }

    var visible = 0;
    cards.forEach(function (card) {
      var score = parseInt(card.dataset.score, 10) || 0;
      var show = score >= minScore;
      card.style.display = show ? '' : 'none';
      if (show) { visible++; }
    });
    resultCount.textContent = visible + ' of ' + cards.length + ' shown';
  }

  scoreFilter.addEventListener('change', render);
  groupByWeek.addEventListener('change', render);
  render();
})();
</script>
"""

HTML_FOOT = """</div></body></html>"""


def score_int(interest_score: str) -> int:
    """Pull the leading integer out of an interest_score string like '4 — ...'."""
    m = re.match(r"\s*(\d)", str(interest_score))
    return int(m.group(1)) if m else 0


def _esc(s: str) -> str:
    return html.escape(str(s))


def _badge_html(source_label: str, result: dict | None) -> str:
    """Render one visible pill badge for an ORCID/GtR result, or '' if there's
    nothing worth showing. Mirrors generate._format_orcid()/_format_gtr()'s
    severity tiering: confirmed gets a green tick, a few-candidates ambiguous
    match gets an amber '?', everything weaker is silent (no badge) rather
    than cluttering the page with low-confidence noise."""
    if not result:
        return ""
    status = result.get("status")
    if status == "confirmed":
        detail = result.get("institutions") if source_label == "ORCID" else None
        detail_text = ", ".join(detail) if detail else result.get("organisation", "")
        title = f"{source_label} confirmed" + (f" — {detail_text}" if detail_text else "")
        return f'<span class="pill pill-confirmed" title="{_esc(title)}">{source_label} &#10003;</span>'
    if status == "ambiguous":
        count = result.get("candidate_count")
        if not (isinstance(count, int) and count <= 10):
            return ""  # too common to be worth a badge -- same cutoff as _format_orcid/_format_gtr
        title = f"{source_label}: {count} possible matches, unverified"
        return f'<span class="pill pill-ambiguous" title="{_esc(title)}">{source_label}?</span>'
    return ""


def _badge_md(source_label: str, result: dict | None) -> str:
    """Plain-text equivalent of _badge_html() for the markdown digest."""
    if not result:
        return ""
    status = result.get("status")
    if status == "confirmed":
        return f" [{source_label} ✓]"
    if status == "ambiguous":
        count = result.get("candidate_count")
        if not (isinstance(count, int) and count <= 10):
            return ""
        return f" [{source_label}?]"
    return ""


def _repeat_founder_badge_html(rf_result: dict | None) -> str:
    """Pill badge for a confirmed same-sector repeat-founder match, mirroring
    _badge_html()'s green-tick styling. Silent for every other status --
    same silence-over-clutter philosophy as the ORCID/GtR badges."""
    if not rf_result or rf_result.get("status") != "same_sector_confirmed":
        return ""
    matches = rf_result.get("same_sector_matches") or []
    names = ", ".join(m.get("company_name", "") for m in matches) or "another company in this sector"
    title = f"Repeat founder (same sector) — {names}"
    return f'<span class="pill pill-confirmed" title="{_esc(title)}">Repeat Founder &#10003;</span>'


def _repeat_founder_badge_md(rf_result: dict | None) -> str:
    if not rf_result or rf_result.get("status") != "same_sector_confirmed":
        return ""
    return " [Repeat Founder ✓]"


def _advisor_pattern_badge_html(b: dict) -> str:
    """Pill badge for the advisor/portfolio-pattern signal (a director with
    several other active directorships at once). Amber, like the
    ambiguous-match badges, since it's an explicitly two-sided signal, not
    a simple positive."""
    if not b.get("advisor_pattern"):
        return ""
    return ('<span class="pill pill-ambiguous" '
            'title="Director(s) hold several other active directorships">Advisor Pattern</span>')


def _advisor_pattern_badge_md(b: dict) -> str:
    return " [Advisor Pattern]" if b.get("advisor_pattern") else ""


def _search_badge_html(b: dict) -> str:
    """Small pill marking a brief that went through the gated web-search
    enrichment pass (see generate.enrich_with_web_search / WEB_SEARCH_SPEC.md).
    Distinguishes "we looked further and this is what we found" from a
    pass-1-only brief, same silence-over-clutter philosophy as the ORCID/GtR
    badges -- only appears when search actually ran."""
    if not b.get("search_enriched"):
        return ""
    return '<span class="pill pill-search" title="Web-search enriched">&#128269; Web-verified</span>'


def _sources_html(b: dict) -> str:
    sources = b.get("search_sources") or []
    if not sources:
        return ""
    links = ", ".join(f'<a href="{_esc(u)}">{_esc(u)}</a>' for u in sources)
    return f'<p class="sources">Sources: {links}</p>'


def _sources_md(b: dict) -> str:
    sources = b.get("search_sources") or []
    if not sources:
        return ""
    return "Sources: " + ", ".join(sources)


def _company_html(b: dict) -> str:
    s = score_int(b.get("interest_score", ""))
    badges_html = (_badge_html("ORCID", b.get("orcid_badge")) + _badge_html("GtR", b.get("gtr_badge"))
                   + _repeat_founder_badge_html(b.get("repeat_founder_badge"))
                   + _advisor_pattern_badge_html(b) + _search_badge_html(b))
    incorporated = _esc(b.get("incorporated", ""))
    # data-score/data-incorporated feed the toolbar's client-side score
    # filter and "group by week" toggle (see TOOLBAR_HTML) -- purely a
    # display-layer concern, no effect on the underlying brief JSON.
    # id lets sql_explorer.html deep-link a query result straight to this
    # company's brief (digests/{date}.html#company-{company_number}).
    company_id = _esc(b.get("company_number", ""))
    parts = [f'<div class="company" id="company-{company_id}" data-score="{s}" data-incorporated="{incorporated}">',
             f'<h2>{_esc(b["company_name"])} <span class="badge">Interest {s} / 5</span>{badges_html}</h2>',
             f'<p class="oneliner">{_esc(b.get("one_liner", ""))}</p>',
             f'<p class="field"><span class="label">Incorporated:</span> {_esc(b.get("incorporated",""))} '
             f'&middot; <span class="label">SIC:</span> {_esc(", ".join(b.get("sic_codes", [])) or "—")}</p>']
    for label, key in [("Science", "science"), ("Stage", "stage_signal"),
                       ("Team provenance", "team_provenance"),
                       ("Location signal", "location_signal"), ("Funding", "funding")]:
        parts.append(f'<p class="field"><span class="label">{label}.</span> {_esc(b.get(key,""))}</p>')
    for title, key in [("Positive flags", "flags_positive"), ("Negative flags", "flags_negative")]:
        items = b.get(key, []) or []
        parts.append(f'<p class="flags-title">{title}</p><ul>' +
                     "".join(f"<li>{_esc(i)}</li>" for i in items) + "</ul>")
    justification = re.sub(r"^\s*\d\s*[—-]?\s*", "", str(b.get("interest_score", "")))
    parts.append(f'<p class="field"><span class="label">Interest score: {s}</span> — '
                 f'{_esc(justification)}</p>')
    unknowns = b.get("unknowns", []) or []
    if unknowns:
        # Below-threshold briefs deliberately get an empty unknowns list
        # (see generate.load_prompt()'s cost-control rule) -- omit the
        # heading entirely rather than showing it followed by nothing.
        parts.append('<p class="flags-title">Open questions for a first call</p><ul>' +
                     "".join(f"<li>{_esc(u)}</li>" for u in unknowns) + "</ul>")
    parts.append(_sources_html(b))
    parts.append('</div>')
    return "\n".join(parts)


def _search_badge_md(b: dict) -> str:
    return " [\U0001F50D Web-verified]" if b.get("search_enriched") else ""


def _company_md(b: dict) -> str:
    s = score_int(b.get("interest_score", ""))
    badges_md = (_badge_md("ORCID", b.get("orcid_badge")) + _badge_md("GtR", b.get("gtr_badge"))
                 + _repeat_founder_badge_md(b.get("repeat_founder_badge"))
                 + _advisor_pattern_badge_md(b) + _search_badge_md(b))
    lines = [f'## {b["company_name"]} — Interest {s} / 5{badges_md}', "",
             f'> {b.get("one_liner", "")}', "",
             f'**Incorporated:** {b.get("incorporated","")} · **SIC:** {", ".join(b.get("sic_codes", [])) or "—"}', ""]
    for label, key in [("Science", "science"), ("Stage", "stage_signal"),
                       ("Team provenance", "team_provenance"),
                       ("Location signal", "location_signal"), ("Funding", "funding")]:
        lines += [f'**{label}.** {b.get(key,"")}', ""]
    for title, key in [("Positive flags", "flags_positive"), ("Negative flags", "flags_negative")]:
        lines.append(f'**{title}**'); lines.append("")
        lines += [f'- {i}' for i in (b.get(key, []) or [])]; lines.append("")
    justification = re.sub(r"^\s*\d\s*[—-]?\s*", "", str(b.get("interest_score", "")))
    lines += [f'**Interest score: {s}** — {justification}', ""]
    unknowns = b.get("unknowns", []) or []
    if unknowns:
        lines.append("**Open questions for a first call**"); lines.append("")
        lines += [f'- {u}' for u in unknowns]; lines.append("")
    sources_line = _sources_md(b)
    if sources_line:
        lines += [sources_line, ""]
    return "\n".join(lines)


def render_digest(briefs: list[dict], run_date: date | None = None) -> dict:
    """Write dated .md and .html digests, rebuild the archive index. Returns paths."""
    run_date = run_date or date.today()
    briefs = sorted(briefs, key=lambda b: score_int(b.get("interest_score", "")), reverse=True)
    config.DIGESTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = run_date.isoformat()
    title = f"Biotech Scanner Digest — {stamp}"
    meta = (f"Generated {stamp} · {len(briefs)} companies · "
            f"Source: Companies House new incorporations (SIC {', '.join(config.SIC_CODES)})")

    # HTML
    html_body = HTML_HEAD.format(title=_esc(title))
    legend = ('ORCID ✓ / GtR ✓ = confirmed unique match (hover for detail) · '
              'ORCID? / GtR? = possible match, unverified — common name, worth a manual check · '
              'Repeat Founder ✓ = confirmed prior/current directorship at another company in this '
              'sector (hover for detail) · Advisor Pattern = director holds several other active '
              'directorships — well-connected, but likely not day-to-day operational here · '
              '🔍 Web-verified = re-checked with a live web search (see Sources on that brief)')
    html_body += (f'<h1>{_esc(title)}</h1><p class="meta">{_esc(meta)}</p>'
                  f'<details class="legend"><summary>Badge legend</summary>'
                  f'<p>{_esc(legend)}</p></details><hr>')
    if briefs:
        html_body += TOOLBAR_HTML
        html_body += "".join(_company_html(b) for b in briefs)
    else:
        html_body += "<p>No new companies today.</p>"
    html_body += HTML_FOOT
    html_path = config.DIGESTS_DIR / f"{stamp}.html"
    html_path.write_text(html_body)

    # Markdown
    md = [f"# {title}", "", meta, "", "---", ""]
    md += ["\n---\n\n".join(_company_md(b) for b in briefs) if briefs else "_No new companies today._"]
    md_path = config.DIGESTS_DIR / f"{stamp}.md"
    md_path.write_text("\n".join(md))

    _rebuild_index()
    return {"html": str(html_path), "md": str(md_path)}


def _rebuild_index() -> None:
    """Regenerate digests/index.html listing every dated digest, newest
    first, each annotated with company count and top score (read from the
    already-saved JSON, no API calls) so a visitor can tell which days are
    worth opening before clicking into any of them."""
    digests = sorted((p.stem for p in config.DIGESTS_DIR.glob("*.html") if p.stem != "index"),
                     reverse=True)
    body = HTML_HEAD.format(title="Biotech Scanner — Digest Archive")
    body += '<h1>Biotech Scanner — Digest Archive</h1>'
    body += '<p class="meta">Daily automated screening digests of newly incorporated UK biotech companies.</p>'
    body += '<p><a href="../index.html">&larr; Project home</a> &middot; <a href="../sql_explorer.html">Run SQL against the live database &rarr;</a></p><hr><ul>'
    items = []
    for d in digests:
        briefs_path = config.BRIEFS_DIR / f"{d}.json"
        n = 0
        top = 0
        if briefs_path.exists():
            briefs = json.loads(briefs_path.read_text())
            n = len(briefs)
            top = max((score_int(b.get("interest_score", "")) for b in briefs), default=0)
        summary = f"{n} compan{'y' if n == 1 else 'ies'}, top score {top}/5" if n else "0 companies"
        items.append(f'<li><a href="{d}.html">{d}</a> <span class="archive-summary">— {_esc(summary)}</span></li>')
    body += "".join(items)
    body += "</ul>" + HTML_FOOT
    (config.DIGESTS_DIR / "index.html").write_text(body)


def save_raw_briefs(briefs: list[dict], run_date: date | None = None) -> str:
    run_date = run_date or date.today()
    config.BRIEFS_DIR.mkdir(parents=True, exist_ok=True)
    path = config.BRIEFS_DIR / f"{run_date.isoformat()}.json"
    path.write_text(json.dumps(briefs, indent=2, ensure_ascii=False))
    return str(path)
