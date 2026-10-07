"""Builds reports/hindi_audit_2026-10/inventory.csv from inventory_before.json + inventory_after.json.

One row per (key, variant). Columns: key, variant, file, screen, section, kind, where_appears,
english, hindi_before, hindi_after, chars_before, chars_after, has_figure_placeholder, changed,
had_hindi_before.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "reports" / "hindi_audit_2026-10"

WHERE = {
    "aria-label": "screen-reader label (aria-label), main page",
    "heading": "section heading",
    "label": "button/label/card text",
    "short label": "short label/chip",
    "banner/message": "status banner or error message",
    "sentence": "sentence in a card/banner/panel",
}


def main() -> None:
    before = {
        r["key"]: r for r in json.loads((OUT / "inventory_before.json").read_text(encoding="utf-8"))
    }
    after = json.loads((OUT / "inventory_after.json").read_text(encoding="utf-8"))
    rows = []
    for r in after:
        b = before.get(r["key"])
        for hv in r["hi_variants"]:
            en = next(
                (v["text"] for v in r["en_variants"] if v["variant"] == hv["variant"]),
                r["en_variants"][0]["text"],
            )
            old = ""
            if b and b["has_hindi"]:
                old = next(
                    (v["text"] for v in b["hi_variants"] if v["variant"] == hv["variant"]), ""
                )
            where = WHERE.get(r["kind"], r["kind"])
            if r["file"] == "how-we-know-strings.js":
                where = "how-we-know.html: " + where
            rows.append(
                {
                    "key": r["key"],
                    "variant": hv["variant"],
                    "file": r["file"],
                    "screen": r["screen"],
                    "section": r["section"],
                    "kind": r["kind"],
                    "where_appears": where,
                    "english": en,
                    "hindi_before": old,
                    "hindi_after": hv["text"],
                    "chars_before": len(old),
                    "chars_after": len(hv["text"]),
                    "has_figure_placeholder": "{" in hv["text"] or "{" in en,
                    "changed": old != hv["text"],
                    "had_hindi_before": bool(old),
                }
            )
    with (OUT / "inventory.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(
        "rows",
        len(rows),
        "changed",
        sum(r["changed"] for r in rows),
        "new (no hindi before)",
        sum(not r["had_hindi_before"] for r in rows),
        "with placeholder",
        sum(r["has_figure_placeholder"] for r in rows),
    )


if __name__ == "__main__":
    main()
