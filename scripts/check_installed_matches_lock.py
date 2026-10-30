"""Fail unless every package ml/requirements-inference.lock pins is installed at exactly that version.

    python scripts/check_installed_matches_lock.py [--lock ml/requirements-inference.lock]

The required `lint` job installs the lock the way check-price does, so the suite tests what
production runs (brief 2026-10-29: lint had been installing ml/requirements.txt and testing
pandas 3.1.0 and torch 2.15 while production ran 3.0.6 and 2.13). This is the proof that it did.

A local version label is ignored: check-price installs with the CPU torch index, so the lock's
`torch==2.13.0` arrives as `2.13.0+cpu`, which pip and uv accept as satisfying `==2.13.0` (PEP 440).
That case is printed, never hidden. Packages installed but not in the lock are not judged (e.g. the
CI image's own pip/setuptools tooling).

Exit codes: 0 every pin matches; 1 at least one missing or different; 2 the lock cannot be read,
or has a top-level line that is not a plain `name==version` pin (so it would escape the check).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import print_runtime_versions as prv

ROOT = Path(__file__).resolve().parent.parent


def unparsed_lines(lock: Path) -> list[str]:
    """Top-level lock lines that are not a plain `name==version` pin (e.g. `pkg[extra]==1`,
    `pkg @ url`). lock_pins skips them, so without this a future recompile could drop a package
    out of the check with nothing to show for it."""
    return [
        line
        for line in lock.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line[0].isspace() and not line.startswith("#")
        if not prv._PIN.fullmatch(line.strip())
    ]


def mismatches(pins: dict[str, str]) -> tuple[list[str], list[str]]:
    """(problems, notes): a missing or different version is a problem; a match that differs only
    in its local label (`+cpu`) is a note."""
    problems: list[str] = []
    notes: list[str] = []
    for name, pin in sorted(pins.items()):
        have = prv.installed(name)
        if have is None:
            problems.append(f"{name}: NOT INSTALLED (lock pins {pin})")
        elif have != pin:
            if have.split("+", 1)[0] == pin.split("+", 1)[0]:
                notes.append(f"{name}=={have} (lock pins {pin}; local label only)")
            else:
                problems.append(f"{name}=={have} (lock pins {pin})")
    return problems, notes


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--lock", type=Path, default=ROOT / "ml/requirements-inference.lock")
    args = ap.parse_args(argv)
    try:
        pins = prv.lock_pins(args.lock)
        unparsed = unparsed_lines(args.lock)
    except OSError as exc:
        print(f"CANNOT CHECK: {type(exc).__name__}: {exc}")
        return 2
    if unparsed:
        for line in unparsed:
            print(f"CANNOT CHECK: lock line is not a plain name==version pin: {line}")
        return 2
    if not pins:
        print(f"CANNOT CHECK: no pins read from {args.lock}")
        return 2
    problems, notes = mismatches(pins)
    for line in notes:
        print(f"note: {line}")
    for line in problems:
        print(f"MISMATCH: {line}")
    print(f"{len(pins) - len(problems)} of {len(pins)} lock pins installed exactly")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
