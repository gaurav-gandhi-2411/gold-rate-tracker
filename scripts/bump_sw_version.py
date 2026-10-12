"""Legacy Pages mode: bump service-worker.js VERSION and every build tag in one command.

    python scripts/bump_sw_version.py v95-20261015-short-name

The tags in index.html / how-we-know.html must equal VERSION (scripts/shell_build_tags.py; the
sw-version-guard job fails otherwise), so a hand edit of VERSION alone is no longer enough. In
actions mode (PAGES_BUILD_MODE == "actions") VERSION and the tags are stamped at build time by
scripts/stamp_sw_version.py and this script is not used.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shell_build_tags as tags

_SAFE = re.compile(r"^[A-Za-z0-9._-]+$")
_VERSION_CONST = re.compile(rb'const VERSION = "[^"]*";')


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("version")
    ap.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parent.parent)
    args = ap.parse_args()
    if not _SAFE.match(args.version):
        print("FAIL: version may only contain letters, digits, '.', '_' and '-'", file=sys.stderr)
        return 1
    sw = args.repo_root / "service-worker.js"
    raw = sw.read_bytes()
    new, n = _VERSION_CONST.subn(f'const VERSION = "{args.version}";'.encode(), raw)
    if n != 1:
        print(f"FAIL: expected one VERSION const in service-worker.js, found {n}", file=sys.stderr)
        return 1
    try:
        tags.rewrite(args.repo_root, args.version)
    except tags.TagError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    sw.write_bytes(new)
    print(f"VERSION and {len(tags.TAGGED_PAGES)} pages' tags set to {args.version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
