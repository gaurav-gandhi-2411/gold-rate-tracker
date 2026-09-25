"""scripts/prove_data_crypt_roundtrip.py -- ADR 060 proof on the real files, with a LOCAL TEST KEY
(never DATA_ENC_KEY). Usage: python scripts/prove_data_crypt_roundtrip.py WORKDIR [OUT.json]

clone -> migrate (encrypt + git rm --cached) -> commit -> fresh clone of that commit (commit
format: no plaintext) -> decrypt -> byte-identical + manifest SHA-256 -> tamper / wrong key ->
ADR 044 and ADR 052 --check from the decrypted snapshots.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent
TMP = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else SRC.parent / "data-crypt-proof"
PY = sys.executable
TEST_KEY = "local-test-key-e1-" + "7c1f0e9a2b3d4c5e6f708192a3b4c5d6"  # not the real secret
WRONG_KEY = "some-other-local-test-key-0000000000"


def run(cmd: list[str], cwd: Path, key: str | None = TEST_KEY, check: bool = True):
    env = {k: v for k, v in os.environ.items() if k != "DATA_ENC_KEY"}
    if key is not None:
        env["DATA_ENC_KEY"] = key
    r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, check=False)
    if check and r.returncode != 0:
        raise SystemExit(f"FAILED {cmd}: {r.stdout}\n{r.stderr}")
    return r


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> None:
    a, b = TMP / "prove_a", TMP / "prove_b"
    for d in (a, b):
        if d.exists():
            shutil.rmtree(d, onerror=lambda f, p, e: (os.chmod(p, 0o700), f(p)))
    head = run(["git", "rev-parse", "HEAD"], SRC).stdout.strip()
    run(
        ["git", "clone", "-q", "--no-hardlinks", "-c", "core.autocrlf=false", str(SRC), str(a)], TMP
    )
    run(["git", "checkout", "-q", head], a)
    sys.path.insert(0, str(a / "scripts"))
    import data_crypt as dc  # the clone's copy

    TMP.mkdir(parents=True, exist_ok=True)
    report: dict = {"source_commit": head, "test_key_id": None, "files": {}}
    originals = {
        lp: (sha(a / lp), (a / lp).stat().st_size) for lp in dc.REGISTRY if (a / lp).exists()
    }
    # frozen snapshots: the bytes committed by #1990 (1c4a372e) before #2038 proposed deleting them
    for lp in (
        "reports/vol_regime_prereg_gcf_2000_2012.csv",
        "reports/vol_regime_prereg_gld_2004_2012.csv",
    ):
        raw = subprocess.run(
            ["git", "show", f"1c4a372e:{lp}"], cwd=a, capture_output=True, check=True
        ).stdout
        report["files"].setdefault(lp, {})["sha256_at_1c4a372e"] = hashlib.sha256(raw).hexdigest()
        report["files"][lp]["adr044_pinned"] = dc.REGISTRY[lp]["frozen_sha256"]

    t0 = run([PY, "scripts/data_crypt.py", "migrate"], a)
    report["migrate_stdout"] = t0.stdout.strip()
    run(["git", "add", "data/encrypted/"], a, key=None)
    report["guard_after_migrate"] = run(
        [PY, "scripts/data_crypt.py", "guard"], a, key=None
    ).stdout.strip()
    run(
        [
            "git",
            "-c",
            "user.email=t@example.com",
            "-c",
            "user.name=t",
            "commit",
            "-q",
            "-m",
            "migrate (test key)",
        ],
        a,
        key=None,
    )
    scan = run(
        [
            PY,
            "scripts/data_crypt.py",
            "scan-key",
            *[str(p) for p in (a / "data/encrypted").rglob("*") if p.is_file()],
        ],
        a,
    )
    report["scan_ciphertext_for_test_key"] = scan.stdout.strip()

    # commit format: a fresh clone of the migration commit has no plaintext at all
    run(
        ["git", "clone", "-q", "--no-hardlinks", "-c", "core.autocrlf=false", str(a), str(b)],
        TMP,
        key=None,
    )
    missing = [lp for lp in originals if not (b / lp).exists()]
    report["plaintext_absent_in_fresh_clone"] = f"{len(missing)}/{len(originals)}"
    run([PY, "scripts/data_crypt.py", "decrypt", "--all"], b)
    for lp, (orig_sha, size) in originals.items():
        meta = json.loads((b / "data/encrypted" / f"{lp}.sha256.json").read_text())
        report["test_key_id"] = meta["key_id"]
        got = sha(b / lp)
        report["files"].setdefault(lp, {}).update(
            {
                "plaintext_bytes": size,
                "ciphertext_bytes": (b / "data/encrypted" / f"{lp}.enc").stat().st_size,
                "original_sha256": orig_sha,
                "manifest_sha256": meta["plaintext_sha256"],
                "decrypted_sha256": got,
                "byte_identical": (b / lp).read_bytes() == (a / lp).read_bytes()
                if (a / lp).exists()
                else None,
                "round_trip_ok": got == orig_sha == meta["plaintext_sha256"],
            }
        )

    # tamper: flip one ciphertext byte in a copy of every file, decrypt must fail, write nothing
    tamper = {}
    for lp in originals:
        enc = b / "data/encrypted" / f"{lp}.enc"
        saved = enc.read_bytes()
        (b / lp).unlink()
        flipped = bytearray(saved)
        flipped[len(flipped) // 2] ^= 0x01
        enc.write_bytes(bytes(flipped))
        meta_p = b / "data/encrypted" / f"{lp}.sha256.json"
        meta_saved = meta_p.read_text()
        meta = json.loads(meta_saved)
        meta["ciphertext_sha256"] = hashlib.sha256(bytes(flipped)).hexdigest()  # attacker fixes it
        meta_p.write_text(json.dumps(meta))
        r = run([PY, "scripts/data_crypt.py", "decrypt", lp], b, check=False)
        tamper[lp] = {
            "exit": r.returncode,
            "plaintext_written": (b / lp).exists(),
            "stderr": r.stderr.strip()[:140],
        }
        enc.write_bytes(saved)
        meta_p.write_text(meta_saved)
        run([PY, "scripts/data_crypt.py", "decrypt", lp], b)
    report["tamper"] = tamper
    (b / "data/ibja_rates.parquet").unlink()
    wk = run(
        [PY, "scripts/data_crypt.py", "decrypt", "data/ibja_rates.parquet"],
        b,
        key=WRONG_KEY,
        check=False,
    )
    report["wrong_key"] = {
        "exit": wk.returncode,
        "plaintext_written": (b / "data/ibja_rates.parquet").exists(),
        "stderr": wk.stderr.strip(),
    }
    nk = run(
        [PY, "scripts/data_crypt.py", "decrypt", "data/ibja_rates.parquet"],
        b,
        key=None,
        check=False,
    )
    report["no_key"] = {"exit": nk.returncode, "stderr": nk.stderr.strip()}
    run([PY, "scripts/data_crypt.py", "decrypt", "data/ibja_rates.parquet"], b)
    report["verify_hash_only"] = (
        run([PY, "scripts/data_crypt.py", "verify", "--all", "--hash-only"], b, key=None)
        .stdout.strip()
        .splitlines()
    )

    # --check reproduction from the decrypted snapshots
    for script in ("scripts/analysis_vol_regime_prereg.py", "scripts/analysis_dow_prereg.py"):
        r = run([PY, script, "--check"], b, key=None, check=False)
        report[f"check::{script}"] = {
            "exit": r.returncode,
            "stdout": r.stdout.strip()[-600:],
            "stderr": r.stderr.strip()[-300:],
        }
    # and a missing snapshot is an error, not a download
    (b / "reports/vol_regime_prereg_gcf_2000_2012.csv").unlink()
    r = run([PY, "scripts/analysis_vol_regime_prereg.py", "--check"], b, key=None, check=False)
    report["missing_snapshot"] = {"exit": r.returncode, "stderr": r.stderr.strip()[-250:]}

    # the test key must not appear anywhere in either clone's working files (outside .git)
    forms = dc.key_forms(TEST_KEY.encode())
    leaks = []
    for root in (a, b):
        for f in root.rglob("*"):
            if f.is_file() and ".git" not in f.relative_to(root).parts:
                data = f.read_bytes()
                if any(x in data for x in forms):
                    leaks.append(str(f))
    report["test_key_in_working_files"] = leaks
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else TMP / "prove_report.json"
    out.write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
