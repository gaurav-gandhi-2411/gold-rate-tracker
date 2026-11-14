"""scripts/pr_hygiene.py: PR secret scan + large-file check (GG decision 2c, 2026-11-13).

Hermetic: every root is tmp_path, gitleaks is a fake executable written to tmp_path, no clock, no
network. The real binary's flags were proven separately (see the PR body).
"""

from __future__ import annotations

import os
import re
import stat
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import pr_hygiene as ph

KB = 1024


def _changes(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "changes.txt"
    p.write_bytes(text.encode("utf-8"))
    return p


def _file(tmp_path: Path, rel: str, size: int) -> None:
    f = tmp_path / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"x" * size)


# --- change-list parsing ------------------------------------------------------------------------
def test_parse_uses_destination_of_renames_and_skips_deletes():
    text = "A\ta.json\nM\tb.json\nR100\told.json\tnew.json\nD\tgone.json\nC75\ts\tcopy.json\n"
    assert ph.parse_changes(text) == ["a.json", "b.json", "new.json", "copy.json"]


def test_parse_handles_crlf_and_blank_lines_and_empty_list():
    assert ph.parse_changes("A\ta.json\r\n\r\n") == ["a.json"]
    assert ph.parse_changes("") == []


@pytest.mark.parametrize("bad", ["garbage line\n", "Z\tfile\n", "A\n", "\tfile\n"])
def test_parse_returns_none_for_a_malformed_line(bad):
    assert ph.parse_changes(bad) is None


def test_parse_unquotes_gits_c_style_paths():
    assert ph.parse_changes('A\t"caf\\303\\251.json"\n') == ["café.json"]
    assert ph.parse_changes('A\t"a\\tb.json"\n') == ["a\tb.json"]


# --- size check ---------------------------------------------------------------------------------
def test_size_over_at_and_under_the_limit(tmp_path):
    _file(tmp_path, "over.bin", 2 * KB + 1)
    _file(tmp_path, "at.bin", 2 * KB)
    _file(tmp_path, "under.bin", 2 * KB - 1)
    ch = _changes(tmp_path, "A\tover.bin\nM\tat.bin\nA\tunder.bin\n")
    status, findings = ph.check_large_files(ch, tmp_path, 2)
    assert status == "1 over limit"
    assert len(findings) == 1 and "over.bin" in findings[0]


def test_deleted_and_missing_files_are_not_findings(tmp_path):
    _file(tmp_path, "big.bin", 5 * KB)  # on disk, but the change list says deleted
    ch = _changes(tmp_path, "D\tbig.bin\nA\tnot_on_disk.bin\n")
    assert ph.check_large_files(ch, tmp_path, 1) == (ph.OK, [])


def test_a_renamed_file_is_sized_at_its_new_path(tmp_path):
    _file(tmp_path, "new.bin", 5 * KB)
    ch = _changes(tmp_path, "R090\told.bin\tnew.bin\n")
    status, findings = ph.check_large_files(ch, tmp_path, 1)
    assert status == "1 over limit" and "new.bin" in findings[0]


def test_unreadable_or_malformed_change_list_is_could_not_verify_not_ok(tmp_path):
    assert ph.check_large_files(tmp_path / "missing.txt", tmp_path, 1)[0] == ph.COULD_NOT_VERIFY
    assert ph.check_large_files(tmp_path, tmp_path, 1)[0] == ph.COULD_NOT_VERIFY  # a directory
    bad = _changes(tmp_path, "not a name-status line\n")
    assert ph.check_large_files(bad, tmp_path, 1)[0] == ph.COULD_NOT_VERIFY
    nonutf = tmp_path / "nonutf.txt"
    nonutf.write_bytes(b"A\t\xff\xfe\n")
    assert ph.check_large_files(nonutf, tmp_path, 1)[0] == ph.COULD_NOT_VERIFY


def test_an_empty_change_list_is_ok(tmp_path):
    assert ph.check_large_files(_changes(tmp_path, ""), tmp_path, 1) == (ph.OK, [])


