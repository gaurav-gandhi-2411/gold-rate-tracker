"""Build tags (`app.js?v=<VERSION>`): a page and its scripts must always come from one build."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import shell_build_tags as tags


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


stamp = _load("stamp_sw_version")


def _tree(dest: Path) -> Path:
    dest.mkdir(parents=True)
    shutil.copy2(ROOT / "service-worker.js", dest / "service-worker.js")
    for f in stamp.shell_files(ROOT):
        (dest / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / f, dest / f)
    return dest


def test_every_script_and_stylesheet_of_both_pages_carries_the_workers_version() -> None:
    assert tags.problems(ROOT) == []
    version = tags.sw_version(ROOT)
    for page in tags.TAGGED_PAGES:
        found = tags.refs((ROOT / page).read_text(encoding="utf-8"))
        assert len(found) >= 4, page  # the parser still sees the page's scripts and stylesheet
        assert {tag for _, tag in found} == {version}
    index = dict(tags.refs((ROOT / "index.html").read_text(encoding="utf-8")))
    assert {"app.js", "i18n.js", "flags.js", "style.css"} <= set(index)


def test_an_untagged_or_mismatched_reference_is_reported(tmp_path: Path) -> None:
    tree = _tree(tmp_path / "t")
    page = tree / "index.html"
    text = page.read_text(encoding="utf-8")
    version = tags.sw_version(tree)
    page.write_text(text.replace("app.js?v=" + version, "app.js", 1), encoding="utf-8")
    assert any("app.js" in p and "None" in p for p in tags.problems(tree))
    page.write_text(text.replace("flags.js?v=" + version, "flags.js?v=old", 1), encoding="utf-8")
    assert any("flags.js" in p and "'old'" in p for p in tags.problems(tree))


def test_the_build_time_version_ignores_tag_values_and_stamps_them(tmp_path: Path) -> None:
    a = _tree(tmp_path / "a")
    b = _tree(tmp_path / "b")
    tags.rewrite(b, "something-else")  # same code, different tag values
    assert stamp.compute_version(a) == stamp.compute_version(b)
    version = stamp.compute_version(a)
    stamp.stamp(a, version)
    assert (
        tags.sw_version(a) == version and tags.problems(a) == []
    )  # VERSION and tags move together


def test_a_real_code_change_still_changes_the_build_time_version(tmp_path: Path) -> None:
    a = _tree(tmp_path / "a")
    before = stamp.compute_version(a)
    (a / "app.js").write_bytes((a / "app.js").read_bytes() + b"\n// x\n")
    assert stamp.compute_version(a) != before


def test_bump_script_sets_version_and_every_tag(tmp_path: Path) -> None:
    t = _tree(tmp_path / "t")
    r = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "bump_sw_version.py"),
            "v999-test",
            "--repo-root",
            str(t),
        ],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    assert tags.sw_version(t) == "v999-test" and tags.problems(t) == []
    bad = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "bump_sw_version.py"),
            "bad version!",
            "--repo-root",
            str(t),
        ],
        capture_output=True,
        text=True,
    )
    assert bad.returncode != 0


def test_worker_serves_tagged_requests_by_build_and_fails_install_on_a_mismatched_page() -> None:
    sw = (ROOT / "service-worker.js").read_text(encoding="utf-8")
    handler = sw.split('addEventListener("fetch"', 1)[1]
    assert "tag === VERSION" in handler and "cacheFirstShell(e, url)" in handler
    assert (
        'fetch(e.request, { cache: "no-cache" })' in handler
    )  # another build's tag -> network only
    assert "assertCachedPageMatchesVersion(c)" in sw.split('addEventListener("install"', 1)[1]
