"""Every install in a required CI job is pinned (brief 2026-10-30).

On 2026-10-29 an unpinned package (pandas-stubs, via the mypy hook) changed what the required `lint`
job ran and froze the site for ~19.5 h. This fails on any install command in a required job whose
packages are not pinned, so a bump is always a deliberate PR.

Scope (stated, rule 85b): every job, in every workflow, whose key or `name` is a required context
(scripts/check_required_checks_skippable.REQUIRED), plus dependency-audit (named in the brief).
Install shapes recognised: pip / pip3 / python -m pip / uv pip install, pipx install, npm install|i,
npx, apt-get install, gem install, go install, and `curl|wget ... | sh|bash`. Pinned means:
`name==version` for every package, `-r` / `-c` only with a `.lock` file, `npm ci` (lockfile), no
`--upgrade`. Any other install shape in a required job fails as unrecognised. The `.pre-commit-config`
hook pins are a separate surface: hook `rev`s are tags and additional_dependencies are checked below.
"""

from __future__ import annotations

import re
import shlex
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import check_required_checks_skippable as skippable

EXTRA_JOBS = {"dependency-audit"}
_PIP = re.compile(r"\b(?:python3? -m pip|pip3?|uv pip)\s+install\b(.*)")
_OTHER = re.compile(
    r"\b(pipx install|npm install|npm i\b|npx\b|apt-get install|apt install|gem install|go install)"
)
_PIPE_SH = re.compile(r"\b(curl|wget)\b[^|]*\|\s*(sudo\s+)?(ba)?sh\b")
_VALUE_FLAGS = {"--extra-index-url", "--index-url", "-i", "--index-strategy", "--python"}


def problems_in(run: str) -> list[str]:
    out: list[str] = []
    text = run.replace("\\\n", " ")
    for raw in text.splitlines():
        line = raw.split(" #", 1)[0].strip()
        if not line or line.startswith("#"):
            continue
        for part in re.split(r"&&|\|\||;", line):
            part = part.strip()
            if _PIPE_SH.search(part):
                out.append(f"pipes a download into a shell: {part}")
                continue
            m = _PIP.search(part)
            if m:
                out += _pip_problems(m.group(1), part)
                continue
            if part.startswith("npm ci"):
                continue
            o = _OTHER.search(part)
            if o:
                out.append(f"unpinned/unrecognised install ({o.group(1)}): {part}")
    return out


def _pip_problems(args: str, whole: str) -> list[str]:
    toks = shlex.split(args)
    out: list[str] = []
    i = 0
    while i < len(toks):
        t = toks[i]
        if t in ("-U", "--upgrade"):
            out.append(f"--upgrade floats versions: {whole}")
        elif t in ("-r", "-c", "--requirement", "--constraint"):
            f = toks[i + 1] if i + 1 < len(toks) else ""
            if not f.endswith(".lock"):
                out.append(f"{t} {f} is not a lock file: {whole}")
            i += 1
        elif t in _VALUE_FLAGS:
            i += 1
        elif t.startswith("-"):
            pass
        # a bare name is unpinned, unless a lock constraint (-c x.lock) pins it
        elif "==" not in t and not any(c in toks for c in ("-c", "--constraint")):
            out.append(f"{t} is not pinned (name==version): {whole}")
        i += 1
    return out


def _required_jobs() -> list[tuple[str, str, dict]]:
    wanted = set(skippable.REQUIRED) | EXTRA_JOBS
    jobs = []
    for wf_path in sorted((ROOT / ".github/workflows").glob("*.yml")):
        wf = yaml.safe_load(wf_path.read_text(encoding="utf-8")) or {}
        for key, job in (wf.get("jobs") or {}).items():
            if key in wanted or job.get("name") in wanted:
                jobs.append((wf_path.name, key, job))
    return jobs


def test_the_scope_finds_every_required_context():
    found = {key for _, key, _ in _required_jobs()} | {
        job.get("name") for _, _, job in _required_jobs()
    }
    assert set(skippable.REQUIRED) | EXTRA_JOBS <= found


def test_every_install_in_a_required_job_is_pinned():
    bad = []
    for wf, key, job in _required_jobs():
        for step in job.get("steps", []):
            for p in problems_in(str(step.get("run", ""))):
                bad.append(f"{wf}:{key}: {p}")
    assert bad == []


def test_check_price_installs_the_same_pinned_uv_as_lint():
    cp = (ROOT / ".github/workflows/check-price.yml").read_text(encoding="utf-8")
    lint = (ROOT / ".github/workflows/lint.yml").read_text(encoding="utf-8")
    pins = set(re.findall(r"pip install -q uv==(\S+)", cp + lint))
    assert len(pins) == 1, pins


def test_pre_commit_hook_dependencies_are_pinned():
    cfg = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    for repo in cfg["repos"]:
        for hook in repo.get("hooks", []):
            for dep in hook.get("additional_dependencies", []):
                assert "==" in dep, f"{hook['id']}: {dep}"


@pytest.mark.parametrize(
    ("run", "ok"),
    [
        ("pip install pre-commit", False),
        ("pip install -q pre-commit==4.7.0", True),
        ("pip install -q uv\nuv pip install --system -r ml/requirements-inference.lock", False),
        ("pip install -q uv==0.13.6 && uv pip install --system \\\n  -r ml/x.lock", True),
        ("pip install -q -r ml/requirements.txt", False),
        ("pip install -q --upgrade pip==25.0", False),
        ("pip install -q -c ml/requirements-inference.lock jsonschema pytest", True),
        ("python -m pip install requests", False),
        ("npm ci", True),
        ("npm ci || npm install", False),
        ("npx playwright install", False),
        ("curl -sSL https://x/install.sh | sh", False),
        ("echo pip install is mentioned in a comment # pip install foo", False),
        ("pytest -q tests/", True),
    ],
)
def test_the_checker_itself(run, ok):
    assert (problems_in(run) == []) is ok, problems_in(run)
