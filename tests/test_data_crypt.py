"""Tests for scripts/data_crypt.py (ADR 060). A throwaway git repo per test, a known test key."""

from __future__ import annotations

import base64
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "data_crypt.py"
_spec = importlib.util.spec_from_file_location("data_crypt", _SCRIPT)
assert _spec and _spec.loader
dc = importlib.util.module_from_spec(_spec)
sys.modules["data_crypt"] = dc
_spec.loader.exec_module(dc)

TEST_KEY = "test-key-NOT-THE-REAL-ONE-3f9c2a7d41b8e6"  # >= 16 chars, fixed for leak scanning
OTHER_KEY = "a-different-test-key-0123456789abcdef"
IBJA = "data/ibja_rates.parquet"
GCF = "reports/vol_regime_prereg_gcf_2000_2012.csv"


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A git repo with the real .gitignore rules for every registered path."""
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    ignore = "\n".join(f"/{p}" for p in dc.REGISTRY) + f"\n/{dc.STATE_FILE}\n"
    (tmp_path / ".gitignore").write_text(ignore, encoding="utf-8")
    return tmp_path


@pytest.fixture
def key_env(monkeypatch: pytest.MonkeyPatch) -> bytes:
    monkeypatch.setenv(dc.KEY_ENV, TEST_KEY)
    return TEST_KEY.encode()


def _put(root: Path, logical: str, data: bytes) -> Path:
    path = root / logical
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


@dataclass
class Result:
    returncode: int
    stdout: str
    stderr: str


def _cli(root: Path, *args: str, key: str | None = TEST_KEY) -> Result:
    """The CLI in-process (same code path as `python scripts/data_crypt.py`), with its own
    environment and captured stdout/stderr. In-process so the cheap-scrypt fixture applies."""
    saved = os.environ.get(dc.KEY_ENV)
    if key is None:
        os.environ.pop(dc.KEY_ENV, None)
    else:
        os.environ[dc.KEY_ENV] = key
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = dc.main(["--root", str(root), *args])
    finally:
        if saved is None:
            os.environ.pop(dc.KEY_ENV, None)
        else:
            os.environ[dc.KEY_ENV] = saved
    return Result(code, out.getvalue(), err.getvalue())


def _subprocess_cli(root: Path, *args: str, key: str | None) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != dc.KEY_ENV}
    if key is not None:
        env[dc.KEY_ENV] = key
    return subprocess.run(
        [sys.executable, str(_SCRIPT), "--root", str(root), *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


@pytest.fixture(autouse=True)
def cheap_scrypt(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Production scrypt costs ~0.1-0.7 s per call; tests use n=2**10 unless marked real_kdf.
    The header records n and decryption checks it, so both sides see the same value."""
    if "real_kdf" not in request.keywords:
        monkeypatch.setattr(dc, "SCRYPT_N", 2**10)
    dc.key_id.cache_clear()
    yield
    dc.key_id.cache_clear()


# ---------------------------------------------------------------- format


@pytest.mark.real_kdf
def test_round_trip_and_header_binds_path() -> None:
    key = TEST_KEY.encode()
    data = os.urandom(5000)
    blob = dc.encrypt_bytes(data, IBJA, key)
    assert dc.decrypt_bytes(blob, IBJA, key) == data
    header, aad, _ = dc.parse_header(blob)
    assert header["path"] == IBJA and header["schema_version"] == 1
    assert len(base64.b64decode(header["nonce"])) == 12  # 96-bit nonce
    assert blob[: len(aad)] == aad
    with pytest.raises(dc.CryptError, match="not 'data/fusion_snapshots.parquet'"):
        dc.decrypt_bytes(blob, "data/fusion_snapshots.parquet", key)


def test_fresh_nonce_and_salt_every_time() -> None:
    key = TEST_KEY.encode()
    a = dc.encrypt_bytes(b"same", IBJA, key)
    b = dc.encrypt_bytes(b"same", IBJA, key)
    assert a != b
    assert dc.parse_header(a)[0]["nonce"] != dc.parse_header(b)[0]["nonce"]


