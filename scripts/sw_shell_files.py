"""Print the files service-worker.js precaches, one repo-relative path per line.

lint.yml's sw-version-guard job needs to know which files a client's installed service
worker serves cache-first, because changing any of them without bumping VERSION freezes
installed clients on the old copy. That set used to be a hand-typed regex in lint.yml
(index.html, app.js, style.css, manifest.webmanifest) which had already drifted:
i18n.js is in SHELL_FILES but was never in the regex. Reading SHELL_FILES itself removes
the second copy.

Fails closed (non-zero exit, message on stderr) if SHELL_FILES cannot be found, is empty,
or names a file that does not exist -- an empty result would make the guard's `grep -f`
match nothing and silently pass every PR.

Usage:  python scripts/sw_shell_files.py [--repo-root PATH]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_ARRAY = re.compile(r"const SHELL_FILES = \[(.*?)\];", re.S)
_ENTRY = re.compile(r'"\./([^"]+)"')


class ShellFilesError(Exception):
    """The precache list could not be read reliably; callers must treat this as a failure."""


def shell_files(repo_root: Path) -> list[str]:
    text = (repo_root / "service-worker.js").read_text(encoding="utf-8")
    m = _ARRAY.search(text)
    if not m:
        raise ShellFilesError("service-worker.js: no `const SHELL_FILES = [...]` array found")
    # Comments inside the array mention fonts by path; only quoted entries count.
    body = re.sub(r"//[^\n]*", "", m.group(1))
    files = _ENTRY.findall(body)  # the bare "./" root entry has no path after the slash
    if not files:
        raise ShellFilesError("SHELL_FILES parsed to zero files -- the parser is stale")
    missing = [f for f in files if not (repo_root / f).is_file()]
    if missing:
        raise ShellFilesError(f"SHELL_FILES names files that do not exist: {missing}")
    return files


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parent.parent)
    args = ap.parse_args()
    try:
        files = shell_files(args.repo_root)
    except ShellFilesError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    # Bytes, not print(): Windows text-mode stdout writes \r\n, and the guard's `grep -x`
    # never matches a line that ends in \r -- the output must be identical on every OS.
    sys.stdout.buffer.write(("\n".join(files) + "\n").encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
