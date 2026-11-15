"""PR hygiene: a secret scan of the PR's own commits and a size check of the files it changes.

Why this exists (GG decision 2c, 2026-11-13): a data-only PR skips `pre-commit run --all-files`, so
gitleaks and check-added-large-files stopped running on it. Worse, the pre-commit gitleaks hook's
entry is `gitleaks git --pre-commit --redact --staged --verbose`; a CI checkout has nothing staged,
so even on full PRs that hook scanned nothing. This script scans the commits in a range instead.

Checks:
  * large files: every added / modified / renamed / copied path in a `git diff --name-status` file
    that exists on disk and is larger than --max-kb (os.stat only; contents are never read);
  * secret scan: the pinned gitleaks binary over `--range` (`git --log-opts=<range>`), JSON report
    to a temp file, only rule id / file / line printed (never a secret, and gitleaks runs with
    --redact as well). Exit 1 = leaks found; any other non-zero, a timeout, an unreadable report or
    an unstartable binary = could-not-verify, never ok. No binary path = skipped, never ok.

Output: one machine-greppable line
  PR-HYGIENE: secret_scan=<ok|FOUND n|could-not-verify|skipped> large_files=<ok|n over limit|
  could-not-verify> mode=<report|enforce>

MODE is 'report' (exit 0 always) until 5 clean real runs spanning >= 24 h (.claude/CLAUDE.md,
2026-10-30); flipping it to 'enforce' (exit 1 unless both checks are ok or skipped) is a separate
PR that edits tests/test_pr_hygiene.py deliberately.

Limits: only commits in the range are scanned (history before it is not); a path git prints with
non-ASCII characters is unquoted from git's C-style form; a file that is in the change list but
deleted by the end of the range is ignored (nothing to commit-size).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

MODE = "report"  # flip to "enforce" only in a PR that cites 5 clean real runs over >= 24 h
DEFAULT_MAX_KB = 10240  # matches the check-added-large-files hook (--maxkb=10240)
DEFAULT_TIMEOUT_S = 300
OK = "ok"
COULD_NOT_VERIFY = "could-not-verify"
SKIPPED = "skipped"
_SIZED_STATUSES = ("A", "M", "R", "C")  # added, modified, renamed, copied; D/T/U/X are not sized


def _unquote(path: str) -> str:
    """Undo git's C-style quoting of a path (core.quotePath), e.g. "\\303\\251" -> a real char."""
    if not (len(path) >= 2 and path.startswith('"') and path.endswith('"')):
        return path
    body = path[1:-1]
    out = bytearray()
    i = 0
    simple = {"n": 10, "t": 9, '"': 34, "\\": 92}
    while i < len(body):
        c = body[i]
        if c == "\\" and i + 1 < len(body):
            nxt = body[i + 1]
            if nxt in simple:
                out.append(simple[nxt])
                i += 2
                continue
            if body[i + 1 : i + 4].isdigit() and len(body[i + 1 : i + 4]) == 3:
                out.append(int(body[i + 1 : i + 4], 8))
                i += 4
                continue
        out += c.encode("utf-8")
        i += 1
    return out.decode("utf-8", errors="replace")


def parse_changes(text: str) -> list[str] | None:
    """Paths to size-check from `git diff --name-status` text, or None if a line is malformed.

    The destination path is used for renames and copies. A malformed line makes the whole list
    unverifiable (None) rather than silently skipping it.
    """
    paths: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip("\r")
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 2 or not parts[0] or not parts[-1]:
            return None
        status = parts[0][0]
        if status in _SIZED_STATUSES:
            paths.append(_unquote(parts[-1]))
        elif status not in "DTUX":
            return None
    return paths


def check_large_files(changes_file: Path, root: Path, max_kb: int) -> tuple[str, list[str]]:
    """Return (status, findings); status is 'ok', '<n> over limit' or could-not-verify."""
    try:
        text = changes_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return COULD_NOT_VERIFY, []
    paths = parse_changes(text)
    if paths is None:
        return COULD_NOT_VERIFY, []
    limit = max_kb * 1024
    findings: list[str] = []
    for rel in paths:
        try:
            size = (root / rel).stat().st_size
        except FileNotFoundError:
            continue  # deleted later in the range: nothing to size
        except OSError:
            return COULD_NOT_VERIFY, findings
        if size > limit:
            findings.append(f"large file: {rel} is {size} bytes (limit {limit})")
    return (f"{len(findings)} over limit" if findings else OK), findings


def run_gitleaks(binary: str, rev_range: str, root: Path, timeout_s: int) -> tuple[str, list[str]]:
    """Run gitleaks over a commit range; return (status, findings).

    Findings carry rule id, file and line only. Secrets are never printed (--redact, and the
    report's Secret/Match fields are not read).
    """
    if not binary:
        return SKIPPED, []
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "report.json"
        cmd = [
            binary,
            "git",
            "--no-banner",
            "--redact",
            f"--log-opts={rev_range}",
            "--report-format",
            "json",
            "--report-path",
            str(report),
            str(root),
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=timeout_s, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return COULD_NOT_VERIFY, []
        if proc.returncode == 0:
            return OK, []
        if proc.returncode != 1:
            return COULD_NOT_VERIFY, []
        try:
            leaks = json.loads(report.read_text(encoding="utf-8"))
            findings = [
                f"secret: rule={x.get('RuleID')} file={x.get('File')} line={x.get('StartLine')}"
                for x in leaks
            ]
        except (OSError, ValueError, TypeError, AttributeError):
            return "FOUND ?", ["secret: gitleaks reported leaks but its report was unreadable"]
        return (f"FOUND {len(findings)}" if findings else COULD_NOT_VERIFY), findings


def main(argv: list[str] | None = None, mode: str | None = None) -> int:
    """Run both checks, print the summary line, return the exit code for the mode."""
    mode = MODE if mode is None else mode
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--changes", required=True, help="git diff --name-status output file")
    ap.add_argument("--range", default="", dest="rev_range", help="git rev range for gitleaks")
    ap.add_argument("--gitleaks", default="", help="gitleaks binary; empty skips the scan")
    ap.add_argument("--max-kb", type=int, default=DEFAULT_MAX_KB)
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S)
    ap.add_argument("--root", default=".", help="repository root (paths in --changes are relative)")
    args = ap.parse_args(argv)
    root = Path(args.root)

    large, large_findings = check_large_files(Path(args.changes), root, args.max_kb)
    if args.gitleaks and not args.rev_range:
        secret, secret_findings = COULD_NOT_VERIFY, []
    else:
        secret, secret_findings = run_gitleaks(args.gitleaks, args.rev_range, root, args.timeout)
    for f in secret_findings + large_findings:
        print(f)
    print(f"PR-HYGIENE: secret_scan={secret} large_files={large} mode={mode}")
    clean = secret in (OK, SKIPPED) and large == OK
    if (secret != OK or large != OK) and os.environ.get("GITHUB_ACTIONS") == "true":
        print(
            f"::warning title=pr-hygiene::secret_scan={secret} large_files={large} "
            f"(mode={mode}); read the step log"
        )
    return 1 if (mode == "enforce" and not clean) else 0


if __name__ == "__main__":
    sys.exit(main())
