"""Blind LLM judging of the site's Hindi strings (reports/hindi_audit_2026-10/).

LLM consensus, NOT native review. Zero spend: local Ollama models only, temperature 0, seed 42.

Subcommands
  build      inventory_before.json + inventory_after.json -> items.json (shuffled, author-blind)
  calibrate  run each judge on constructed cases with known answers -> calibration.json
  judge      run one judge over items.json -> judgments_<tag>.jsonl (resumable)
  analyze    judgments + calibration -> agreement stats, old-vs-new, contested list

The judge sees only: English source, candidate Hindi, context (kind, screen) and the glossary.
It is never told whether a candidate is an old or a new string, nor who wrote it.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "reports" / "hindi_audit_2026-10"
CRITERIA = ("natural", "correct", "glossary", "grammar")
PASS_MIN = 4  # 1-5 scale; >= 4 counts as pass
SEED = 42

# Compact glossary shown to every judge (the authoritative copy is docs/HINDI_GLOSSARY.md).
GLOSSARY = """\
gold rate / gold price today -> सोने का भाव   (NOT सोने की कीमत / स्वर्ण मूल्य / सोने की दर)
estimate / estimated -> अनुमान / अनुमानित   (NOT आकलन)
making charge -> मेकिंग चार्ज   (NOT निर्माण शुल्क)
22K / 24K / 18K -> 22 कैरेट / 24 कैरेट / 18 कैरेट   (NOT 22K, 22 KT)
jeweller -> ज्वेलर   (NOT जौहरी)   |  shop -> दुकान (NOT स्टोर)
usual / normal -> आम   (NOT सामान्य)
range (price band) -> रेंज   (NOT दायरा/सीमा)
cheaper / pricier -> सस्ता / महंगा
this week / this month -> इस हफ़्ते / इस महीने   (NOT सप्ताह / माह)
global gold price -> दुनिया के बाज़ार में सोने का भाव   (NOT वैश्विक)
accurate -> सही   (NOT सटीक, except on the technical page)
reading (a recorded price) -> भाव / दर्ज किया भाव   (NOT रीडिंग)
history (section) -> पुराने भाव
rough indication -> मोटा अंदाज़ा   (NOT संकेतात्मक)
Tanishq's listed rate -> Tanishq का बताया भाव / Tanishq की साइट पर दिया भाव   (NOT सूचीबद्ध)
not financial advice -> यह निवेश की सलाह नहीं है
Loanwords people say are kept: ट्रेंड, चार्ट, ऐप, रीफ़्रेश, ऑफ़लाइन, GST, HUID.
{name} tokens are numbers/dates filled in at runtime and must be kept."""

RUBRIC = """You are a careful native Hindi reader reviewing text for an Indian gold-jewellery buyer's \
website. Judge ONE candidate Hindi string against its English source.

Score each criterion 1-5 (5 = excellent, 1 = clearly bad):
- natural: sounds like everyday spoken Hindi a shopper would use, not a literal/textbook translation.
- correct: faithful to the English meaning, fits the context (button vs sentence vs banner), keeps every \
{{placeholder}} and number, adds no claim the English does not make.
- glossary: uses the glossary terms below wherever the concept appears (a rejected term = low score).
- grammar: grammatical, correct spelling, gender/number agreement, punctuation.

GLOSSARY:
{glossary}

CONTEXT: kind={kind}; screen={screen}
ENGLISH SOURCE: {en}
CANDIDATE HINDI: {hi}

