"""Guards the shape of .gitleaks.toml so an allowlist entry cannot quietly hide a secret."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

CONFIG = Path(__file__).resolve().parent.parent / ".gitleaks.toml"
# A regex of 20+ chars made only of these is a bare token, not a path pattern.
_VALUE_LIKE = re.compile(r"[A-Za-z0-9+/=_-]{20,}")
# Characters that make a pattern path-ish: separators, anchors, escapes, groups.
_PATHISH = set("/\\.^$()|*[]?")


def _entries() -> list[dict]:
    return tomllib.loads(CONFIG.read_text(encoding="utf-8")).get("allowlists", [])


def _patterns(entry: dict) -> list[str]:
    return list(entry.get("paths", [])) + list(entry.get("regexes", []))


def test_config_extends_defaults_and_has_entries() -> None:
    data = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
    assert data["extend"]["useDefault"] is True
    assert _entries()


def test_every_entry_has_a_description() -> None:
    for entry in _entries():
        assert str(entry.get("description", "")).strip(), entry


def test_every_entry_has_paths_or_commits_and_a_rule() -> None:
    for entry in _entries():
        assert entry.get("paths") or entry.get("commits"), entry
        assert entry.get("targetRules"), entry


def test_no_entry_uses_a_bare_wildcard_path() -> None:
    for entry in _entries():
        for p in entry.get("paths", []):
            assert p.strip() not in {".*", "^.*$", ".+", "(.*)"}, entry


def test_no_entry_names_a_value() -> None:
    for entry in _entries():
        assert not entry.get("regexes"), "allowlist by value regex is not permitted"
        for p in _patterns(entry):
            assert not (_VALUE_LIKE.fullmatch(p) and not (set(p) & _PATHISH)), p
