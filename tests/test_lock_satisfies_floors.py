"""Production's pinned lock must satisfy every floor declared in ml/requirements.txt.

check-price.yml, weekly-backtest.yml and eval-direction.yml install `ml/requirements-inference.lock`,
not `ml/requirements.txt`. The lock is generated from the requirements file by a manual
`uv pip compile` (docs/RUNBOOK.md: "Regenerate it whenever ml/requirements.txt changes") -- a rule
with no enforcement. Floors kept being raised (dependabot, CVE pins) while the lock stayed at its
2026-08-11 state, so on 2026-09-21 production ran below 5 of its 17 declared floors: cryptography
50.0.0 (<50.0.1), setuptools 83.0.0 (<84.0.0) -- both labelled CVE minimums in requirements.txt --
plus lxml 6.1.1 (<6.1.2), pandas 3.0.3 (<3.0.5) and yfinance 1.4.1 (<1.5.2). CI's own
`pip install -r ml/requirements.txt` resolves the newest releases, so nothing red ever showed it.

Both files are read as data; the floor set is discovered from the requirements file, not listed here.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _version_key(v: str) -> tuple[int, ...]:
    """Numeric release segments only (drops local tags like +cpu and pre-release suffixes)."""
    core = re.split(r"[+\-]", v, maxsplit=1)[0]
    return tuple(int(p) for p in re.findall(r"\d+", core))


def parse_floors(requirements_text: str) -> dict[str, str]:
    floors: dict[str, str] = {}
    for line in requirements_text.splitlines():
        m = re.match(r"^([A-Za-z0-9_.\-]+)\s*>=\s*([0-9][^\s,;#]*)", line.strip())
        if m:
            floors[_norm(m.group(1))] = m.group(2)
    return floors


def parse_pins(lock_text: str) -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in lock_text.splitlines():
        m = re.match(r"^([A-Za-z0-9_.\-]+)==(\S+)", line)
        if m:
            pins[_norm(m.group(1))] = m.group(2)
    return pins


def find_violations(requirements_text: str, lock_text: str) -> list[str]:
    floors, pins = parse_floors(requirements_text), parse_pins(lock_text)
    # Fail closed: an empty parse means a format change, not "nothing to check".
    assert len(floors) >= 5, f"parsed only {len(floors)} floors -- requirements parser is stale"
    assert len(pins) >= 30, f"parsed only {len(pins)} pins -- lock parser is stale"
    out = []
    for pkg, floor in sorted(floors.items()):
        pinned = pins.get(pkg)
        if pinned is None:
            out.append(f"{pkg}: floor >={floor} but not pinned in the lock")
        elif _version_key(pinned) < _version_key(floor):
            out.append(f"{pkg}: lock =={pinned} is below floor >={floor}")
    return out


def test_real_lock_satisfies_every_declared_floor() -> None:
    violations = find_violations(
        (REPO_ROOT / "ml" / "requirements.txt").read_text(encoding="utf-8"),
        (REPO_ROOT / "ml" / "requirements-inference.lock").read_text(encoding="utf-8"),
    )
    assert violations == [], (
        "production installs the lock, which is below ml/requirements.txt's floors "
        "(regenerate it: docs/RUNBOOK.md 'Regenerating the inference dependency lockfile'):\n  "
        + "\n  ".join(violations)
    )


_REQ = "\n".join(f"pkg{i}>=1.0.0" for i in range(5)) + "\nlxml>=6.1.2\n"
_LOCK = (
    "\n".join(f"pkg{i}==1.0.0" for i in range(5))
    + "\n"
    + "\n".join(f"dep{i}==1.0" for i in range(30))
)


def test_detects_a_pin_below_its_floor() -> None:
    # The violation itself, constructed: lxml floor raised, lock left behind.
    assert find_violations(_REQ, _LOCK + "\nlxml==6.1.1\n") == [
        "lxml: lock ==6.1.1 is below floor >=6.1.2"
    ]


def test_detects_a_floor_with_no_pin() -> None:
    assert find_violations(_REQ, _LOCK) == ["lxml: floor >=6.1.2 but not pinned in the lock"]


def test_a_pin_at_or_above_the_floor_passes_including_local_tags() -> None:
    assert find_violations(_REQ, _LOCK + "\nlxml==6.1.3+cpu\n") == []
    assert find_violations(_REQ, _LOCK + "\nlxml==6.1.2\n") == []


def test_version_compare_is_numeric_not_lexicographic() -> None:
    # "6.10.0" < "6.9.0" as strings; it must compare as newer.
    assert find_violations(_REQ, _LOCK + "\nlxml==6.10.0\n") == []


def test_empty_parse_fails_closed() -> None:
    try:
        find_violations("# nothing parseable\n", _LOCK)
    except AssertionError:
        return
    raise AssertionError("an unparseable requirements file must fail closed, not pass")
