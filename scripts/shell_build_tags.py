"""Build tags on the shell's own scripts and stylesheets: `app.js?v=<service-worker VERSION>`.

Why (2026-10-12): the service worker serves the page network-first so a deploy reaches the next
visit, but a slow network could then pair a cached index.html with a freshly fetched app.js (or the
reverse). A page and its scripts must always come from one build. Every local <script src> and
<link href> of index.html and how-we-know.html therefore carries `?v=<VERSION>`, the same string as
`const VERSION` in service-worker.js. The worker uses it: a request tagged with ITS OWN version is
answered from its cache (the atomic set it installed, matching the page it came with); a request
tagged with another version goes to the network only (that page is from a different build, so a
cached copy would be a mismatch). The query string also keeps the browser HTTP cache from handing an
old build's file to a new tag. The worker's cache key ignores the query, so there is still one entry
per file.

This module is the single source of truth for finding, checking and rewriting those tags. It is used
by scripts/check_sw_version_guard.py (tags == VERSION on every PR), scripts/stamp_sw_version.py (the
build-time version from #2068: the hash ignores the tag values, then the tags are stamped) and
scripts/bump_sw_version.py (legacy mode: one command bumps VERSION and every tag).
"""

from __future__ import annotations

import re
from pathlib import Path

TAGGED_PAGES = ("index.html", "how-we-know.html")

# A local script or stylesheet reference: no scheme, no slash, ends in .js or .css.
_REF = re.compile(r'((?:src|href)=")([A-Za-z0-9_.-]+\.(?:js|css))(\?v=([^"&]*))?(")')
_VERSION_CONST = re.compile(rb'const VERSION = "([^"]*)";')


class TagError(Exception):
    """The tags could not be read or are inconsistent; callers must treat this as a failure."""


def refs(text: str) -> list[tuple[str, str | None]]:
    """Every local js/css reference as (path, tag-or-None), in document order."""
    return [(m.group(2), m.group(4)) for m in _REF.finditer(text)]


def normalize(text: str) -> str:
    """The text with every tag value blanked: what the build-time hash is computed over."""
    return _REF.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{'?v=' if m.group(3) else ''}{m.group(5)}", text
    )


def set_tags(text: str, version: str) -> str:
    return _REF.sub(lambda m: f"{m.group(1)}{m.group(2)}?v={version}{m.group(5)}", text)


def sw_version(repo_root: Path) -> str:
    raw = (repo_root / "service-worker.js").read_bytes()
    found = _VERSION_CONST.findall(raw)
    if len(found) != 1:
        raise TagError(
            f'expected one `const VERSION = "...";` in service-worker.js, found {len(found)}'
        )
    return found[0].decode("utf-8")


def problems(repo_root: Path) -> list[str]:
    """Human-readable problems; an empty list means every tag equals the worker's VERSION."""
    version = sw_version(repo_root)
    out: list[str] = []
    for page in TAGGED_PAGES:
        page_path = repo_root / page
        if not page_path.is_file():
            continue  # a minimal fixture repo; the real repo is pinned by tests/test_shell_build_tags.py
        found = refs(page_path.read_text(encoding="utf-8"))
        for path, tag in found:
            if tag != version:
                out.append(
                    f"{page}: {path} is tagged {tag!r}, service-worker.js VERSION is {version!r}"
                )
    return out


def rewrite(repo_root: Path, version: str) -> None:
    """Set every tag in the tagged pages to `version`, byte-preserving everything else."""
    for page in TAGGED_PAGES:
        p = repo_root / page
        raw = p.read_bytes().decode("utf-8")
        new = set_tags(raw, version)
        if not refs(new):
            raise TagError(f"{page}: no local script/stylesheet references found")
        p.write_bytes(new.encode("utf-8"))
