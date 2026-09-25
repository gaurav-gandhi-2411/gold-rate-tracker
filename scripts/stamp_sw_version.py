"""Stamp service-worker.js's VERSION const from a hash of the precached shell files.

PR #2058+ found several open PRs all hand-bumping VERSION to v58/v59 -- every PR that
touches a precached shell file has to bump VERSION (lint.yml's sw-version-guard job
enforces this), and every one of them picks the next free-looking number, so they all
conflict with each other on merge. This script removes the conflict at its root: VERSION
is derived from the *content* of the shell files themselves, computed once at build/deploy
time, never hand-typed in a PR diff.

What it hashes: every file scripts/sw_shell_files.py says service-worker.js precaches
(the same source of truth the guard already uses -- no second copy to drift), read as raw
bytes, sorted by repo-relative path. Each file is framed as
    (8-byte big-endian path-length) + (path bytes, utf-8) +
    (8-byte big-endian content-length) + (content bytes)
before being fed to sha256, so two different (path, content) pairs can never hash to the
same byte stream (a bare concatenation would let "ab"+"c" collide with "a"+"bc"). VERSION
is set to "sh-" + the first 16 hex chars of that digest.

Properties, stated plainly (rule 65a -- no vague "deterministic" claim):
  - Deterministic across runs: hashing the same shell-file bytes on any machine, any run,
    always produces the same digest -- sha256 has no run-to-run entropy and the framing
    has no environment-dependent input (no timestamps, no paths outside the repo root).
  - NOT line-ending-stable: this hashes exact bytes, not normalized text. If a shell
    file's line endings change (LF <-> CRLF) with no other edit, the byte stream differs,
    so the stamped version changes. This is intentional, not a limitation: a client's
    installed cache is keyed off exactly what bytes it fetched, and a line-ending-only
    edit does change those served bytes, so it is correct for it to also change VERSION.

Fails closed (rule 98a): any SHELL_FILES entry that is missing, unreadable, or the
underlying `const SHELL_FILES = [...]` array itself being absent/empty all raise and exit
non-zero, via scripts/sw_shell_files.shell_files()'s own fail-closed checks -- this script
adds no separate silent-pass path on top of that.

Usage:
    python scripts/stamp_sw_version.py            # writes the stamped VERSION line in place
    python scripts/stamp_sw_version.py --check     # prints the version only, writes nothing
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sw_shell_files import ShellFilesError, shell_files

_VERSION_LINE = re.compile(rb'const VERSION = "[^"]*";')


class StampError(Exception):
    """The version could not be computed or written reliably; callers must treat as failure."""


def compute_version(repo_root: Path) -> str:
    """Return "sh-<16 hex chars>" derived from every precached shell file's exact bytes.

    Raises StampError (wrapping ShellFilesError, or on an unreadable file) rather than
    ever falling back to a placeholder -- an unhashable shell file must fail the build,
    never silently produce a version that omits it from the cache-invalidation guarantee.
    """
    try:
        files = shell_files(repo_root)
    except ShellFilesError as exc:
        raise StampError(str(exc)) from exc

    hasher = hashlib.sha256()
    for path in sorted(files):
        try:
            content = (repo_root / path).read_bytes()
        except OSError as exc:
            raise StampError(f"could not read shell file {path!r}: {exc}") from exc
        path_bytes = path.encode("utf-8")
        hasher.update(len(path_bytes).to_bytes(8, "big"))
        hasher.update(path_bytes)
        hasher.update(len(content).to_bytes(8, "big"))
        hasher.update(content)

    return f"sh-{hasher.hexdigest()[:16]}"


def stamp(repo_root: Path, version: str) -> None:
    """Replace the single `const VERSION = "...";` substring in service-worker.js in place.

    Operates on raw bytes and replaces only the matched substring -- not the whole line --
    so the file's existing CRLF line terminators (and everything else) are left
    byte-identical; only the quoted version string changes.
    """
    sw_path = repo_root / "service-worker.js"
    try:
        raw = sw_path.read_bytes()
    except OSError as exc:
        raise StampError(f"could not read service-worker.js: {exc}") from exc

    new_raw, count = _VERSION_LINE.subn(f'const VERSION = "{version}";'.encode(), raw)
    if count != 1:
        raise StampError(
            f'expected exactly one `const VERSION = "...";` in service-worker.js, found {count}'
        )
    sw_path.write_bytes(new_raw)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parent.parent)
    ap.add_argument(
        "--check",
        action="store_true",
        help="print the computed version and exit; do not write service-worker.js",
    )
    args = ap.parse_args()

    try:
        version = compute_version(args.repo_root)
        if not args.check:
            stamp(args.repo_root, version)
    except StampError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    print(version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
