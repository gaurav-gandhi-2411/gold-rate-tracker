"""Retailer takedown switch (ADR 059, decision G1d; runbook: docs/RETAILER_TAKEDOWN.md).

``config/retailers.json`` holds one ``enabled`` boolean per scraped retailer. Every code
path that fetches or uses a retailer's data asks :func:`is_enabled` first, so a takedown
request is one committed edit, not a hunt through workflows and modules.

Fail loud, never open: a missing file, bad JSON, a missing/unknown retailer or a
non-boolean flag raises :class:`RetailerConfigError`. The two silent alternatives are
both wrong -- defaulting to "enabled" would keep publishing data we were asked to
remove, and defaulting to "disabled" would silently change the live site because of a
typo. A loud failure stops the workflow; the site keeps serving its last good files.

``RETAILERS_CONFIG_PATH`` overrides the path (tests and the takedown dry run only).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = ROOT / "config" / "retailers.json"

# Every retailer any code path scrapes. A config that omits one of these is an error:
# "not mentioned" must never be read as either on or off.
KNOWN_RETAILERS: frozenset[str] = frozenset({"tanishq", "grt", "malabar", "kalyan"})


class RetailerConfigError(RuntimeError):
    """config/retailers.json is missing, malformed, or incomplete."""


def _config_path() -> Path:
    override = os.environ.get("RETAILERS_CONFIG_PATH")
    return Path(override) if override else DEFAULT_CONFIG_PATH


def load_retailer_flags(path: Path | None = None) -> dict[str, bool]:
    """Return ``{retailer: enabled}`` for every known retailer, or raise."""
    path = path or _config_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RetailerConfigError(f"retailer switch file missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RetailerConfigError(f"retailer switch file is not valid JSON: {path}: {exc}") from exc

    retailers = raw.get("retailers") if isinstance(raw, dict) else None
    if not isinstance(retailers, dict):
        raise RetailerConfigError(f"{path}: expected a top-level 'retailers' object")

    names = set(retailers)
    if names != KNOWN_RETAILERS:
        missing = sorted(KNOWN_RETAILERS - names)
        unknown = sorted(names - KNOWN_RETAILERS)
        raise RetailerConfigError(
            f"{path}: missing retailers {missing}, unknown retailers {unknown}"
        )

    flags: dict[str, bool] = {}
    for name, entry in retailers.items():
        enabled = entry.get("enabled") if isinstance(entry, dict) else None
        if not isinstance(enabled, bool):
            raise RetailerConfigError(f"{path}: retailers.{name}.enabled must be true or false")
        flags[name] = enabled
    return flags


def is_enabled(name: str, path: Path | None = None) -> bool:
    """True when ``name`` may be fetched and used. Raises on an unknown name."""
    if name not in KNOWN_RETAILERS:
        raise RetailerConfigError(f"unknown retailer {name!r}; known: {sorted(KNOWN_RETAILERS)}")
    return load_retailer_flags(path)[name]


def disabled_retailers(path: Path | None = None) -> list[str]:
    """Sorted names of every retailer currently switched off."""
    return sorted(name for name, on in load_retailer_flags(path).items() if not on)
