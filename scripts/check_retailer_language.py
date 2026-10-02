"""scripts/check_retailer_language.py -- fails when judgmental wording appears near a
retailer's name in user-facing text (ADR 059, decision G1b).

Why: the product names retailers (Tanishq, GRT, Malabar, Kalyan) whose rates it reads.
Every such mention must be strictly factual, dated and neutral -- "Tanishq's listed
rate is 2.1% above today's IBJA rate", never "Tanishq is overcharging". A single
careless string is a reputational and legal risk this check exists to stop at CI.

Scope (rule 85b -- the shapes actually covered):
  * Files: index.html, how-we-know.html, og.html, manifest.webmanifest, i18n.js (EN+HI),
    app.js, how-we-know.js, how-we-know-strings.js, flags.js, ml/public_copy.py,
    ml/notifications.py, README.md. Deliberately raw text, INCLUDING code comments
    (except HTML comments): a judgmental remark about a retailer in a comment is one
    copy-paste away from UI text, and a broad scan is cheap to keep clean.
  * Retailer names: tanishq, kalyan, malabar, grt (word-bounded, any case) and the
    Devanagari forms तनिष्क, कल्याण, मालाबार, जीआरटी.
  * Judgmental terms (EN): overcharg*, over-charg*, rip-off/ripoff/rip off, scam*,
    cheat*, fleec*, loot*, gouge/gouging, fraud*, swindl*, con job, daylight robbery,
    exploit*, greed*, overpriced, over-priced, expensive, pricey, costly, inflated,
    extortionate, exorbitant, shady, dishonest, unfair, predatory, beware, avoid,
    "too much", "too high", "don't buy". (HI): लूट, धोखा, ठग, महंगा/महँगा, ज़्यादा वसूल,
    जालसाज़, बेईमान, सावधान.
  * "Near" = within WINDOW characters of a retailer name, in either direction.
Not covered: meaning expressed without any listed word, or text assembled at runtime
from pieces that are individually clean. Human review of new retailer strings is still
required (ADR 059's Hindi native-review list).

Usage:
    python scripts/check_retailer_language.py [--repo-root PATH] [FILE ...]
Exit 0 = clean, 1 = at least one hit (each printed with file:line and context).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

DEFAULT_FILES = (
    "index.html",
    "how-we-know.html",
    "og.html",
    "manifest.webmanifest",
    "i18n.js",
    "app.js",
    "how-we-know.js",
    "how-we-know-strings.js",
    "flags.js",
    "ml/public_copy.py",
    "ml/notifications.py",
    "README.md",
)

WINDOW = 120

RETAILER_RE = re.compile(r"\b(?:tanishq|kalyan|malabar|grt)\b|तनिष्क|कल्याण|मालाबार|जीआरटी", re.I)

JUDGMENT_RE = re.compile(
    r"\bover-?charg\w*|\brip[- ]?off\w*|\bscam\w*|\bcheat\w*|\bfleec\w*|\bloot\w*"
    r"|\bgoug\w*|\bfraud\w*|\bswindl\w*|\bcon job\b|\bdaylight robbery\b|\bexploit\w*"
    r"|\bgreed\w*|\bover-?priced\b|\bexpensive\b|\bpricey\b|\bcostly\b|\binflated\b"
    r"|\bextortionate\b|\bexorbitant\b|\bshady\b|\bdishonest\b|\bunfair\b|\bpredatory\b"
    r"|\bbeware\b|\bavoid\b|\btoo much\b|\btoo high\b|\bdon'?t buy\b"
    r"|लूट|धोखा|ठग|मह[ंँ]गा|ज़्यादा वसूल|जालसाज़|बेईमान|सावधान",
    re.I,
)

HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)


def find_hits(text: str, *, window: int = WINDOW) -> list[tuple[int, str, str]]:
    """Return (offset, retailer, term) for each judgmental term near a retailer name."""
    retailers = [(m.start(), m.group(0)) for m in RETAILER_RE.finditer(text)]
    hits: list[tuple[int, str, str]] = []
    if not retailers:
        return hits
    for jm in JUDGMENT_RE.finditer(text):
        for pos, name in retailers:
            if abs(pos - jm.start()) <= window:
                hits.append((jm.start(), name, jm.group(0)))
                break
    return hits


def scan_file(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix == ".html":
        # Blank out comments but keep offsets/line numbers stable.
        text = HTML_COMMENT_RE.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)
    out = []
    for offset, name, term in find_hits(text):
        line = text.count("\n", 0, offset) + 1
        start = max(0, offset - 60)
        context = text[start : offset + 60].replace("\n", " ")
        out.append(f"{path}:{line}: '{term}' near '{name}': …{context}…")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parent.parent)
    ap.add_argument("files", nargs="*", help="override the default file list")
    args = ap.parse_args(argv)

    paths = [Path(f) for f in args.files] or [args.repo_root / f for f in DEFAULT_FILES]
    missing = [p for p in paths if not p.exists()]
    if missing:
        # Fail closed: a renamed file must not silently drop out of the scan.
        print("FAIL: scanned file(s) missing: " + ", ".join(map(str, missing)))
        return 1

    problems = [hit for p in paths for hit in scan_file(p)]
    if problems:
        print(f"FAIL: {len(problems)} judgmental term(s) near a retailer name (ADR 059):")
        for msg in problems:
            print("  " + msg)
        print(
            'Rewrite as a dated, neutral fact, e.g. "Tanishq\'s listed rate is X% above IBJA today".'
        )
        return 1
    print(f"OK: no judgmental wording near a retailer name in {len(paths)} file(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
