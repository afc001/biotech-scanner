"""One-off utility: re-render a digest page from its already-saved raw JSON
briefs, WITHOUT calling the Claude API again. No cost, no network calls at
all -- this only re-runs render.py's presentation layer against data already
sitting in data/briefs/*.json.

Use this any time render.py's HTML changes (new CSS, the score-filter /
week-grouping toolbar, a typo fix) and you want the existing archive to pick
up the improvement for free. This is exactly the separation render.py's own
docstring promises: "Generation (JSON) is deliberately separate from
presentation... so the whole archive can be re-rendered at any time... without
re-running the model."

NOTE: this only refreshes the HTML/markdown. It does NOT change anything
inside the brief JSON itself (score, flags, one_liner, etc.) -- those were
baked in by whatever prompt was live at generation time. If you want a brief
re-SCORED under a newer prompt (e.g. the ORCID/GtR scoring rule added later),
that requires an actual regeneration (new Claude API calls), not a re-render.

Usage:
    python rerender_digest.py --date 2026-08-11    # one date
    python rerender_digest.py --all                # every saved date
"""
from __future__ import annotations

import argparse
import json
from datetime import date

from scanner import config, render


def rerender_one(target_date: str) -> None:
    path = config.BRIEFS_DIR / f"{target_date}.json"
    if not path.exists():
        print(f"  skip {target_date}: no raw briefs file at {path}")
        return
    briefs = json.loads(path.read_text())
    y, m, d = (int(x) for x in target_date.split("-"))
    paths = render.render_digest(briefs, run_date=date(y, m, d))
    print(f"  re-rendered {target_date}: {len(briefs)} companies -> {paths['html']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--date", help="YYYY-MM-DD, re-render just this one digest")
    group.add_argument("--all", action="store_true", help="re-render every saved date")
    args = parser.parse_args()

    if args.all:
        dates = sorted(p.stem for p in config.BRIEFS_DIR.glob("*.json"))
        print(f"Re-rendering {len(dates)} saved digest(s), no API calls made...")
        for d in dates:
            rerender_one(d)
    else:
        rerender_one(args.date)

    print("\nDone. No Anthropic API calls were made -- this only re-ran the presentation layer.")
