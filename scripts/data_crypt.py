"""Encrypt the raw third-party data files this public repo must not republish (ADR 060).

Only ciphertext and a plaintext SHA-256 are committed for every path in ``REGISTRY``.
CI decrypts with the ``DATA_ENC_KEY`` repository secret, runs the pipeline on the plaintext,
and re-encrypts before committing. The plaintext paths are gitignored.

Layout, for a registered logical path ``P`` (e.g. ``data/ibja_rates.parquet``):
  data/encrypted/P.enc          ciphertext (format below)
  data/encrypted/P.sha256.json  public manifest entry: plaintext SHA-256 and size, ciphertext
                                SHA-256, key id. Anyone holding a decrypted copy can check it.
Per-file manifest entries, not one MANIFEST.json: the producing workflows run on different
schedules and each opens its own bot PR, so a shared manifest file would make them conflict.
``manifest`` prints the combined view.

Ciphertext format (version 1):
  b"GRTENC" | 0x01 | uint32 big-endian header length | header JSON (UTF-8, sorted keys) |
  AES-256-GCM ciphertext with its 16-byte tag
The header holds the logical path, schema version, KDF parameters, a random 16-byte salt, a
random 96-bit nonce and the key id. The whole prefix (magic through header) is the GCM
associated data, so the path and schema version are authenticated: a ciphertext moved to
another path, or a header edited in any byte, fails authentication.

Key handling:
- The key is read from the ``DATA_ENC_KEY`` environment variable only. There is no command-line
  option for it (argv shows up in process listings and CI logs).
- The per-file AES key is scrypt(DATA_ENC_KEY, salt, n=2**15, r=8, p=1). scrypt rather than HKDF
  because the secret's entropy is not known to this code: scrypt keeps a guessable secret
  expensive to brute-force, and costs about 0.1 s per file.
- The key id is scrypt(DATA_ENC_KEY, fixed salt)[:8] in hex. It tells "wrong key" apart from
  "tampered file" and names the key during rotation. Guessing a key from its id costs the same
  scrypt call as attacking any file.
- No message this script prints contains the key or anything derived from it except the key id.
  ``mask-key`` is the one exception: it prints ``::add-mask::`` lines for GitHub Actions, and
  CI calls it before any other step.

Fail-closed rules:
- Decryption checks the tag, then the header path, then the plaintext SHA-256 against the
  manifest. It writes the plaintext to a temporary file and moves it into place only after
  every check passes, so a failed check never leaves a partial plaintext file.
- ``encrypt`` refuses to replace an existing ciphertext with a different plaintext unless that
  path was decrypted in this workspace (the ``.data_crypt_state.json`` record). If a pipeline
  step ran without decrypting first, for example after a failed decrypt, it would rebuild the
  file from nothing, and encrypting that would silently wipe the history. ``--allow-replace``
  overrides the check, for deliberate rewrites only.
- A frozen file (a registered research snapshot) must match its pinned SHA-256 at every
  encrypt and decrypt.

Usage:
  python scripts/data_crypt.py decrypt --all
  python scripts/data_crypt.py encrypt data/ibja_rates.parquet
  python scripts/data_crypt.py verify --all [--hash-only]
  python scripts/data_crypt.py guard            # CI: no key needed
  python scripts/data_crypt.py key-id           # is my offline copy the key CI uses?
  python scripts/data_crypt.py scan-key --changed
  python scripts/data_crypt.py migrate          # one-shot, see encrypt-raw-data-migration.yml
  python scripts/data_crypt.py rotate           # DATA_ENC_KEY_OLD -> DATA_ENC_KEY (ADR 060)
"""

from __future__ import annotations

import argparse
import base64
import binascii
import functools
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

KEY_ENV = "DATA_ENC_KEY"
OLD_KEY_ENV = "DATA_ENC_KEY_OLD"  # rotation only: the key being retired
MIN_KEY_CHARS = 16
MAGIC = b"GRTENC"
FORMAT_VERSION = 1
SCHEMA_VERSION = 1
ALG = "AES-256-GCM"
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**15, 8, 1
KEY_ID_SALT = b"grt-data-enc/key-id/v1"
ENC_DIR = "data/encrypted"
STATE_FILE = ".data_crypt_state.json"

