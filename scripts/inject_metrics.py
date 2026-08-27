#!/usr/bin/env python3
"""Resolve <!--METRIC:...--> markers in README.md/docs/*.md from data/*.json.

Why this exists: three numbers in this repo's docs were found hand-copied and
silently stale (R²=0.96 written 2026-07-18 from a fit that's since moved to
0.975; "97.3% coverage (n=75)" written 2026-08-06 against a file that's since
grown to 88.5%/n=96; direction-signal accuracy numbers 22 days behind their
own live source despite a doc header claiming "auto-measured weekly") --
session dated 2026-08-27. The fix is that these values stop being typed by
hand at all: a marker names its source field, this script renders it, and CI
(docs-freshness in lint.yml) fails if the committed text doesn't match a
fresh resolve.

Marker syntax (paired HTML comments, invisible when GitHub renders the
markdown -- the visible page shows plain text, never raw marker syntax):

    <!--METRIC:<json-path>#<field.path>:<format>[|n=<field>][|asof=<field>][|ci=<lo>,<hi>][|unresolved_if=<field>]-->
    rendered text goes here, replaced on every run
    <!--/METRIC-->

  <json-path>       repo-relative path to a data/*.json file
  <field.path>      dot-separated path into that JSON (supports nested objects,
                    e.g. horizons.h1.logistic_metrics.accuracy)
  <format>          pct1 (80.0%), pct2 (80.00%), num2 (0.98), num3 (0.975),
                    int (96), raw (verbatim)
  n=<field>         optional sibling field (same file) supplying the sample size;
                    rendered as "(n=<value>, ...)" alongside the value
  asof=<field>      optional sibling field supplying the as-of date/timestamp;
                    ISO timestamps are truncated to YYYY-MM-DD
  ci=<lo>,<hi>      optional pair of sibling fields (e.g. Wilson CI bounds),
                    rendered as "95% CI [lo%, hi%]" -- pct1/pct2 formats only
  unresolved_if=<field>  optional sibling boolean field; when it is falsy,
                    appends a fixed "— not yet resolvable at this sample
                    size" note. A computed flag, never hand-typed prose (see
                    ml.metrics.compute_band_coverage's resolvable_at_n).

The opening/closing comments are the PERSISTENT template -- unlike a
one-shot {{placeholder}} that gets consumed on first render (an earlier
draft of this script did exactly that, discovered and fixed before this
shipped: once resolved, nothing was left for CI to re-check against),
everything between the markers is discarded and regenerated every run, but
the markers themselves are never removed. That is what makes `--check` mode
meaningful: it re-resolves from live data and compares against whatever
currently sits between the markers.

Every resolved marker renders as "value (n=N, as of DATE)" together -- a
number without its n and date is exactly the defect class this fixes. If
either n= or asof= is omitted from a marker, that piece is omitted from the
rendered text too (some sources genuinely have no natural n, e.g. a single
fitted slope) -- but that is a per-marker authoring decision, not something
this script enforces.

FROZEN blocks: content between a line containing exactly
    <!--FROZEN reason="..."-->
and a later line containing exactly
    <!--/FROZEN-->
(or both markers inline on the same line, inside a single markdown table
cell) is never scanned for METRIC markers and never rewritten -- for ADR
historical snapshots and the backtest.json SHA-pinned citation, which must
describe a point-in-time decision, not drift with the live data. If you find
yourself wanting a METRIC marker inside a FROZEN block, the block is
mis-scoped -- split it, don't nest a live number inside a frozen one.

Modes:
    python scripts/inject_metrics.py            resolve and rewrite files in place
    python scripts/inject_metrics.py --check     resolve, compare, DON'T write --
                                                  exit 1 if any file would change,
                                                  or if any marker can't be
                                                  resolved. Used by CI.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

TARGET_GLOBS: tuple[str, ...] = ("README.md", "docs/*.md", "docs/adr/*.md")

METRIC_RE = re.compile(
    r"<!--METRIC:([^#<>]+)#([^:<>]+):([a-z0-9]+)((?:\|[a-z_]+=[^<>|]+)*)-->"
    r"(.*?)"
    r"<!--/METRIC-->",
    re.DOTALL,
)
FROZEN_START_RE = re.compile(r'<!--FROZEN reason="[^"]*"-->')
FROZEN_END_RE = re.compile(r"<!--/FROZEN-->")


class MetricError(Exception):
    pass


@cache
def _load_json(rel_path: str) -> dict:
    path = ROOT / rel_path
    if not path.exists():
        raise MetricError(f"{rel_path}: file does not exist")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MetricError(f"{rel_path}: invalid JSON ({exc})") from exc


def _get_field(data: dict, field_path: str, source: str) -> object:
    node: object = data
    parts = field_path.split(".")
    for i, part in enumerate(parts):
        if not isinstance(node, dict) or part not in node:
            walked = ".".join(parts[: i + 1])
            raise MetricError(f"{source}#{field_path}: no such field (failed at '{walked}')")
        node = node[part]
    return node


def _format_value(value: object, fmt: str, source: str) -> str:
    if fmt == "raw":
        return str(value)
    if not isinstance(value, int | float):
        raise MetricError(f"{source}: value {value!r} is not numeric, cannot format as {fmt!r}")
    if fmt == "pct1":
        return f"{value * 100:.1f}%"
    if fmt == "pct2":
        return f"{value * 100:.2f}%"
    if fmt == "num2":
        return f"{value:.2f}"
    if fmt == "num3":
        return f"{value:.3f}"
    if fmt == "int":
        return str(int(value))
    raise MetricError(f"{source}: unknown format {fmt!r}")


def _format_asof(value: object) -> str:
    text = str(value)
    # Truncate ISO timestamps ("2026-08-23T02:54:42...") to a plain date.
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return text[:10] if len(text) >= 10 and text[4] == "-" else text


_UNRESOLVED_NOTE = "not yet resolvable at this sample size"


def resolve_marker(json_path: str, field_path: str, fmt: str, modifiers_raw: str) -> str:
    source = f"{json_path}#{field_path}"
    data = _load_json(json_path)
    value = _get_field(data, field_path, json_path)
    rendered = _format_value(value, fmt, source)

    n_val: str | None = None
    asof_val: str | None = None
    ci_val: str | None = None
    unresolved_note: str | None = None
    for key, field in re.findall(r"\|([a-z_]+)=([^<>|]+)", modifiers_raw):
        if key == "n":
            raw = _get_field(data, field, json_path)
            n_val = str(int(raw)) if isinstance(raw, int | float) else str(raw)
        elif key == "asof":
            raw = _get_field(data, field, json_path)
            asof_val = _format_asof(raw)
        elif key == "ci":
            # ci=<lo_field>,<hi_field> -- renders "95% CI [lo%, hi%]", same
            # format as the marker's own value (pct1/pct2 only; a CI on a
            # num/int-formatted value isn't a supported combination).
            try:
                lo_field, hi_field = field.split(",")
            except ValueError as exc:
                raise MetricError(
                    f"{source}: ci= needs exactly two comma-separated fields"
                ) from exc
            lo_raw = _get_field(data, lo_field, json_path)
            hi_raw = _get_field(data, hi_field, json_path)
            if fmt not in ("pct1", "pct2"):
                raise MetricError(f"{source}: ci= modifier only supports pct1/pct2 formats")
            lo_text = _format_value(lo_raw, fmt, source)
            hi_text = _format_value(hi_raw, fmt, source)
            ci_val = f"95% CI [{lo_text}, {hi_text}]"
        elif key == "unresolved_if":
            # unresolved_if=<field> -- appends a fixed warning when that
            # boolean field is falsy (i.e. NOT resolvable at this n). A
            # computed flag, not hand-typed prose -- see E4a.
            raw = _get_field(data, field, json_path)
            if not raw:
                unresolved_note = _UNRESOLVED_NOTE
        else:
            raise MetricError(f"{source}: unknown modifier {key!r}")

    suffix_parts = []
    if n_val is not None:
        suffix_parts.append(f"n={n_val}")
    if ci_val is not None:
        suffix_parts.append(ci_val)
    if asof_val is not None:
        suffix_parts.append(f"as of {asof_val}")
    text = rendered
    if suffix_parts:
        text = f"{text} ({', '.join(suffix_parts)})"
    if unresolved_note is not None:
        text = f"{text} — {unresolved_note}"
    return text


def _frozen_spans(text: str) -> list[tuple[int, int]]:
    """Return [(start, end), ...] character spans that are inside a FROZEN block."""
    spans: list[tuple[int, int]] = []
    pos = 0
    while True:
        start_m = FROZEN_START_RE.search(text, pos)
        if not start_m:
            break
        end_m = FROZEN_END_RE.search(text, start_m.end())
        if not end_m:
            raise MetricError(f"unclosed FROZEN block starting at offset {start_m.start()}")
        spans.append((start_m.start(), end_m.end()))
        pos = end_m.end()
    return spans


def render_file(text: str) -> tuple[str, list[str]]:
    """Return (rendered_text, errors). errors is empty on full success."""
    frozen_spans = _frozen_spans(text)

    def in_frozen(idx: int) -> bool:
        return any(start <= idx < end for start, end in frozen_spans)

    errors: list[str] = []

    def _sub(match: re.Match[str]) -> str:
        if in_frozen(match.start()):
            return match.group(0)  # never touch markers inside FROZEN (shouldn't exist)
        json_path, field_path, fmt, modifiers_raw = match.group(1, 2, 3, 4)
        open_marker = match.group(0).split("-->", 1)[0] + "-->"
        try:
            rendered = resolve_marker(json_path, field_path, fmt, modifiers_raw)
        except MetricError as exc:
            errors.append(f"{open_marker}...<!--/METRIC-->: {exc}")
            return match.group(0)
        return f"{open_marker}{rendered}<!--/METRIC-->"

    rendered_text = METRIC_RE.sub(_sub, text)
    return rendered_text, errors


def target_files() -> list[Path]:
    seen: set[Path] = set()
    files: list[Path] = []
    for pattern in TARGET_GLOBS:
        for p in sorted(ROOT.glob(pattern)):
            if p.is_file() and p not in seen:
                seen.add(p)
                files.append(p)
    return files


def main() -> int:
    check_mode = "--check" in sys.argv[1:]
    any_errors = False
    any_changes = False

    for path in target_files():
        original = path.read_text(encoding="utf-8")
        try:
            rendered, errors = render_file(original)
        except MetricError as exc:
            print(f"FAIL: {path.relative_to(ROOT)}: {exc}")
            any_errors = True
            continue

        if errors:
            any_errors = True
            for e in errors:
                print(f"FAIL: {path.relative_to(ROOT)}: unresolved marker {e}")

        if rendered != original:
            any_changes = True
            if check_mode:
                print(
                    f"FAIL: {path.relative_to(ROOT)}: committed text does not match a fresh "
                    f"resolve — run `python scripts/inject_metrics.py` and commit the result."
                )
            else:
                path.write_text(rendered, encoding="utf-8", newline="\n")
                print(f"OK: {path.relative_to(ROOT)}: updated")

    if any_errors:
        return 1
    if check_mode and any_changes:
        return 1
    if not any_changes and not any_errors:
        print("OK: all metric markers resolved and up to date.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