Answer ONLY with JSON: {{"natural": n, "correct": n, "glossary": n, "grammar": n, "note": "<=12 words"}}"""

# model tag -> (family, extra ollama option)
JUDGES = {
    "gemma2:9b": "Google Gemma",
    "qwen3:8b": "Alibaba Qwen",
    "llama3.1:8b": "Meta Llama",
    "qwen3:30b-a3b": "Alibaba Qwen (larger MoE)",
}

# Constructed calibration cases. expect: criterion -> True (must pass, score>=4) / False (must fail).
CAL = [
    # clearly good: every criterion should pass
    ("G1", "label", "main page", "Share", "शेयर करें", dict.fromkeys(CRITERIA, True)),
    (
        "G2",
        "sentence",
        "main page",
        "Today's 22K gold price is ₹{price}/gram — check Gold Tracker",
        "आज 22 कैरेट सोने का भाव ₹{price}/ग्राम है — Gold Tracker पर देखें",
        dict.fromkeys(CRITERIA, True),
    ),
    (
        "G3",
        "sentence",
        "main page",
        "Right around the middle for this month.",
        "इस महीने के बीच के भाव के आसपास।",
        dict.fromkeys(CRITERIA, True),
    ),
    ("G4", "aria-label", "main page", "Price history", "पुराने भाव", dict.fromkeys(CRITERIA, True)),
    (
        "G5",
        "banner/message",
        "main page",
        "You're offline — no prices loaded yet",
        "आप ऑफ़लाइन हैं — अभी तक कोई भाव लोड नहीं हुआ",
        dict.fromkeys(CRITERIA, True),
    ),
    (
        "G6",
        "heading",
        "main page",
        "How much would you pay?",
        "आपको कितना पड़ेगा?",
        dict.fromkeys(CRITERIA, True),
    ),
    # clearly wrong meaning (opposite / unrelated / missing figure)
    (
        "B1",
        "sentence",
        "main page",
        "Cheaper than most days this month.",
        "इस महीने के ज़्यादातर दिनों से महंगा।",
        {"correct": False},
    ),
    (
        "B2",
        "banner/message",
        "main page",
        "You're offline — no prices loaded yet",
        "आपका खाता बंद कर दिया गया है",
        {"correct": False},
    ),
    (
        "B3",
        "sentence",
        "main page",
        "Down ₹{delta} this week.",
        "इस हफ़्ते भाव घटा है।",
        {"correct": False},
    ),
    # clearly ungrammatical / clearly unnatural
    (
        "N1",
        "sentence",
        "main page",
        "Prices have been fairly steady this month.",
        "कीमतें इस महीना काफी स्थिर होना रही है।",
        {"grammar": False},
    ),
    (
        "N2",
        "sentence",
        "main page",
        "Getting cheaper this week",
        "इस सप्ताह मूल्य न्यूनीकरण प्रक्रिया में संलग्न है",
        {"natural": False},
    ),
    # meaning right and readable, but uses rejected glossary terms
    ("V1", "label", "main page", "Making charge", "निर्माण शुल्क", {"glossary": False}),
    (
        "V2",
        "heading",
        "main page",
        "22K gold rate today",
        "आज का स्वर्ण मूल्य 22K",
        {"glossary": False},
    ),
    (
        "V3",
        "sentence",
        "main page",
        "Estimate — stores vary.",
        "आकलन — स्टोर अलग-अलग होते हैं।",
        {"glossary": False},
    ),
]
CAL_THRESHOLD = 0.90  # share of expectation cells a judge must get right to be kept


def ask(model: str, prompt: str) -> dict:
    body = json.dumps(
        {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0, "seed": SEED, "num_predict": 160},
            **({"think": False} if model.startswith("qwen3") else {}),
        }
    ).encode()
    req = urllib.request.Request(
        "http://localhost:11434/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    for attempt in range(4):  # Ollama 500s transiently while swapping models
        try:
            with urllib.request.urlopen(req, timeout=900) as r:
                raw = json.loads(r.read())["response"]
            break
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
            if attempt == 3:
                return {"parse_error": "request failed"}
            time.sleep(20)
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.S)
    try:
        out = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, flags=re.S)
        try:
            out = json.loads(m.group(0)) if m else {"parse_error": raw[:200]}
        except json.JSONDecodeError:
            out = {"parse_error": raw[:200]}
    return out


def scores(r: dict) -> dict[str, int] | None:
    try:
        s = {c: int(r[c]) for c in CRITERIA}
    except (KeyError, TypeError, ValueError):
        return None
    return s if all(1 <= v <= 5 for v in s.values()) else None


def prompt_for(it: dict) -> str:
    return RUBRIC.format(
        glossary=GLOSSARY, kind=it["kind"], screen=it["screen"], en=it["en"], hi=it["hi"]
    )


# --------------------------------------------------------------------------- build
def build() -> None:
    before = json.loads((OUT / "inventory_before.json").read_text(encoding="utf-8"))
    after = json.loads((OUT / "inventory_after.json").read_text(encoding="utf-8"))
    b_by_key = {r["key"]: r for r in before if not r.get("orphan_hi_key")}
    items = []
    for r in after:
        old = b_by_key.get(r["key"])
        for i, hv in enumerate(r["hi_variants"]):
            en_v = (
                next((v for v in r["en_variants"] if v["variant"] == hv["variant"]), None)
                or (r["en_variants"][min(i, len(r["en_variants"]) - 1)])
            )
            base = {
                "key": r["key"],
                "variant": hv["variant"],
                "kind": r["kind"],
                "screen": r["screen"],
                "en": en_v["text"],
            }
            items.append({**base, "which": "new", "hi": hv["text"]})
            if old and old["has_hindi"]:
                ov = next((v for v in old["hi_variants"] if v["variant"] == hv["variant"]), None)
                if ov and ov["text"] != hv["text"]:
                    items.append({**base, "which": "old", "hi": ov["text"]})
    random.Random(SEED).shuffle(items)  # order carries no author signal
    for n, it in enumerate(items):
        it["id"] = f"u{n:04d}"
    (OUT / "items.json").write_text(
        json.dumps(items, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    new = sum(i["which"] == "new" for i in items)
    print(f"items={len(items)} new={new} old={len(items) - new}")


def refresh() -> None:
    """After a late wording fix: update items.json in place (ids are kept) from inventory_after.json
    and drop the stale judgments of every candidate whose text changed, so `judge` re-rates only those."""
    items = json.loads((OUT / "items.json").read_text(encoding="utf-8"))
    after = {
        r["key"]: r for r in json.loads((OUT / "inventory_after.json").read_text(encoding="utf-8"))
    }
    stale = set()
    for it in items:
        if it["which"] != "new":
            continue
        hv = next(v for v in after[it["key"]]["hi_variants"] if v["variant"] == it["variant"])
        if hv["text"] != it["hi"]:
            it["hi"] = hv["text"]
            stale.add(it["id"])
    (OUT / "items.json").write_text(
        json.dumps(items, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    for path in OUT.glob("judgments_*.jsonl"):
        rows = [
            r
            for r in path.read_text(encoding="utf-8").splitlines()
            if r and json.loads(r)["id"] not in stale
        ]
        path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    print("refreshed", sorted(stale))


# --------------------------------------------------------------------------- calibrate
def calibrate(models: list[str]) -> None:
    cal_path = OUT / "calibration.json"
    result = json.loads(cal_path.read_text(encoding="utf-8")) if cal_path.exists() else {}
    for m in models:
        cells, right, rows = 0, 0, []
        parse_fail = 0
        for cid, kind, screen, en, hi, expect in CAL:
            r = ask(m, prompt_for({"kind": kind, "screen": screen, "en": en, "hi": hi}))
            s = scores(r)
            if s is None:
                parse_fail += 1
                rows.append({"case": cid, "raw": r, "cells": "parse failure"})
                cells += len(expect)
                continue
            got = {}
            for c, want in expect.items():
                ok = (s[c] >= PASS_MIN) == want
                cells += 1
                right += ok
                got[c] = {"score": s[c], "want_pass": want, "ok": ok}
            rows.append({"case": cid, "scores": s, "cells": got, "note": r.get("note")})
            print(m, cid, s, flush=True)
        acc = right / cells if cells else 0.0
        result[m] = {
            "family": JUDGES.get(m, "?"),
            "cells_right": right,
            "cells_total": cells,
            "accuracy": round(acc, 3),
            "parse_failures": parse_fail,
            "kept": acc >= CAL_THRESHOLD and parse_fail == 0,
            "rows": rows,
        }
        print(m, "calibration accuracy", round(acc, 3), "kept", result[m]["kept"], flush=True)
    (OUT / "calibration.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )


# --------------------------------------------------------------------------- judge
def judge(model: str, which: str = "all") -> None:
    items = json.loads((OUT / "items.json").read_text(encoding="utf-8"))
    if which != "all":
        items = [i for i in items if i["which"] == which]
    tag = model.replace(":", "_").replace("/", "_")
    path = OUT / f"judgments_{tag}.jsonl"
    done = set()
    if path.exists():
        done = {
            json.loads(line)["id"] for line in path.read_text(encoding="utf-8").splitlines() if line
        }
    t0 = time.time()
    with path.open("a", encoding="utf-8") as fh:
        for n, it in enumerate(items):
            if it["id"] in done:
                continue
            r = ask(model, prompt_for(it))
            row = {
                "id": it["id"],
                "model": model,
                "scores": scores(r),
                "note": r.get("note"),
                "raw": None if scores(r) else r,
            }
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()
            if n % 25 == 0:
                print(model, n, "/", len(items), f"{time.time() - t0:.0f}s", flush=True)


# --------------------------------------------------------------------------- analyze
def kappa(a: list[bool], b: list[bool]) -> float:
    n = len(a)
    po = sum(x == y for x, y in zip(a, b, strict=True)) / n
    pa, pb = sum(a) / n, sum(b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return 1.0 if pe == 1 else (po - pe) / (1 - pe)


def analyze() -> None:
    items = {i["id"]: i for i in json.loads((OUT / "items.json").read_text(encoding="utf-8"))}
    cal = json.loads((OUT / "calibration.json").read_text(encoding="utf-8"))
    kept = [m for m, v in cal.items() if v["kept"]]
    J: dict[str, dict[str, dict]] = {}
    for m in cal:
        p = OUT / f"judgments_{m.replace(':', '_')}.jsonl"
        if p.exists():
            J[m] = {r["id"]: r for r in map(json.loads, p.read_text(encoding="utf-8").splitlines())}
    summary: dict = {
        "label": "LLM consensus, not native review",
        "judges_kept": kept,
        "judges_dropped": [m for m in cal if not cal[m]["kept"]],
        "per_judge": {},
        "agreement": {},
    }
    for m in J:
        valid = [r for r in J[m].values() if r["scores"]]
        summary["per_judge"][m] = {"n_scored": len(valid), "n_parse_fail": len(J[m]) - len(valid)}
    # --- agreement between kept judges, per criterion, on pass/fail (new strings only) ---
    pairs = [(a, b) for i, a in enumerate(kept) for b in kept[i + 1 :] if a in J and b in J]
    for a, b in pairs:
        # agreement is on the NEW strings only, so every judge pair is compared on the same population
        ids = [
            i
            for i in J[a]
            if i in J[b] and items[i]["which"] == "new" and J[a][i]["scores"] and J[b][i]["scores"]
        ]
        row = {"n": len(ids)}
        for c in (*CRITERIA, "overall"):

            def ok(m: str, i: str, c=c) -> bool:
                s = J[m][i]["scores"]
                return min(s.values()) >= PASS_MIN if c == "overall" else s[c] >= PASS_MIN

            pa = [ok(a, i) for i in ids]
            pb = [ok(b, i) for i in ids]
            row[c] = {
                "pct_agree": round(sum(x == y for x, y in zip(pa, pb, strict=True)) / len(ids), 3),
                "kappa": round(kappa(pa, pb), 3),
                "pass_rate_a": round(sum(pa) / len(ids), 3),
                "pass_rate_b": round(sum(pb) / len(ids), 3),
            }
            sa = [
                J[a][i]["scores"][c] if c != "overall" else min(J[a][i]["scores"].values())
                for i in ids
            ]
            sb = [
                J[b][i]["scores"][c] if c != "overall" else min(J[b][i]["scores"].values())
                for i in ids
            ]
            row[c]["within1"] = round(
                sum(abs(x - y) <= 1 for x, y in zip(sa, sb, strict=True)) / len(ids), 3
            )
        summary["agreement"][f"{a} vs {b}"] = row
    # --- old vs new, per kept judge (paired by key+variant) ---
    summary["old_vs_new"] = {}
    by_pair: dict[tuple[str, str], dict[str, str]] = {}
    for iid, it in items.items():
        by_pair.setdefault((it["key"], it["variant"]), {})[it["which"]] = iid
    for m in kept:
        if m not in J:
            continue
        d = {c: [0.0, 0.0, 0] for c in CRITERIA}
        for pair in by_pair.values():
            if "old" in pair and "new" in pair:
                so, sn = (
                    J[m].get(pair["old"], {}).get("scores"),
                    J[m].get(pair["new"], {}).get("scores"),
                )
                if so and sn:
                    for c in CRITERIA:
                        d[c][0] += so[c]
                        d[c][1] += sn[c]
                        d[c][2] += 1
        summary["old_vs_new"][m] = {
            c: {
                "old_mean": round(v[0] / v[2], 2),
                "new_mean": round(v[1] / v[2], 2),
                "n_pairs": v[2],
            }
            for c, v in d.items()
            if v[2]
        }
    # --- contested list (NEW strings): judges split, or >= 2 judges flag (min score <= 3) ---
    contested = []
    lone = 0
    for iid, it in items.items():
        if it["which"] != "new":
            continue
        sc = {
            m: J[m][iid]["scores"] for m in kept if m in J and iid in J[m] and J[m][iid]["scores"]
        }
        if len(sc) < 2:
            continue
        flags = {m: min(s.values()) < PASS_MIN for m, s in sc.items()}
        n_flag, n_j = sum(flags.values()), len(flags)
        # GG list = a majority of the kept judges flags the string (any criterion scored <= 3).
        # A flag from a single judge is recorded in the summary count only: with judge-pair kappas
        # of 0.1-0.4 a lone flag is mostly judge noise, not a contested string.
        if n_flag == n_j:
            why = "all judges flag"
        elif n_flag * 2 > n_j:
            why = f"majority flags ({n_flag} of {n_j})"
        else:
            if n_flag:
                lone += 1
            continue
        worst = {c: min(s[c] for s in sc.values()) for c in CRITERIA}
        contested.append(
            {
                "id": iid,
                "key": it["key"],
                "variant": it["variant"],
                "en": it["en"],
                "hi_new": it["hi"],
                "why": why,
                "scores": sc,
                "worst_by_criterion": worst,
                "notes": {m: J[m][iid].get("note") for m in sc},
            }
        )
    contested.sort(
        key=lambda r: (r["why"] != "all judges flag", sum(r["worst_by_criterion"].values()))
    )
    summary["n_new_strings_scored_by_all_kept"] = sum(
        1
        for iid, it in items.items()
        if it["which"] == "new"
        and all(m in J and iid in J[m] and J[m][iid]["scores"] for m in kept)
    )
    summary["n_contested"] = len(contested)
    summary["n_flagged_by_one_judge_only"] = lone
    (OUT / "contested.json").write_text(
        json.dumps(contested, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    (OUT / "summary_stats.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {k: v for k, v in summary.items() if k != "per_judge"}, ensure_ascii=False, indent=1
        )[:4000]
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "calibrate", "judge", "analyze", "refresh"])
    ap.add_argument("--models", nargs="*", default=list(JUDGES))
    ap.add_argument("--which", choices=["all", "new", "old"], default="all")
    a = ap.parse_args()
    if a.cmd == "build":
        build()
    elif a.cmd == "refresh":
        refresh()
    elif a.cmd == "calibrate":
        calibrate(a.models)
    elif a.cmd == "judge":
        for m in a.models:
            judge(m, a.which)
    else:
        analyze()
    return 0


if __name__ == "__main__":
    sys.exit(main())