# Every path whose plaintext must never be committed. category: why it is raw (ADR 060's table).
# frozen_sha256: research snapshots whose bytes are part of a registered result; any other bytes
# are refused.
REGISTRY: dict[str, dict[str, str]] = {
    "reports/vol_regime_prereg_gcf_2000_2012.csv": {
        "category": "frozen research snapshot (Yahoo Finance GC=F closes, ADR 044/052)",
        "frozen_sha256": "2db071aae050c593607f07518a4b36663916bc9fc9e288e3ee8292800437c6ac",
    },
    "reports/vol_regime_prereg_gld_2004_2012.csv": {
        "category": "frozen research snapshot (Yahoo Finance GLD closes, ADR 044)",
        "frozen_sha256": "9aa4143f1b1e4326a686f885724966d360fd22aa85c75ee8b7e1939b2e141127",
    },
    "data/ibja_rates.parquet": {"category": "raw IBJA history (ADR 059)"},
    "data/fusion_snapshots.parquet": {"category": "raw GRT/Malabar/Kalyan rates (ADR 059)"},
    "data/shadow_fusion_output.json": {"category": "raw Kalyan rates and invertible markups"},
    "data/feature_store/snapshots.parquet": {
        "category": "raw Tanishq, IBJA and Yahoo Finance levels (ADR 054/059)"
    },
    "data/next_day_range_shadow.json": {"category": "raw Tanishq current/next-day prices"},
    "data/wait_or_buy_shadow.json": {"category": "raw IBJA prices per entry"},
    "data/nowcast_shadow_log.json": {"category": "raw Tanishq daily truth"},
    "reports/derived_premium.json": {"category": "raw IBJA pm_999 and Yahoo COMEX/USD-INR"},
}


class CryptError(Exception):
    """Any failure: the CLI prints it and exits non-zero. Messages never contain key material."""


@dataclass(frozen=True)
class Paths:
    root: Path

    def plain(self, logical: str) -> Path:
        return self.root / logical

    def enc(self, logical: str) -> Path:
        return self.root / ENC_DIR / f"{logical}.enc"

    def meta(self, logical: str) -> Path:
        return self.root / ENC_DIR / f"{logical}.sha256.json"

    def state(self) -> Path:
        return self.root / STATE_FILE


# ---------------------------------------------------------------- key material


def _load_key(env: str = KEY_ENV) -> bytes:
    raw = os.environ.get(env)
    if raw is None or raw == "":
        raise CryptError(f"{env} is not set")
    key = raw.strip()
    if len(key) < MIN_KEY_CHARS:
        raise CryptError(f"{env} is shorter than {MIN_KEY_CHARS} characters; refusing to use it")
    return key.encode("utf-8")


def _scrypt(key: bytes, salt: bytes) -> bytes:
    return hashlib.scrypt(
        key, salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, maxmem=128 * 1024 * 1024, dklen=32
    )


@functools.lru_cache(maxsize=4)  # one scrypt per process per key, not one per file
def key_id(key: bytes) -> str:
    return _scrypt(key, KEY_ID_SALT)[:8].hex()


def key_forms(key: bytes) -> list[bytes]:
    """The key as it could plausibly appear if leaked: raw, base64 (std/urlsafe, padded or not),
    hex (lower/upper), plus the same encodings of the decoded bytes when the key is itself
    base64 or hex."""
    blobs = [key]
    text = key.decode("ascii", errors="ignore")
    padded = text + "=" * (-len(text) % 4)
    for decode in (
        lambda: base64.b64decode(padded, validate=True),
        lambda: base64.urlsafe_b64decode(padded),
        lambda: bytes.fromhex(text),
    ):
        try:
            decoded = decode()
        except (ValueError, binascii.Error):
            continue
        if len(decoded) >= 8:
            blobs.append(decoded)
    forms: set[bytes] = set()
    for b in blobs:
        forms.update(
            {
                b,
                base64.b64encode(b),
                base64.b64encode(b).rstrip(b"="),
                base64.urlsafe_b64encode(b),
                base64.urlsafe_b64encode(b).rstrip(b"="),
                b.hex().encode(),
                b.hex().upper().encode(),
            }
        )
    return sorted((f for f in forms if len(f) >= 8), key=len, reverse=True)