# --- gitleaks runner, with a fake executable ----------------------------------------------------
FAKE_PY = """\
import json, os, sys, time
args = sys.argv[1:]
open(os.environ["FAKE_ARGS_LOG"], "w").write(json.dumps(args))
rc = int(os.environ["FAKE_RC"])
if rc == 99:
    time.sleep(30)
if "FAKE_REPORT" in os.environ:
    path = args[args.index("--report-path") + 1]
    open(path, "w").write(os.environ["FAKE_REPORT"])
print("SECRET-IN-STDOUT-" + "must-never-be-echoed")
sys.exit(rc)
"""


def _fake_gitleaks(tmp_path: Path) -> str:
    script = tmp_path / "fake_gitleaks.py"
    script.write_text(FAKE_PY, encoding="utf-8")
    if os.name == "nt":
        exe = tmp_path / "fake_gitleaks.bat"
        exe.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        exe = tmp_path / "fake_gitleaks"
        exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    return str(exe)


@pytest.fixture
def fake(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_ARGS_LOG", str(tmp_path / "args.json"))
    monkeypatch.delenv("FAKE_REPORT", raising=False)
    return _fake_gitleaks(tmp_path)


def test_gitleaks_exit_0_is_ok_and_the_invocation_is_the_pinned_shape(tmp_path, fake, monkeypatch):
    monkeypatch.setenv("FAKE_RC", "0")
    assert ph.run_gitleaks(fake, "base..head", tmp_path, 60) == (ph.OK, [])
    import json

    args = json.loads((tmp_path / "args.json").read_text(encoding="utf-8"))
    assert args[:3] == ["git", "--no-banner", "--redact"]
    assert "--log-opts=base..head" in args
    assert args[args.index("--report-format") + 1] == "json"


def test_gitleaks_exit_1_is_found_with_rule_file_line_and_no_secret(
    tmp_path, fake, monkeypatch, capsys
):
    monkeypatch.setenv("FAKE_RC", "1")
    monkeypatch.setenv(
        "FAKE_REPORT",
        '[{"RuleID":"github-pat","File":"a.txt","StartLine":3,"Secret":"REAL-SECRET-VALUE",'
        '"Match":"REAL-SECRET-VALUE"},{"RuleID":"aws-access-token","File":"b.txt","StartLine":9}]',
    )
    status, findings = ph.run_gitleaks(fake, "b..h", tmp_path, 60)
    assert status == "FOUND 2"
    assert findings == [
        "secret: rule=github-pat file=a.txt line=3",
        "secret: rule=aws-access-token file=b.txt line=9",
    ]
    assert not any("REAL-SECRET-VALUE" in f for f in findings)
    ch = _changes(tmp_path, "")
    ph.main(["--changes", str(ch), "--range", "b..h", "--gitleaks", fake, "--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert "REAL-SECRET-VALUE" not in out and "SECRET-IN-STDOUT" not in out
    assert "PR-HYGIENE: secret_scan=FOUND 2 large_files=ok mode=report" in out


def test_gitleaks_exit_1_with_an_unreadable_report_is_still_found(tmp_path, fake, monkeypatch):
    monkeypatch.setenv("FAKE_RC", "1")
    monkeypatch.setenv("FAKE_REPORT", "not json")
    assert ph.run_gitleaks(fake, "b..h", tmp_path, 60)[0] == "FOUND ?"


@pytest.mark.parametrize("rc", ["2", "126", "3"])
def test_gitleaks_other_nonzero_is_could_not_verify(tmp_path, fake, monkeypatch, rc):
    monkeypatch.setenv("FAKE_RC", rc)
    assert ph.run_gitleaks(fake, "b..h", tmp_path, 60) == (ph.COULD_NOT_VERIFY, [])


def test_gitleaks_timeout_is_could_not_verify(tmp_path, fake, monkeypatch):
    monkeypatch.setenv("FAKE_RC", "99")  # the fake sleeps 30 s
    assert ph.run_gitleaks(fake, "b..h", tmp_path, 1) == (ph.COULD_NOT_VERIFY, [])


def test_gitleaks_missing_binary_is_could_not_verify_and_no_binary_is_skipped(tmp_path):
    assert ph.run_gitleaks(str(tmp_path / "nope"), "b..h", tmp_path, 5)[0] == ph.COULD_NOT_VERIFY
    assert ph.run_gitleaks("", "b..h", tmp_path, 5) == (ph.SKIPPED, [])


# --- mode, exit code, output line ---------------------------------------------------------------
def test_mode_is_pinned_to_report_until_the_flip_pr():
    assert ph.MODE == "report"


def _run(tmp_path: Path, capsys, *extra: str, mode: str | None = None) -> tuple[int, str]:
    ch = _changes(tmp_path, "A\tbig.bin\n")
    _file(tmp_path, "big.bin", 3 * KB)
    rc = ph.main(["--changes", str(ch), "--root", str(tmp_path), *extra], mode=mode)
    return rc, capsys.readouterr().out


def test_report_mode_exits_0_even_with_findings_and_enforce_exits_1(tmp_path, capsys):
    rc, out = _run(tmp_path, capsys, "--max-kb", "1")
    assert rc == 0
    assert "PR-HYGIENE: secret_scan=skipped large_files=1 over limit mode=report" in out
    rc, out = _run(tmp_path, capsys, "--max-kb", "1", mode="enforce")
    assert rc == 1 and out.rstrip().endswith("mode=enforce")


def test_enforce_exits_0_when_clean_and_1_when_could_not_verify(
    tmp_path, capsys, fake, monkeypatch
):
    rc, out = _run(tmp_path, capsys, "--max-kb", "10", mode="enforce")
    assert rc == 0 and "secret_scan=skipped large_files=ok mode=enforce" in out
    monkeypatch.setenv("FAKE_RC", "2")
    rc, out = _run(
        tmp_path, capsys, "--max-kb", "10", "--gitleaks", fake, "--range", "a..b", mode="enforce"
    )
    assert rc == 1 and "secret_scan=could-not-verify" in out


def test_a_scan_with_a_binary_but_no_range_is_could_not_verify(tmp_path, capsys, fake):
    rc, out = _run(tmp_path, capsys, "--gitleaks", fake)
    assert rc == 0 and "secret_scan=could-not-verify" in out


def test_the_summary_line_format_is_greppable(tmp_path, capsys):
    _, out = _run(tmp_path, capsys)
    line = [x for x in out.splitlines() if x.startswith("PR-HYGIENE:")]
    assert len(line) == 1
    assert re.fullmatch(
        r"PR-HYGIENE: secret_scan=(ok|FOUND [0-9?]+|could-not-verify|skipped) "
        r"large_files=(ok|[0-9]+ over limit|could-not-verify) mode=(report|enforce)",
        line[0],
    )


def test_github_actions_warning_is_printed_only_when_something_is_not_ok(
    tmp_path, capsys, monkeypatch
):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    _, out = _run(tmp_path, capsys)  # secret scan skipped
    assert "::warning title=pr-hygiene::" in out
    monkeypatch.delenv("GITHUB_ACTIONS")
    _, out = _run(tmp_path, capsys)
    assert "::warning" not in out


# --- the workflow step --------------------------------------------------------------------------
def _hygiene_run() -> str:
    wf = yaml.safe_load((ROOT / ".github/workflows/lint.yml").read_text(encoding="utf-8"))
    step = next(
        s for s in wf["jobs"]["lint"]["steps"] if str(s.get("name", "")).startswith("PR hygiene")
    )
    return str(step["run"])


def test_the_workflow_downloads_the_pinned_release_and_verifies_the_committed_checksum_first():
    run = _hygiene_run()
    assert "releases/download/v${V}/" in run and "V=8.30.1" in run
    assert "latest" not in run and "| sh" not in run and "| bash" not in run
    assert run.index("sha256sum -c") < run.index("tar -xzf") < run.index("pr_hygiene.py")
    # a failed download/verification must not leave a runnable binary path
    assert 'BIN=""' in run and run.index('BIN=""') < run.index('BIN="$D/gitleaks"')


def test_the_committed_checksum_names_the_pinned_tarball():
    line = (ROOT / "scripts/pr_hygiene_gitleaks.sha256").read_text(encoding="utf-8").strip()
    assert re.fullmatch(r"[0-9a-f]{64}  gitleaks_8\.30\.1_linux_x64\.tar\.gz", line)


def test_the_pre_commit_gitleaks_rev_matches_the_pinned_release():
    cfg = (ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    assert "rev: v8.30.1" in cfg
