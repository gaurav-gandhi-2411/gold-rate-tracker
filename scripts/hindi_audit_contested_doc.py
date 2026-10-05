"""Writes docs/HINDI_CONTESTED.md from reports/hindi_audit_2026-10/contested.json + inventory_before.json.

The `ASSESSMENT` column is a human (author) reading of why the judges flagged the string; it is not
a judge output. Strings are listed only when a MAJORITY of the calibrated judges flagged them
(any criterion scored <= 3); strings flagged by a single judge are counted in the PR body only.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "reports" / "hindi_audit_2026-10"

FRAGMENT = "judge saw a bare fragment without the sentence it is inserted into"
GLOSS = "judge prompt omitted the glossary's own exception (bill-line 'gold value' is सोने की कीमत, not भाव)"
WORDING = "genuine wording choice, worth a native glance"
ASSESSMENT = {
    "trendDirUp": FRAGMENT + " (inserted after 'ट्रेंड:' in the chart aria-label)",
    "trendDirDown": FRAGMENT,
    "dirWordUnchanged": FRAGMENT + " (idiom जस का तस = 'as it was')",
    "noChangeLabel": FRAGMENT,
    "thDelta": FRAGMENT + " (table column header)",
    "thWhen": FRAGMENT + " (table column header)",
    "rangeAll": FRAGMENT + " (chart range button)",
    "calcPresetCustom": FRAGMENT + " (a radio option: अपना = 'your own')",
    "bottomNavAriaLabel": FRAGMENT,
    "appTitle": "brand name, intentionally unchanged (same in English)",
    "methAssumeNoChange": FRAGMENT,
    "methRangeStrFallback": FRAGMENT + " (slots into 'हमारी रेंज (...) ...')",
    "ratioWatch": FRAGMENT,
    "freshnessOkAria": FRAGMENT + " ({rel} is a relative time like '2 घंटे पहले')",
    "methRangeSub": "'लगभग 10 में से N बार' is the fixed form used site-wide for this claim",
    "calcRowGoldValue": GLOSS,
    "calcMakingModePct": GLOSS,
    "calcPresetCoinsRange": GLOSS,
    "calcPresetPlainRange": GLOSS,
    "calcPresetIntricateRange": GLOSS,
    "calcEmptyState": WORDING + " (ख़र्च vs कीमत for 'cost')",
    "calcAriaLabel": WORDING + " (ख़र्च vs कीमत for 'cost')",
    "calcPresetCustomHint": WORDING
    + " (English says 'exact rate'; the field is the making charge)",
    "ratioRetrainSub": WORDING
    + " ('may need recalibration' has no everyday Hindi; chose इसे फिर से ठीक करना)",
    "karatToggleAriaLabel": WORDING + " (शुद्धता = purity)",
    "methAccuracyDrift": WORDING + " (technical-page label)",
    "todaysReadEyebrow": WORDING + " ('Today's read' -> आज का हाल)",
    "pv2RangeOddsClause": WORDING + " (flagged OFF page_v2 only)",
    "driverPartGoldTookOff": "judge claimed the verb should be बढ़े; it is घटे on purpose ('took off' = reduced), so the flag is wrong",
    "driverPartRupeeTookOff": "same as driverPartGoldTookOff",
}


def main() -> None:
    contested = json.loads((OUT / "contested.json").read_text(encoding="utf-8"))
    before = {
        r["key"]: r for r in json.loads((OUT / "inventory_before.json").read_text(encoding="utf-8"))
    }

    def old_hi(key: str, variant: str) -> str:
        b = before.get(key)
        if not b or not b["has_hindi"]:
            return "(none: showed English)"
        v = next((x for x in b["hi_variants"] if x["variant"] == variant), None)
        return v["text"] if v else "(none)"

    lines = [
        "# Hindi strings the judges contested (optional review list for GG)",
        "",
        "LLM consensus, not native review. Three local judges (gemma2:9b, llama3.1:8b, qwen3:30b-a3b) rated each new",
        "string blind; a string is listed here only when a MAJORITY of them scored at least one criterion 3 or lower",
        f"({len(contested)} of 353 new strings). Judge-pair agreement is modest (kappa 0.12-0.44, see",
        "`reports/hindi_audit_2026-10/summary_stats.json` and the PR body), so most entries are judge noise or a judge lacking context; the",
        "last column is the author's reading, not a judge output. Everything else (323 strings) was not contested.",
        "A native speaker is still the real review; this list is only where to look first.",
        "",
        "| Key | English | Old Hindi | New Hindi | Contested because |",
        "|---|---|---|---|---|",
    ]
    for r in contested:
        why = ASSESSMENT.get(
            r["key"], "judges disagree with the glossary or each other; no author note"
        )
        cell = lambda t: t.replace("|", "\\|").replace("\n", " ")  # noqa: E731
        lines.append(
            f"| `{r['key']}` | {cell(r['en'])} | {cell(old_hi(r['key'], r['variant']))} | {cell(r['hi_new'])} | "
            f"{r['why']}; {why} |"
        )
    (ROOT / "docs" / "HINDI_CONTESTED.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("rows", len(contested))


if __name__ == "__main__":
    main()