@pytest.mark.parametrize("where", ["header", "ciphertext", "tag"])
def test_any_flipped_byte_fails_authentication(where: str) -> None:
    key = TEST_KEY.encode()
    blob = bytearray(dc.encrypt_bytes(b"x" * 300, IBJA, key))
    _, aad, _ = dc.parse_header(bytes(blob))
    # header: flip a byte inside the base64 salt, which leaves the JSON parseable
    idx = {
        "header": bytes(blob).index(b'"salt":"') + 9,
        "ciphertext": len(aad) + 5,
        "tag": len(blob) - 1,
    }[where]
    blob[idx] ^= 0x01
    with pytest.raises(dc.CryptError):
        dc.decrypt_bytes(bytes(blob), IBJA, key)


def test_swapping_the_path_in_the_header_fails_authentication() -> None:
    key = TEST_KEY.encode()
    blob = dc.encrypt_bytes(b"secret rows", IBJA, key)
    # same length path so the header length field stays valid
    forged = blob.replace(IBJA.encode(), b"data/ibja_rates.parqueX")
    with pytest.raises(dc.CryptError):
        dc.decrypt_bytes(forged, "data/ibja_rates.parqueX", key)


def test_wrong_key_is_reported_as_wrong_key() -> None:
    blob = dc.encrypt_bytes(b"rows", IBJA, TEST_KEY.encode())
    with pytest.raises(dc.CryptError, match="wrong key"):
        dc.decrypt_bytes(blob, IBJA, OTHER_KEY.encode())


# ---------------------------------------------------------------- files


def test_cli_full_round_trip_every_registered_path(repo: Path) -> None:
    originals = {lp: os.urandom(1000) + lp.encode() for lp in dc.REGISTRY if "frozen_sha256" not in dc.REGISTRY[lp]}
    for lp, data in originals.items():
        _put(repo, lp, data)
    r = _cli(repo, "encrypt", *originals)
    assert r.returncode == 0, r.stderr
    for lp, data in originals.items():
        meta = json.loads((repo / dc.ENC_DIR / f"{lp}.sha256.json").read_text())
        assert meta["plaintext_sha256"] == dc._sha256(data)
        (repo / lp).unlink()
    (repo / dc.STATE_FILE).unlink()
    r = _cli(repo, "decrypt", "--all")
    assert r.returncode == 0, r.stderr
    for lp, data in originals.items():
        assert (repo / lp).read_bytes() == data
    assert _cli(repo, "verify", "--all").returncode == 0
    assert _cli(repo, "verify", "--all", "--hash-only", key=None).returncode == 0
    assert _cli(repo, "guard", key=None).returncode == 0


def test_tampered_ciphertext_exits_nonzero_and_writes_no_plaintext(repo: Path, key_env: bytes) -> None:
    _put(repo, IBJA, b"original rows")
    p = dc.Paths(repo)
    assert dc.encrypt_one(p, IBJA, key_env) == "encrypted"
    (repo / IBJA).unlink()
    enc = p.enc(IBJA)
    blob = bytearray(enc.read_bytes())
    blob[-20] ^= 0xFF
    enc.write_bytes(bytes(blob))
    meta = json.loads(p.meta(IBJA).read_text())
    meta["ciphertext_sha256"] = dc._sha256(bytes(blob))  # attacker also fixes the public hash
    p.meta(IBJA).write_text(json.dumps(meta))
    r = _cli(repo, "decrypt", IBJA)
    assert r.returncode == 1
    assert "authentication failed" in r.stderr
    assert not (repo / IBJA).exists()
    assert not list((repo / "data").glob(".*.tmp"))


def test_tampered_ciphertext_does_not_overwrite_existing_plaintext(repo: Path, key_env: bytes) -> None:
    _put(repo, IBJA, b"good")
    p = dc.Paths(repo)
    dc.encrypt_one(p, IBJA, key_env)
    enc = p.enc(IBJA)
    enc.write_bytes(enc.read_bytes()[:-1] + b"\x00")
    (repo / IBJA).write_bytes(b"local copy")
    assert _cli(repo, "decrypt", IBJA).returncode == 1
    assert (repo / IBJA).read_bytes() == b"local copy"


