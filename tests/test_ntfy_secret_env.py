"""Every workflow step that sends to ntfy must have the NTFY_TOPIC secret in its env.

2026-10-19: a run log of visitors-weekly showed the line `NTFY_TOPIC is not set; nothing sent`, which looked like a
missing secret. It was the step's own script echoed by the runner (the colored command echo), not output; the same
step's env block showed `NTFY_TOPIC: ***` (set) and it printed `nothing to send`. This file keeps the real failure
mode out: a run script that reads NTFY_TOPIC, or a composite action given ntfy-topic, without the secret wired in
would silently skip every alert. The steps are enumerated from the workflow files, not a hand list."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"
SECRET = "${{ secrets.NTFY_TOPIC }}"
READS_TOPIC = re.compile(r"\$\{?NTFY_TOPIC\b")


def steps():
    for p in sorted(WORKFLOWS.glob("*.yml")):
        doc = yaml.safe_load(p.read_text(encoding="utf-8"))
        wf_env = doc.get("env") or {}
        for job_id, job in (doc.get("jobs") or {}).items():
            job_env = job.get("env") or {}
            for i, step in enumerate(job.get("steps") or []):
                env = {**wf_env, **job_env, **(step.get("env") or {})}
                yield p.name, f"{job_id}[{i}] {step.get('name', step.get('uses', ''))}", step, env


def test_steps_are_found() -> None:
    assert sum(1 for _ in steps()) > 100  # guards against the enumeration silently matching nothing


def missing_secret(found) -> list[str]:
    return [
        f"{f}: {label}"
        for f, label, step, env in found
        if READS_TOPIC.search(step.get("run") or "") and env.get("NTFY_TOPIC") != SECRET
    ]


def test_run_scripts_reading_ntfy_topic_get_the_secret() -> None:
    bad = missing_secret(steps())
    assert not bad, f"reads $NTFY_TOPIC without env NTFY_TOPIC: {SECRET}: {bad}"


def test_some_run_script_reads_ntfy_topic() -> None:
    assert any(READS_TOPIC.search(s.get("run") or "") for _, _, s, _ in steps())


def test_ntfy_topic_inputs_get_the_secret() -> None:
    bad = [
        f"{f}: {label} ntfy-topic={step['with']['ntfy-topic']!r}"
        for f, label, step, _ in steps()
        if "ntfy-topic" in (step.get("with") or {}) and step["with"]["ntfy-topic"] != SECRET
    ]
    assert not bad, bad


def test_checker_flags_a_step_without_the_secret() -> None:
    step = {"run": 'curl "https://ntfy.sh/${NTFY_TOPIC}"'}
    assert missing_secret([("x.yml", "s", step, {})]) == ["x.yml: s"]
    assert missing_secret([("x.yml", "s", step, {"NTFY_TOPIC": SECRET})]) == []
