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
  .legend {{ color:#777; font-size:13px; margin:0 0 22px; }}
  .oneliner {{ font-style:italic; font-size:18px; margin:12px 0 16px; color:#222; }}
  .field {{ margin:9px 0; }} .field .label {{ font-weight:bold; }}
  .flags-title {{ font-weight:bold; margin:16px 0 4px; }}
  ul {{ margin:4px 0 10px; padding-left:24px; }} li {{ margin:3px 0; }}
  a {{ color:#1a3e6e; }}
</style></head><body><div class="page">
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


def _company_html(b: dict) -> str:
    s = score_int(b.get("interest_score", ""))
    badges_html = _badge_html("ORCID", b.get("orcid_badge")) + _badge_html("GtR", b.get("gtr_badge"))
    parts = [f'<div class="company">',
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
    parts.append('<p class="flags-title">Open questions for a first call</p><ul>' +
                 "".join(f"<li>{_esc(u)}</li>" for u in unknowns) + "</ul></div>")
    return "\n".join(parts)


def _company_md(b: dict) -> str:
    s = score_int(b.get("interest_score", ""))
    badges_md = _badge_md("ORCID", b.get("orcid_badge")) + _badge_md("GtR", b.get("gtr_badge"))
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
    lines.append("**Open questions for a first call**"); lines.append("")
    lines += [f'- {u}' for u in (b.get("unknowns", []) or [])]; lines.append("")
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
              'ORCID? / GtR? = possible match, unverified — common name, worth a manual check')
    html_body += (f'<h1>{_esc(title)}</h1><p class="meta">{_esc(meta)}</p>'
                  f'<p class="legend">{_esc(legend)}</p><hr>')
    html_body += "<hr>".join(_company_html(b) for b in briefs) if briefs else "<p>No new companies today.</p>"
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
    """Regenerate digests/index.html listing every dated digest, newest first."""
    digests = sorted((p.stem for p in config.DIGESTS_DIR.glob("*.html") if p.stem != "index"),
                     reverse=True)
    body = HTML_HEAD.format(title="Biotech Scanner — Digest Archive")
    body += '<h1>Biotech Scanner — Digest Archive</h1>'
    body += '<p class="meta">Daily automated screening digests of newly incorporated UK biotech companies.</p><hr><ul>'
    body += "".join(f'<li><a href="{d}.html">{d}</a></li>' for d in digests)
    body += "</ul>" + HTML_FOOT
    (config.DIGESTS_DIR / "index.html").write_text(body)


def save_raw_briefs(briefs: list[dict], run_date: date | None = None) -> str:
    run_date = run_date or date.today()
    config.BRIEFS_DIR.mkdir(parents=True, exist_ok=True)
    path = config.BRIEFS_DIR / f"{run_date.isoformat()}.json"
    path.write_text(json.dumps(briefs, indent=2, ensure_ascii=False))
    return str(path)
