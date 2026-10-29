"""Print the installed version of every runtime dependency, so a CI log shows what actually ran.

    python scripts/print_runtime_versions.py [--lock ml/requirements-inference.lock]

The names come from ml/requirements.txt plus the packages the forecast imports through
chronos-forecasting (torch, transformers). With --lock, each line also shows the version the lock
pins and marks a difference. That makes two things visible that no log showed before 2026-10-29:
check-price installs the lock with the CPU torch index, so `torch==2.13.0` can arrive as
`2.13.0+cpu`; and lint installs ml/requirements.txt, not the lock, so its tests can run on newer
versions than production.

Informational only: it always exits 0, and a package that is not installed prints as such.
"""

from __future__ import annotations

import argparse
import platform
import re
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Imported at runtime through chronos-forecasting, not listed in ml/requirements.txt.
INDIRECT = ("torch", "transformers")
_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==(\S+)")


def _canon(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def requirement_names(path: Path) -> list[str]:
    names: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0]
        m = _NAME.match(line)
        if m:
            names.append(m.group(1))
    return names


def lock_pins(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _PIN.match(line)
        if m:
            pins[_canon(m.group(1))] = m.group(2)
    return pins


def installed(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def report(names: list[str], pins: dict[str, str] | None) -> list[str]:
    rows = [f"python {platform.python_version()}"]
    for name in names:
        have = installed(name)
        row = f"{name}=={have}" if have else f"{name}: NOT INSTALLED"
        if pins is not None:
            pin = pins.get(_canon(name))
            if pin is None:
                row += "  (not in lock)"
            elif have != pin:
                row += f"  (lock pins {pin}: DIFFERS)"
        rows.append(row)
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--requirements", type=Path, default=ROOT / "ml/requirements.txt")
    ap.add_argument("--lock", type=Path, default=None)
    args = ap.parse_args(argv)
    names = requirement_names(args.requirements)
    names += [n for n in INDIRECT if _canon(n) not in {_canon(x) for x in names}]
    pins = lock_pins(args.lock) if args.lock else None
    print("\n".join(report(names, pins)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