def test_wrong_key_cli_fails_cleanly(repo: Path, key_env: bytes) -> None:
    _put(repo, IBJA, b"rows")
    dc.encrypt_one(dc.Paths(repo), IBJA, key_env)
    (repo / IBJA).unlink()
    r = _cli(repo, "decrypt", IBJA, key=OTHER_KEY)
    assert r.returncode == 1
    assert "wrong key" in r.stderr
    assert not (repo / IBJA).exists()


def test_missing_or_short_key_fails(repo: Path, key_env: bytes) -> None:
    _put(repo, IBJA, b"rows")
    dc.encrypt_one(dc.Paths(repo), IBJA, key_env)
    assert "not set" in _cli(repo, "decrypt", IBJA, key=None).stderr
    r = _cli(repo, "decrypt", IBJA, key="short")
    assert r.returncode == 1 and "shorter than" in r.stderr


def test_nothing_migrated_is_a_keyless_noop(repo: Path) -> None:
    _put(repo, IBJA, b"rows")
    _git(repo, "add", "-f", IBJA)
    r = _cli(repo, "decrypt", "--all", key=None)
    assert r.returncode == 0 and "nothing migrated" in r.stdout
    r = _cli(repo, "encrypt", IBJA, key=None)
    assert r.returncode == 0 and "pre-migration" in r.stdout


def test_unchanged_plaintext_is_not_re_encrypted(repo: Path, key_env: bytes) -> None:
    _put(repo, IBJA, b"rows")
    p = dc.Paths(repo)
    dc.encrypt_one(p, IBJA, key_env)
    before = p.enc(IBJA).read_bytes()
    assert dc.encrypt_one(p, IBJA, key_env) == "unchanged"
    assert p.enc(IBJA).read_bytes() == before  # no ciphertext churn in the bot PRs


def test_replacing_history_without_decrypt_is_refused(repo: Path, key_env: bytes) -> None:
    p = dc.Paths(repo)
    _put(repo, IBJA, b"300 days of history")
    dc.encrypt_one(p, IBJA, key_env)
    p.state().unlink()  # a fresh CI workspace where the decrypt step did not run
    _put(repo, IBJA, b"today only")
    with pytest.raises(dc.CryptError, match="refusing to replace"):
        dc.encrypt_one(p, IBJA, key_env)
    # after a real decrypt the pipeline may append and re-encrypt
    assert dc.decrypt_one(p, IBJA, key_env) == "decrypted"
    _put(repo, IBJA, b"300 days of history + today")
    assert dc.encrypt_one(p, IBJA, key_env) == "encrypted"
    assert dc.encrypt_one(p, IBJA, key_env) == "unchanged"


def test_frozen_snapshot_must_match_its_pin(repo: Path, key_env: bytes) -> None:
    _put(repo, GCF, b"date,close\n2000-08-30,273.9\n")
    with pytest.raises(dc.CryptError, match="frozen snapshot"):
        dc.encrypt_one(dc.Paths(repo), GCF, key_env)


def test_migrate_encrypts_and_untracks(repo: Path, key_env: bytes) -> None:
    data = b"ibja rows"
    _put(repo, IBJA, data)
    _put(repo, "data/shadow_fusion_output.json", b"{}")
    _git(repo, "add", "-f", IBJA, "data/shadow_fusion_output.json")
    _git(repo, "commit", "-q", "-m", "seed")
    assert dc.main(["--root", str(repo), "migrate"]) == 0
    tracked = subprocess.run(["git", "ls-files"], cwd=repo, capture_output=True, text=True).stdout
    assert IBJA not in tracked and "shadow_fusion_output.json" not in tracked.splitlines()
    (repo / IBJA).unlink()
    assert dc.main(["--root", str(repo), "decrypt", IBJA]) == 0
    assert (repo / IBJA).read_bytes() == data
    _git(repo, "add", "data/encrypted")
    assert dc.guard(dc.Paths(repo)) == []


def test_guard_catches_tracked_plaintext_unignored_and_stray_files(repo: Path, key_env: bytes) -> None:
    p = dc.Paths(repo)
    _put(repo, IBJA, b"rows")
    dc.encrypt_one(p, IBJA, key_env)
    _git(repo, "add", "-f", IBJA)
    (repo / dc.ENC_DIR / "stray.csv").write_text("x")
    (repo / ".gitignore").write_text("", encoding="utf-8")
    problems = "\n".join(dc.guard(p))
    assert "tracked by git although it has ciphertext" in problems
    assert "not gitignored" in problems
    assert "stray.csv: unexpected file" in problems


