"""Every data file app.js fetches must be servable from the GitHub Pages build.

_config.yml's `exclude:` list scopes the Pages build to the app surface. It is a
hand-maintained registry that has to stay in sync with what app.js actually
fetches: data/calibration.json was excluded on 2026-07-18 (nothing fetched it
then), app.js started fetching it on 2026-08-11 (#786), and the live site
returned 404 for it on every page load for ~41 days before anything noticed --
render-smoke does not look at optional-fetch status codes and the client's
Sentry DSN is a placeholder, so no channel reported it.

This reads both files from the repo root; `find_excluded_fetches` takes the root
as a parameter so it can be pointed at any historical tree.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Any quoted "data/....json" string literal on a non-comment line. Broader than
# the *_URL constants on purpose: a fetch built from a literal elsewhere counts too.
_DATA_LITERAL = re.compile(r"""["'](data/[A-Za-z0-9_./-]+\.json)["']""")


def fetched_data_files(app_js: str) -> set[str]:
    found: set[str] = set()
    for line in app_js.splitlines():
        if line.lstrip().startswith("//"):
            continue
        found.update(_DATA_LITERAL.findall(line))
    return found


def excluded_paths(config_yml: str) -> list[str]:
    """Entries of the top-level `exclude:` list (Jekyll paths relative to the site root)."""
    entries: list[str] = []
    in_exclude = False
    for raw in config_yml.splitlines():
        line = raw.rstrip()
        if re.match(r"^exclude:\s*$", line):
            in_exclude = True
            continue
        if in_exclude:
            m = re.match(r"^\s+-\s+(\S+)\s*$", line)
            if m:
                entries.append(m.group(1).rstrip("/"))
            elif line and not line.lstrip().startswith("#"):
                break  # next top-level key ends the list
    return entries


def find_excluded_fetches(root: Path) -> list[str]:
    fetched = fetched_data_files((root / "app.js").read_text(encoding="utf-8"))
    # Fail closed: zero matches means the regex stopped matching app.js's style, not
    # that nothing is fetched -- an empty set would make this check pass vacuously.
    assert fetched, "found no data/*.json string literals in app.js -- pattern is stale"
    excluded = excluded_paths((root / "_config.yml").read_text(encoding="utf-8"))
    assert excluded, "found no `exclude:` entries in _config.yml -- parser is stale"
    return sorted(f for f in fetched if any(f == e or f.startswith(e + "/") for e in excluded))


def test_real_repo_serves_every_data_file_app_js_fetches() -> None:
    assert find_excluded_fetches(REPO_ROOT) == []


def test_detects_a_fetched_file_that_is_excluded(tmp_path: Path) -> None:
    # The violation itself, constructed: app.js fetches a file _config.yml excludes.
    (tmp_path / "app.js").write_text('const X_URL = "data/x.json";\n', encoding="utf-8")
    (tmp_path / "_config.yml").write_text(
        "exclude:\n  - docs/\n  - data/x.json\n", encoding="utf-8"
    )
    assert find_excluded_fetches(tmp_path) == ["data/x.json"]


def test_detects_a_fetched_file_under_an_excluded_directory(tmp_path: Path) -> None:
    (tmp_path / "app.js").write_text('fetch("data/feature_store/f.json");\n', encoding="utf-8")
    (tmp_path / "_config.yml").write_text("exclude:\n  - data/feature_store/\n", encoding="utf-8")
    assert find_excluded_fetches(tmp_path) == ["data/feature_store/f.json"]


def test_commented_out_reference_is_not_a_fetch(tmp_path: Path) -> None:
    (tmp_path / "app.js").write_text(
        '// was "data/old.json"\nconst A_URL = "data/a.json";\n', encoding="utf-8"
    )
    (tmp_path / "_config.yml").write_text("exclude:\n  - data/old.json\n", encoding="utf-8")
    assert find_excluded_fetches(tmp_path) == []


def test_empty_app_js_scan_fails_closed(tmp_path: Path) -> None:
    (tmp_path / "app.js").write_text("const nothing = 1;\n", encoding="utf-8")
    (tmp_path / "_config.yml").write_text("exclude:\n  - docs/\n", encoding="utf-8")
    try:
        find_excluded_fetches(tmp_path)
    except AssertionError:
        return
    raise AssertionError("an app.js with no data literals must fail closed, not pass")
