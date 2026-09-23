"""scripts/check_direction_signal_not_wired_without_promotion.py -- makes
"the direction signal can only become user-visible with an explicit,
GG-approved promotion record" mechanically enforced, not memory-enforced
(GG spec, 2026-09-23, item 1 of the continuation after #1903 merged).

The question this closes: does a passing `ml.direction.gate.
decide_direction_signal`/`decide_timing_signal` gate automatically reach
users? Traced end to end (docs/adr/036) -- as of this script's own first
run, the answer is no: `app.js`'s "Direction signal" section is a
hardcoded, permanently-"off" DARK state (ADR 019/020), gated only on an
UNRELATED field (`fc.chronos_companion.status`, the separate Chronos
price-magnitude forecast's own success/failure -- not the direction
classifier's gate at all). No file outside `ml/direction/` or its own
tests reads `probability_gate`, `timing_gate`, or calls
`decide_direction_signal`/`decide_timing_signal`.

That's a true CURRENT state, not a structural guarantee -- nothing stops a
future change from wiring app.js (or notifications) directly to
`data/direction_baseline.json`'s gate output without anyone deciding that
counts as a promotion. This script makes that decision unavoidable: if any
user-facing surface starts referencing the gate's decision, this fails
UNLESS `data/direction_promotion_record.json` exists (a small, human-
authored record of what was approved, by whom, and when -- see
ml/direction/gate.py's PROMOTION_RECORD_PATH for the exact schema this
script and `is_signal_promoted` both trust as the single source of truth).

Known blind spots (rule 85a) -- state plainly:
  - Regex-based, like every other guard script in this repo (see
    check_bot_pr_sync_allowlist.py's own docstring for why that's the
    accepted trade-off here): a reference disguised through string
    concatenation, a computed property-access, or an indirect re-export
    would not be caught. This catches direct textual references, which is
    what every actual incident of this shape in this codebase's history
    (AF1, T13, AE2) has looked like -- not obfuscated ones.
  - Only scans the files listed in SURFACE_FILES. A new user-facing file
    that isn't added to that list is invisible to this check.
  - Does not validate the CONTENTS of direction_promotion_record.json
    against anything (e.g. that it names a real, currently-shipping model
    config) -- its mere existence is what gates the check. Validating its
    content against the live baseline is a job for a promotion PR's own
    review, not this script.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROMOTION_RECORD_PATH = ROOT / "data" / "direction_promotion_record.json"

# Every file where a direction-gate wire-up would make the signal
# user-visible: the PWA itself, its translated copy, the offline shell, and
# every notification-adjacent module. NOT ml/direction/** itself (the gate
# living there is expected and fine) and not any tests/** file (tests are
# allowed to exercise the gate directly -- see TestPromotionGateNeverWired
# in tests/test_direction_gate.py for the paired positive/negative proof).
SURFACE_FILES: list[Path] = [
    ROOT / "app.js",
    ROOT / "i18n.js",
    ROOT / "index.html",
    ROOT / "service-worker.js",
    ROOT / "ml" / "notifications.py",
    ROOT / "ml" / "notification_routing.py",
    ROOT / "ml" / "public_copy.py",
]

# Direct references to the gate's DECISION -- not the mere word "direction"
# (which appears constantly in unrelated contexts: CSS `flex-direction`,
# price-direction arrows, scroll direction, etc.) or "gate" (used elsewhere
# for unrelated staleness/freshness gates). Deliberately specific to the two
# gate functions and the two JSON fields they populate.
_FORBIDDEN_PATTERNS = [
    re.compile(r"\bprobability_gate\b"),
    re.compile(r"\btiming_gate\b"),
    re.compile(r"\bdecide_direction_signal\b"),
    re.compile(r"\bdecide_timing_signal\b"),
]

# A reference inside a comment explaining the invariant (like this script's
# own docstring, or app.js's existing ADR 019/020 comment) is not a wire-up
# -- only flag matches OUTSIDE of `//`/`#`/`<!--` comment lines and JS/HTML
# block comments would be more precise, but per this script's own stated
# blind spots (regex-based), the simpler and SAFER default is to flag any
# match at all and let a human confirm a false positive is really a comment
# -- fail closed (rule 98a), never silently skip a real reference because
# it might be "just a comment."


def find_references(path: Path) -> list[tuple[int, str]]:
    if not path.exists():
        return []
    hits: list[tuple[int, str]] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        for pattern in _FORBIDDEN_PATTERNS:
            if pattern.search(line):
                hits.append((lineno, line.strip()))
                break
    return hits


def main() -> int:
    all_hits: dict[str, list[tuple[int, str]]] = {}
    for surface_path in SURFACE_FILES:
        hits = find_references(surface_path)
        if hits:
            all_hits[str(surface_path.relative_to(ROOT))] = hits

    if not all_hits:
        print(
            "PASS: no user-facing surface references the direction gate's decision "
            f"(scanned {len(SURFACE_FILES)} files)."
        )
        return 0

    if PROMOTION_RECORD_PATH.exists():
        print(
            f"PASS: user-facing references found, but {PROMOTION_RECORD_PATH.relative_to(ROOT)} "
            "exists -- treated as the explicit, approved promotion record."
        )
        for file, hits in all_hits.items():
            for lineno, text in hits:
                print(f"  (promoted) {file}:{lineno}: {text}")
        return 0

    print(
        "FAIL: a user-facing surface references the direction gate's decision, "
        f"but no {PROMOTION_RECORD_PATH.relative_to(ROOT)} exists.\n"
        "The direction signal may only become user-visible with an explicit, "
        "GG-approved promotion record committed to the repo.\n"
    )
    for file, hits in all_hits.items():
        for lineno, text in hits:
            print(f"  {file}:{lineno}: {text}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