def test_guard_catches_ciphertext_not_matching_manifest(repo: Path, key_env: bytes) -> None:
    p = dc.Paths(repo)
    _put(repo, IBJA, b"rows")
    dc.encrypt_one(p, IBJA, key_env)
    p.enc(IBJA).write_bytes(p.enc(IBJA).read_bytes() + b"x")
    assert any("not matching its manifest" in m for m in dc.guard(p))


# ---------------------------------------------------------------- key leak


def test_key_forms_cover_encodings() -> None:
    key = b"abcdefghijklmnopqrstuvwxyz012345"
    forms = dc.key_forms(key)
    for form in (key, base64.b64encode(key), key.hex().encode(), base64.urlsafe_b64encode(key)):
        assert form in forms
    hexkey = os.urandom(32).hex().encode()  # a hex-encoded secret: its raw bytes count too
    assert base64.b64encode(bytes.fromhex(hexkey.decode())) in dc.key_forms(hexkey)


def test_key_never_appears_in_output_or_written_files(repo: Path) -> None:
    """Every subcommand, run end to end with a known key: neither stdout/stderr nor any file
    written under the repo contains the key in raw, base64 or hex form."""
    for lp in (IBJA, "data/fusion_snapshots.parquet", "data/nowcast_shadow_log.json"):
        _put(repo, lp, os.urandom(2000))
    outputs = []
    for args in (
        ("encrypt", IBJA, "data/fusion_snapshots.parquet", "data/nowcast_shadow_log.json"),
        ("decrypt", "--all"),
        ("verify", "--all"),
        ("verify", "--all", "--hash-only"),
        ("guard",),
        ("manifest",),
        ("scan-key", "--changed"),
        ("decrypt", IBJA),  # and the failure paths
    ):
        r = _cli(repo, *args)
        outputs.append(r.stdout + r.stderr)
    bad = _cli(repo, "decrypt", IBJA, key=OTHER_KEY)
    outputs.append(bad.stdout + bad.stderr)
    forms = dc.key_forms(TEST_KEY.encode()) + dc.key_forms(OTHER_KEY.encode())
    for out in outputs:
        for form in forms:
            assert form.decode("latin-1") not in out
    written = [f for f in repo.rglob("*") if f.is_file() and ".git" not in f.relative_to(repo).parts]
    assert len(written) >= 7
    assert dc.scan_for_key(written, TEST_KEY.encode()) == []


def test_scan_key_finds_a_planted_key(repo: Path) -> None:
    f = _put(repo, "data/leak.json", b'{"k": "' + base64.b64encode(TEST_KEY.encode()) + b'"}')
    r = _cli(repo, "scan-key", str(f))
    assert r.returncode == 1 and "key material found" in r.stderr
    assert TEST_KEY not in r.stderr


def test_mask_key_prints_only_mask_commands(repo: Path) -> None:
    r = _cli(repo, "mask-key")
    assert r.returncode == 0
    lines = r.stdout.splitlines()
    assert lines and all(line.startswith("::add-mask::") for line in lines)
    assert f"::add-mask::{TEST_KEY}" in lines


@pytest.mark.real_kdf
def test_real_process_wrong_key_and_tamper_fail_cleanly(repo: Path) -> None:
    """One true subprocess run at production scrypt cost: exit codes, no traceback, no key."""
    _put(repo, IBJA, b"rows " * 100)
    assert _subprocess_cli(repo, "encrypt", IBJA, key=TEST_KEY).returncode == 0
    (repo / IBJA).unlink()
    bad = _subprocess_cli(repo, "decrypt", IBJA, key=OTHER_KEY)
    assert bad.returncode == 1 and "wrong key" in bad.stderr and "Traceback" not in bad.stderr
    ok = _subprocess_cli(repo, "decrypt", IBJA, key=TEST_KEY)
    assert ok.returncode == 0 and (repo / IBJA).read_bytes() == b"rows " * 100
    for out in (bad.stdout + bad.stderr, ok.stdout + ok.stderr):
        assert TEST_KEY not in out and OTHER_KEY not in out
