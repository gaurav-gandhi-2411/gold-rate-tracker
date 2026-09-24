"""U4: blind readability rating of user-facing strings by two model families
(local Ollama: gemma2:9b = Google, qwen3:30b-a3b = Alibaba). LLM-consensus,
not user testing. Input: JSON list of {"key": ..., "text": ...}.
Output: JSON with per-model ratings, agreement, and the union of flags."""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.request

MODELS = ["gemma2:9b", "qwen3:30b-a3b"]
RUBRIC = """You are checking website text for ordinary Indian gold buyers (roughly Class 10
English, not finance experts). Read the TEXT and answer ONLY with JSON:
{"score": 1-5, "hard_words": [...], "misleading": true|false, "why": "<one short sentence>"}
score: 5 = any buyer understands it at once; 3 = understandable with effort; 1 = confusing.
hard_words: words or phrases a general buyer may not understand (empty list if none).
misleading: true if the text could make a buyer believe something stronger or different
than it says (for example that a price is guaranteed or a prediction is reliable).
Rupee amounts, "22K", "GST" and "IBJA" followed by an explanation are fine.
TEXT: """


def ask(model: str, text: str) -> dict:
    body = json.dumps(
        {
            "model": model,
            "prompt": RUBRIC + json.dumps(text),
            "stream": False,
            "format": "json",
            "options": {"temperature": 0, "seed": 42},
            **({"think": False} if model.startswith("qwen3") else {}),
        }
    ).encode()
    req = urllib.request.Request(
        "http://localhost:11434/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    for attempt in range(4):  # Ollama 500s transiently while swapping models in and out of RAM
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                raw = json.loads(r.read())["response"]
            break
        except urllib.error.HTTPError:
            if attempt == 3:
                raise
            time.sleep(30)
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.S)
    try:
        out = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, flags=re.S)
        out = json.loads(m.group(0)) if m else {"score": None, "parse_error": raw[:200]}
    return out


def flagged(r: dict) -> bool:
    """Calibrated on GG's reference sentence ("Our estimate is usually within Rs 90 of the
    store price, about 7 times out of 10"), which both models score 3: a string is flagged
    by a model at score <= 2 or if the model thinks it could mislead."""
    s = r.get("score")
    return s is None or s <= 2 or bool(r.get("misleading"))


def consensus_hard_words(ratings: dict) -> list[str]:
    """Words BOTH models call hard (case-insensitive)."""
    sets = [{w.lower().strip() for w in ratings[m].get("hard_words") or []} for m in MODELS]
    return sorted(set.intersection(*sets)) if sets else []


def main() -> int:
    with open(sys.argv[1], encoding="utf-8") as fh:
        items = json.load(fh)
    results = []
    for it in items:
        row = {"key": it["key"], "text": it["text"], "ratings": {}}
        for m in MODELS:
            row["ratings"][m] = ask(m, it["text"])
        row["flag_by"] = [m for m in MODELS if flagged(row["ratings"][m])]
        row["consensus_hard_words"] = consensus_hard_words(row["ratings"])
        results.append(row)
        print(
            it["key"],
            {m: row["ratings"][m].get("score") for m in MODELS},
            row["flag_by"],
            flush=True,
        )
    f = [[flagged(r["ratings"][m]) for m in MODELS] for r in results]
    agree = sum(a == b for a, b in f) / len(f) if f else 0.0
    pa = sum(a for a, _ in f) / len(f) if f else 0.0
    pb = sum(b for _, b in f) / len(f) if f else 0.0
    pe = pa * pb + (1 - pa) * (1 - pb)
    kappa = (agree - pe) / (1 - pe) if pe < 1 else 1.0
    summary = {
        "method": "LLM-consensus (not user testing): two model families rated each string "
        "blind to each other with the same rubric; temperature 0, seed 42",
        "models": MODELS,
        "n_strings": len(results),
        "flag_agreement": agree,
        "cohens_kappa_flags": kappa,
        "flag_rule": "model flags at score <= 2 or misleading; plus hard words named by both",
        "flagged_by_either": [r["key"] for r in results if r["flag_by"]],
        "consensus_hard_words": {
            r["key"]: r["consensus_hard_words"] for r in results if r["consensus_hard_words"]
        },
        "results": results,
    }
    with open(sys.argv[2], "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, ensure_ascii=False)
    print(
        "agreement",
        round(agree, 3),
        "kappa",
        round(kappa, 3),
        "flagged",
        len(summary["flagged_by_either"]),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
