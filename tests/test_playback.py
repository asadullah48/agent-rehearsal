"""Recorded playback must work with tool, controller, reviewer and run invocation disabled."""

from __future__ import annotations

import hashlib
import json
import re

import pytest

from agent_rehearsal import controller, privileged, runner, tools
from agent_rehearsal.cli import main
from agent_rehearsal.playback import load_playback, verify_against_report


def _forbid(*_args, **_kwargs):
    raise AssertionError("playback attempted to execute something")


@pytest.fixture
def execution_disabled(monkeypatch):
    monkeypatch.setattr(tools.ToolGateway, "call", _forbid)
    monkeypatch.setattr(tools.ToolGateway, "__init__", _forbid)
    monkeypatch.setattr(controller.ReferenceController, "drive", _forbid)
    monkeypatch.setattr(privileged.ReviewerConsole, "grant_approval", _forbid)
    monkeypatch.setattr(privileged.ReviewerConsole, "revoke_approval", _forbid)
    monkeypatch.setattr(runner, "execute_run", _forbid)
    monkeypatch.setattr("agent_rehearsal.cli.execute_run", _forbid)


def test_playback_preserves_order_and_recorded_responses(completed_run, execution_disabled, capsys):
    db = completed_run.run_dir / "run.sqlite"
    digest_before = hashlib.sha256(db.read_bytes()).hexdigest()

    assert main(["replay", "--run-dir", str(completed_run.run_dir)]) == 0
    out = capsys.readouterr().out

    report = completed_run.report
    assert re.findall(r"EVENT (EV-\d{6})", out) == [e["event_id"] for e in report["events"]]
    assert re.findall(r"CALL  (CALL-\d{4})", out) == [c["call_id"] for c in report["tool_calls"]]
    entries = load_playback(db)
    played = [e.record["response"] for e in entries if e.kind == "tool_call"]
    assert played == [c["response"] for c in report["tool_calls"]]
    seqs = [e.seq for e in entries]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    assert hashlib.sha256(db.read_bytes()).hexdigest() == digest_before  # read-only


def test_playback_detects_report_that_disagrees_with_recording(completed_run, execution_disabled):
    path = completed_run.run_dir / "report.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["tool_calls"][0]["response"]["status"] = "error"
    path.write_text(json.dumps(doc), encoding="utf-8")
    entries = load_playback(completed_run.run_dir / "run.sqlite")
    assert verify_against_report(entries, doc)
    assert main(["replay", "--run-dir", str(completed_run.run_dir)]) == 2
