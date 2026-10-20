"""CLI contract, evidence-reference resolution, and offline guarantees."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

import agent_rehearsal
from agent_rehearsal.cli import main
from conftest import scenario_doc
from corruption import tamper

SRC = Path(agent_rehearsal.__file__).parent


def cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k in ("SYSTEMROOT", "PATH", "TEMP", "TMP")}
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "-m", "agent_rehearsal", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=cwd,
        env=env,
        timeout=120,
        check=False,
    )


def test_documented_cli_flow(tmp_path):
    out = tmp_path / "demo" / "happy"
    listed = cli("scenarios", "list")
    assert listed.returncode == 0, listed.stderr
    assert "happy-path" in listed.stdout and "Deterministic reference controller" in listed.stdout

    ran = cli("run", "--scenario", "happy-path", "--seed", "42", "--output", str(out))
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert "not an AI agent" in ran.stdout
    assert sorted(p.name for p in out.iterdir()) == [
        "evidence-anchor.json",
        "evidence.jsonl",
        "manifest.json",
        "report.json",
        "run.sqlite",
        "summary.md",
    ]

    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    conn = sqlite3.connect(out / "run.sqlite")
    db_events = [r[0] for r in conn.execute("SELECT event_id FROM events ORDER BY seq")]
    conn.close()
    assert [e["event_id"] for e in report["events"]] == db_events
    refs = {r for a in report["assertions"] for r in a["evidence_event_ids"]}
    refs |= {r for c in report["tool_calls"] for r in c["response"]["event_ids"]}
    assert refs and refs <= set(db_events), refs - set(db_events)

    replayed = cli("replay", "--run-dir", str(out))
    assert replayed.returncode == 0, replayed.stderr
    assert "RECORDED PLAYBACK" in replayed.stdout
    shown = re.findall(r"EVENT (EV-\d{6})", replayed.stdout)
    assert shown == db_events

    evaluated = cli("evaluate", "--run-dir", str(out))
    assert evaluated.returncode == 0, evaluated.stderr
    assert "agreement: yes" in evaluated.stdout
    assert "Evaluation verdict: pass" in evaluated.stdout


def test_nonempty_output_directory_is_refused(tmp_path):
    out = tmp_path / "occupied"
    out.mkdir()
    sentinel = out / "keep.txt"
    sentinel.write_text("prior evidence", encoding="utf-8")
    assert main(["run", "--scenario", "happy-path", "--seed", "42", "--output", str(out)]) == 2
    assert sentinel.read_text(encoding="utf-8") == "prior evidence"
    assert sorted(p.name for p in out.iterdir()) == ["keep.txt"]


def test_invalid_scenario_input_exits_2_before_creating_output(tmp_path):
    scen_dir = tmp_path / "scenarios"
    scen_dir.mkdir()
    doc = scenario_doc()
    doc["id"] = "broken"
    doc["initial_world"]["requests"][0]["amount_minor"] = 1250.5
    (scen_dir / "broken.json").write_text(json.dumps(doc), encoding="utf-8")
    out = tmp_path / "never-created"
    code = main(
        [
            "run",
            "--scenario",
            "broken",
            "--seed",
            "1",
            "--output",
            str(out),
            "--scenarios-dir",
            str(scen_dir),
        ]
    )
    assert code == 2
    assert not out.exists()


@pytest.mark.parametrize(
    "argv",
    [
        ["run", "--scenario", "no-such-scenario", "--seed", "1", "--output", "unused-dir"],
        ["run", "--scenario", "../happy-path", "--seed", "1", "--output", "unused-dir"],
        ["run", "--scenario", "happy-path", "--seed", "forty-two", "--output", "unused-dir"],
        ["run", "--scenario", "happy-path", "--output", "unused-dir"],
        ["evaluate", "--run-dir", "does-not-exist"],
        ["replay", "--run-dir", "does-not-exist"],
    ],
)
def test_invalid_input_exits_2(argv, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(argv) == 2
    assert not (tmp_path / "unused-dir").exists()


def test_evaluate_exits_1_when_persisted_evidence_fails(completed_run):
    tamper(
        completed_run.run_dir / "run.sqlite",
        "UPDATE budgets SET available_minor = available_minor + 5000",
    )
    assert main(["evaluate", "--run-dir", str(completed_run.run_dir)]) == 1


def test_evaluate_exits_2_when_report_disagrees_with_evidence(completed_run):
    path = completed_run.run_dir / "report.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["verdict"] = "fail"
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert main(["evaluate", "--run-dir", str(completed_run.run_dir)]) == 2


def test_fresh_evaluate_agrees_with_recorded_verdict(completed_run, capsys):
    assert main(["evaluate", "--run-dir", str(completed_run.run_dir)]) == 0
    assert "agreement: yes" in capsys.readouterr().out


def test_run_ignores_a_present_env_file(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("SECRET=do-not-read\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)  # the autouse guard fails the test if .env is opened
    assert main(["run", "--scenario", "happy-path", "--seed", "42", "--output", "out"]) == 0


def test_only_opt_in_transport_has_network_credential_or_env_access():
    forbidden = re.compile(
        r"^\s*(import|from)\s+(socket|urllib|http|requests|httpx|dotenv|anthropic|openai)\b"
        r"|os\.environ|getenv\(|load_dotenv|\.env\b",
        re.MULTILINE,
    )
    hits = [
        f"{p.name}: {m.group(0).strip()}"
        for p in SRC.glob("*.py")
        if p.name != "live_transport.py"
        for m in forbidden.finditer(p.read_text(encoding="utf-8"))
    ]
    assert hits == []


@pytest.mark.parametrize(
    "scenario_id",
    ["happy-path", "duplicate-po", "response-lost", "approval-revoked", "supplier-changed"],
)
def test_session2_cli_happy_paths(tmp_path, scenario_id):
    output = tmp_path / scenario_id
    assert main(["run", "--scenario", scenario_id, "--seed", "42", "--output", str(output)]) == 0
    for command in ("replay", "evaluate", "verify"):
        assert main([command, "--run-dir", str(output)]) == 0