# ---------------------------------------------------------------- format


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canon(obj: dict[str, Any]) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def encrypt_bytes(plaintext: bytes, logical: str, key: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    salt, nonce = os.urandom(16), os.urandom(12)
    header = {
        "alg": ALG,
        "kdf": {"name": "scrypt", "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P},
        "key_id": key_id(key),
        "nonce": base64.b64encode(nonce).decode(),
        "path": logical,
        "salt": base64.b64encode(salt).decode(),
        "schema_version": SCHEMA_VERSION,
    }
    hbytes = _canon(header)
    prefix = MAGIC + bytes([FORMAT_VERSION]) + len(hbytes).to_bytes(4, "big") + hbytes
    return prefix + AESGCM(_scrypt(key, salt)).encrypt(nonce, plaintext, prefix)


def parse_header(blob: bytes) -> tuple[dict[str, Any], bytes, bytes]:
    """(header, associated data, ciphertext). Raises on anything malformed."""
    if len(blob) < len(MAGIC) + 5 or not blob.startswith(MAGIC):
        raise CryptError("not a GRTENC file (bad magic)")
    version = blob[len(MAGIC)]
    if version != FORMAT_VERSION:
        raise CryptError(f"unsupported format version {version}")
    start = len(MAGIC) + 5
    hlen = int.from_bytes(blob[len(MAGIC) + 1 : start], "big")
    if hlen > 4096 or start + hlen > len(blob):
        raise CryptError("corrupt header length")
    try:
        header = json.loads(blob[start : start + hlen].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CryptError("corrupt header") from exc
    if header.get("alg") != ALG or header.get("schema_version") != SCHEMA_VERSION:
        raise CryptError("unsupported algorithm or schema version in header")
    return header, blob[: start + hlen], blob[start + hlen :]


def decrypt_bytes(blob: bytes, logical: str, key: bytes) -> bytes:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    header, aad, ct = parse_header(blob)
    if header.get("path") != logical:
        raise CryptError(
            f"ciphertext is for {header.get('path')!r}, not {logical!r} (file swapped or moved)"
        )
    kdf = header.get("kdf", {})
    if (kdf.get("name"), kdf.get("n"), kdf.get("r"), kdf.get("p")) != (
        "scrypt",
        SCRYPT_N,
        SCRYPT_R,
        SCRYPT_P,
    ):
        raise CryptError("unsupported KDF parameters in header")
    kid = key_id(key)
    if header.get("key_id") != kid:
        raise CryptError(
            f"wrong key for {logical}: DATA_ENC_KEY has key id {kid}, "
            f"the file was encrypted under key id {header.get('key_id')}"
        )
    try:
        salt = base64.b64decode(header["salt"], validate=True)
        nonce = base64.b64decode(header["nonce"], validate=True)
    except (KeyError, binascii.Error) as exc:
        raise CryptError("corrupt salt/nonce in header") from exc
    if len(salt) != 16 or len(nonce) != 12:
        raise CryptError("corrupt salt/nonce length in header")
    try:
        return AESGCM(_scrypt(key, salt)).decrypt(nonce, ct, aad)
    except InvalidTag as exc:
        raise CryptError(
            f"authentication failed for {logical}: the ciphertext or its header was modified "
            "(or the key is wrong)"
        ) from exc


# ---------------------------------------------------------------- file operations


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _read_meta(p: Paths, logical: str) -> dict[str, Any] | None:
    mp = p.meta(logical)
    if not mp.exists():
        return None
    try:
        meta: dict[str, Any] = json.loads(mp.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CryptError(f"corrupt manifest entry {mp}") from exc
    return meta


def _read_state(p: Paths) -> dict[str, str]:
    try:
        state: dict[str, str] = json.loads(p.state().read_text(encoding="utf-8"))
        return state
    except (OSError, json.JSONDecodeError):
        return {}


def _record_state(p: Paths, logical: str, sha: str) -> None:
    state = _read_state(p)
    state[logical] = sha
    _atomic_write(p.state(), (json.dumps(state, indent=1, sort_keys=True) + "\n").encode())


def _check_frozen(logical: str, sha: str) -> None:
    pinned = REGISTRY[logical].get("frozen_sha256")
    if pinned and sha != pinned:
        raise CryptError(
            f"{logical} is a frozen snapshot: SHA-256 {sha} is not the pinned {pinned}"
        )


def _registered(logical: str) -> str:
    logical = logical.replace("\\", "/")
    if logical not in REGISTRY:
        raise CryptError(
            f"{logical} is not a registered path (see REGISTRY in {Path(__file__).name})"
        )
    return logical


def _git_tracked(root: Path, logical: str) -> bool:
    r = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", logical],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    return r.returncode == 0


def _write_encrypted(p: Paths, logical: str, data: bytes, key: bytes) -> None:
    blob = encrypt_bytes(data, logical, key)
    if (
        decrypt_bytes(blob, logical, key) != data
    ):  # belt and braces: never commit an unreadable file
        raise CryptError(f"round-trip check failed for {logical}")
    _atomic_write(p.enc(logical), blob)
    entry = {
        "path": logical,
        "ciphertext": f"{ENC_DIR}/{logical}.enc",
        "plaintext_sha256": _sha256(data),
        "plaintext_bytes": len(data),
        "ciphertext_sha256": _sha256(blob),
        "key_id": key_id(key),
        "format": f"GRTENC v{FORMAT_VERSION}, {ALG}, scrypt",
        "category": REGISTRY[logical]["category"],
    }
    _atomic_write(p.meta(logical), (json.dumps(entry, indent=1, sort_keys=True) + "\n").encode())
    _record_state(p, logical, entry["plaintext_sha256"])


def encrypt_one(p: Paths, logical: str, key: bytes, *, allow_replace: bool = False) -> str:
    """Returns a one-word outcome: encrypted / unchanged / absent / legacy-plaintext."""
    plain = p.plain(logical)
    meta = _read_meta(p, logical)
    if meta is None and _git_tracked(p.root, logical):
        # Not migrated yet: the plaintext is still tracked, and the migration workflow is the
        # only thing that switches a path over (so ciphertext and `git rm --cached` land together).
        return "legacy-plaintext"
    if not plain.exists():
        return "absent"
    data = plain.read_bytes()
    sha = _sha256(data)
    _check_frozen(logical, sha)
    if meta is not None and p.enc(logical).exists():
        if meta.get("plaintext_sha256") == sha:
            return "unchanged"
        if not allow_replace and _read_state(p).get(logical) is None:
            raise CryptError(
                f"refusing to replace the ciphertext of {logical}: its plaintext was not decrypted "
                "in this workspace, so it may have been rebuilt from nothing (run `decrypt` first, "
                "or pass --allow-replace for a deliberate rewrite)"
            )
    _write_encrypted(p, logical, data, key)
    return "encrypted"


def decrypt_one(p: Paths, logical: str, key: bytes) -> str:
    meta = _read_meta(p, logical)
    enc = p.enc(logical)
    if meta is None and not enc.exists():
        return "not-migrated"
    if meta is None or not enc.exists():
        raise CryptError(f"{logical}: ciphertext and manifest entry must both exist")
    blob = enc.read_bytes()
    if _sha256(blob) != meta.get("ciphertext_sha256"):
        raise CryptError(f"{logical}: ciphertext SHA-256 does not match its manifest entry")
    data = decrypt_bytes(blob, logical, key)
    sha = _sha256(data)
    if sha != meta.get("plaintext_sha256"):
        raise CryptError(f"{logical}: decrypted SHA-256 does not match the manifest")
    _check_frozen(logical, sha)
    _atomic_write(p.plain(logical), data)
    _record_state(p, logical, sha)
    return "decrypted"


def verify_one(p: Paths, logical: str, key: bytes | None) -> str:
    meta = _read_meta(p, logical)
    if meta is None:
        return "not-migrated"
    enc = p.enc(logical)
    if not enc.exists() or _sha256(enc.read_bytes()) != meta.get("ciphertext_sha256"):
        raise CryptError(f"{logical}: ciphertext missing or not matching its manifest entry")
    header, _, _ = parse_header(enc.read_bytes())
    if header.get("path") != logical:
        raise CryptError(f"{logical}: header names {header.get('path')!r}")
    if key is not None:
        if _sha256(decrypt_bytes(enc.read_bytes(), logical, key)) != meta["plaintext_sha256"]:
            raise CryptError(f"{logical}: decrypted content does not match the manifest")
        return "ok (decrypted in memory, hash matches)"
    plain = p.plain(logical)
    if not plain.exists():
        return "ok (ciphertext matches manifest; no plaintext on disk to hash)"
    if _sha256(plain.read_bytes()) != meta["plaintext_sha256"]:
        raise CryptError(f"{logical}: plaintext on disk does not match the manifest SHA-256")
    return "ok (plaintext on disk matches manifest)"


# ---------------------------------------------------------------- guard and scan


def guard(p: Paths) -> list[str]:
    """Structural checks that need no key. Returns problems (empty = pass)."""
    problems: list[str] = []
    for logical in REGISTRY:
        meta_exists = p.meta(logical).exists()
        enc_exists = p.enc(logical).exists()
        if meta_exists != enc_exists:
            problems.append(f"{logical}: ciphertext and manifest entry must both exist or neither")
        if (meta_exists or enc_exists) and _git_tracked(p.root, logical):
            problems.append(f"{logical}: plaintext is tracked by git although it has ciphertext")
        ignored = subprocess.run(
            ["git", "check-ignore", "-q", "--no-index", "--", logical],
            cwd=p.root,
            check=False,
        )
        if ignored.returncode != 0:
            problems.append(f"{logical}: plaintext path is not gitignored")
        if meta_exists and enc_exists:
            try:
                verify_one(p, logical, None)
            except CryptError as exc:
                problems.append(str(exc))
    enc_root = p.root / ENC_DIR
    allowed = {f"{ENC_DIR}/{lp}.enc" for lp in REGISTRY} | {
        f"{ENC_DIR}/{lp}.sha256.json" for lp in REGISTRY
    }
    allowed.add(f"{ENC_DIR}/README.md")
    if enc_root.exists():
        for f in sorted(enc_root.rglob("*")):
            rel = f.relative_to(p.root).as_posix()
            if f.is_file() and rel not in allowed:
                problems.append(f"{rel}: unexpected file under {ENC_DIR}/")
    return problems


def _changed_files(root: Path) -> list[Path]:
    out: set[str] = set()
    for args in (
        ["git", "diff", "--cached", "--name-only"],
        ["git", "diff", "--name-only"],
        ["git", "ls-files", "--others", "--exclude-standard"],
    ):
        r = subprocess.run(args, cwd=root, capture_output=True, text=True, check=True)
        out.update(line for line in r.stdout.splitlines() if line)
    out.update(REGISTRY)  # decrypted plaintext is ignored, so list it explicitly
    out.add(STATE_FILE)
    return [root / f for f in sorted(out) if (root / f).is_file()]


def scan_for_key(files: list[Path], key: bytes) -> list[str]:
    forms = key_forms(key)
    hits = []
    for f in files:
        data = f.read_bytes()
        if any(form in data for form in forms):
            hits.append(str(f))
    return hits


# ---------------------------------------------------------------- CLI


def _targets(args: argparse.Namespace) -> list[str]:
    if args.all:
        return list(REGISTRY)
    if not args.paths:
        raise CryptError("name one or more registered paths, or pass --all")
    return [_registered(x) for x in args.paths]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--root", type=Path, default=ROOT, help="repository root (tests)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("encrypt", "decrypt", "verify"):
        s = sub.add_parser(name)
        s.add_argument("paths", nargs="*")
        s.add_argument("--all", action="store_true")
        if name == "encrypt":
            s.add_argument("--allow-replace", action="store_true")
        if name == "verify":
            s.add_argument("--hash-only", action="store_true", help="no key: hash checks only")
    sub.add_parser("guard")
    sub.add_parser("manifest")
    sub.add_parser("mask-key")
    sub.add_parser("key-id", help="print the key id of DATA_ENC_KEY (compare with a manifest)")
    sk = sub.add_parser("scan-key")
    sk.add_argument("files", nargs="*", type=Path)
    sk.add_argument("--changed", action="store_true")
    sub.add_parser("migrate")
    sub.add_parser("rotate", help=f"re-encrypt every migrated file from {OLD_KEY_ENV} to {KEY_ENV}")
    args = ap.parse_args(argv)
    p = Paths(args.root.resolve())
    try:
        return _run(args, p)
    except CryptError as exc:
        print(f"data_crypt: ERROR: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        # No traceback: an exception message could in principle quote a key-derived value.
        # Redact every key form before printing, whatever the exception.
        msg = f"{type(exc).__name__}: {exc}"
        raw = os.environ.get(KEY_ENV, "").strip().encode()
        for form in key_forms(raw) if raw else []:
            msg = msg.replace(form.decode("latin-1"), "***")
        print(f"data_crypt: UNEXPECTED ERROR: {msg}", file=sys.stderr)
        return 2


def _run(args: argparse.Namespace, p: Paths) -> int:
    if args.cmd == "guard":
        problems = guard(p)
        for msg in problems:
            print(f"data_crypt guard: FAIL: {msg}", file=sys.stderr)
        migrated = sum(p.meta(lp).exists() for lp in REGISTRY)
        print(
            f"data_crypt guard: {len(REGISTRY)} registered, {migrated} migrated, "
            f"{len(problems)} problem(s)"
        )
        return 1 if problems else 0
    if args.cmd == "manifest":
        entries = {lp: _read_meta(p, lp) for lp in REGISTRY}
        print(json.dumps({"registered": entries}, indent=1, sort_keys=True))
        return 0
    if args.cmd in ("mask-key", "scan-key"):
        # Lenient on purpose: masking and scanning are useful for any key, even one too short
        # to encrypt with, and with no key set there is nothing to mask or leak.
        raw_key = os.environ.get(KEY_ENV, "").strip().encode("utf-8")
        if not raw_key:
            print(f"data_crypt {args.cmd}: {KEY_ENV} not set, nothing to do")
            return 0
    if args.cmd == "mask-key":
        for form in key_forms(raw_key):
            try:
                print(f"::add-mask::{form.decode('ascii')}")
            except UnicodeDecodeError:
                continue
        return 0
    if args.cmd == "scan-key":
        key = raw_key
        files = _changed_files(p.root) if args.changed else [f.resolve() for f in args.files]
        hits = scan_for_key(files, key)
        for h in hits:
            print(f"data_crypt scan-key: FAIL: key material found in {h}", file=sys.stderr)
        print(f"data_crypt scan-key: scanned {len(files)} file(s), {len(hits)} hit(s)")
        return 1 if hits else 0
    if args.cmd == "key-id":
        print(key_id(_load_key()))
        return 0
    if args.cmd == "verify":
        key = None if args.hash_only else _load_key()
        for lp in _targets(args):
            print(f"{lp}: {verify_one(p, lp, key)}")
        return 0
    if args.cmd == "migrate":
        return _migrate(p)
    if args.cmd == "rotate":
        return _rotate(p)
    targets = _targets(args)
    if args.cmd == "decrypt":
        if all(_read_meta(p, lp) is None and not p.enc(lp).exists() for lp in targets):
            print("data_crypt: nothing migrated yet; plaintext is still tracked -- nothing to do")
            return 0
        key = _load_key()
        for lp in targets:
            print(f"{lp}: {decrypt_one(p, lp, key)}")
        return 0
    # encrypt
    if all(_read_meta(p, lp) is None and _git_tracked(p.root, lp) for lp in targets):
        print(
            "data_crypt: every target is still tracked plaintext (pre-migration) -- nothing to do"
        )
        return 0
    key = _load_key()
    for lp in targets:
        print(f"{lp}: {encrypt_one(p, lp, key, allow_replace=args.allow_replace)}")
    return 0


def _migrate(p: Paths) -> int:
    """One-shot: encrypt every tracked registered plaintext, verify, and untrack it."""
    key = _load_key()
    done = []
    for lp in REGISTRY:
        if not _git_tracked(p.root, lp):
            print(f"{lp}: not tracked, skipped")
            continue
        if _read_meta(p, lp) is not None:
            raise CryptError(f"{lp}: tracked plaintext AND a manifest entry -- inconsistent state")
        data = p.plain(lp).read_bytes()
        _check_frozen(lp, _sha256(data))
        _write_encrypted(p, lp, data, key)
        if decrypt_bytes(p.enc(lp).read_bytes(), lp, key) != data:
            raise CryptError(f"{lp}: round trip mismatch")
        done.append(lp)
    if done:
        subprocess.run(["git", "rm", "--cached", "--quiet", "--", *done], cwd=p.root, check=True)
    print(f"data_crypt migrate: {len(done)} file(s) encrypted and untracked: {', '.join(done)}")
    return 0


def _rotate(p: Paths) -> int:
    """Key rotation: decrypt every migrated file with the old key, verify it against the
    manifest, and re-encrypt it with the new key (new salt and nonce, new key id)."""
    old_key, new_key = _load_key(OLD_KEY_ENV), _load_key()
    if key_id(old_key) == key_id(new_key):
        raise CryptError(f"{OLD_KEY_ENV} and {KEY_ENV} are the same key")
    done = []
    for lp in REGISTRY:
        meta = _read_meta(p, lp)
        if meta is None:
            continue
        data = decrypt_bytes(p.enc(lp).read_bytes(), lp, old_key)
        if _sha256(data) != meta.get("plaintext_sha256"):
            raise CryptError(f"{lp}: decrypted content does not match the manifest")
        _write_encrypted(p, lp, data, new_key)
        done.append(lp)
    print(
        f"data_crypt rotate: {len(done)} file(s) re-encrypted, key id "
        f"{key_id(old_key)} -> {key_id(new_key)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
