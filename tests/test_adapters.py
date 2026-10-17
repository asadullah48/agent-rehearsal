import json
from pathlib import Path

import pytest

from agent_rehearsal.cli import main
from test_cli import cli

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "revoked-approval.json"


def test_scripted_bad_decision_is_blocked_and_honestly_labelled(tmp_path):
    output = tmp_path / "scripted"
    result = cli(
        "run",
        "--scenario",
        "approval-revoked",
        "--seed",
        "42",
        "--output",
        str(output),
        "--adapter",
        "scripted",
        "--script",
        str(SCRIPT),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Scripted offline decision replay" in result.stdout
    report = json.loads((output / "report.json").read_text())
    assert report["adapter"]["id"] == "scripted-offline"
    assert report["adapter"]["live_model"] is False
    purchases = [c for c in report["tool_calls"] if c["tool"] == "create_purchase_order"]
    assert len(purchases) == 2
    assert all(c["response"]["error"]["reason_code"] == "APPROVAL_REVOKED" for c in purchases)
    assert not report["final_state"]["purchase_orders"]
    assert report["verdict"] == "pass"


def test_both_offline_implementations_satisfy_protocol():
    from agent_rehearsal.adapters import Controller, load_scripted
    from agent_rehearsal.controller import ReferenceController

    assert isinstance(ReferenceController("REQ-001", 42), Controller)
    assert isinstance(load_scripted(SCRIPT), Controller)


def test_evaluator_rejects_scripted_false_success(tmp_path):
    script = json.loads(SCRIPT.read_text())
    script["final_state"] = "FULFILLED"
    path = tmp_path / "bad-claim.json"
    path.write_text(json.dumps(script))
    output = tmp_path / "run"
    assert (
        main(
            [
                "run",
                "--scenario",
                "approval-revoked",
                "--seed",
                "42",
                "--output",
                str(output),
                "--adapter",
                "scripted",
                "--script",
                str(path),
            ]
        )
        == 1
    )
    report = json.loads((output / "report.json").read_text())
    assert (
        next(a for a in report["assertions"] if a["id"] == "expected_business_outcome")["status"]
        == "fail"
    )


def test_scripted_resume_uses_persisted_decisions(tmp_path):
    path = tmp_path / "decisions.json"
    path.write_bytes(SCRIPT.read_bytes())
    output = tmp_path / "run"
    result = cli(
        "run",
        "--scenario",
        "approval-revoked",
        "--seed",
        "42",
        "--output",
        str(output),
        "--adapter",
        "scripted",
        "--script",
        str(path),
        "--crash-at",
        "after-approval",
    )
    assert result.returncode == 86
    path.unlink()
    assert cli("resume", "--run-dir", str(output)).returncode == 0
    assert main(["verify", "--run-dir", str(output)]) == 0
    assert json.loads((output / "report.json").read_text())["adapter"]["id"] == "scripted-offline"


def test_runner_reconciles_ambiguous_commit_even_for_bad_script(tmp_path):
    import sqlite3
    from contextlib import closing

    output = tmp_path / "run"
    result = cli(
        "run",
        "--scenario",
        "happy-path",
        "--seed",
        "42",
        "--output",
        str(output),
        "--adapter",
        "scripted",
        "--script",
        str(SCRIPT),
        "--crash-at",
        "after-purchase-commit",
    )
    assert result.returncode == 86
    with closing(sqlite3.connect(output / "run.sqlite")) as conn:
        cursor = conn.execute("SELECT MAX(seq) FROM tool_calls").fetchone()[0]
    # The deliberately bad replay retries after finding the PO and falsely claims
    # refusal. Those actions must be flagged, but recovery must reconcile first.
    assert cli("resume", "--run-dir", str(output)).returncode == 1
    with closing(sqlite3.connect(output / "run.sqlite")) as conn:
        first = conn.execute(
            "SELECT tool FROM tool_calls WHERE seq > ? ORDER BY seq", (cursor,)
        ).fetchone()[0]
        assert first == "get_purchase_order_by_intent"
        assert conn.execute("SELECT COUNT(*) FROM purchase_orders").fetchone()[0] == 1


@pytest.mark.parametrize(
    "text",
    [
        "not JSON",
        '{"schema_version":1,"decisions":[],"final_state":"REFUSED"}',
        '{"schema_version":1,"decisions":[],"final_state":"REFUSED","role":"reviewer"}',
    ],
)
def test_invalid_scripts_rejected_before_world_creation(tmp_path, text):
    path = tmp_path / "invalid.json"
    path.write_text(text)
    output = tmp_path / "run"
    assert (
        main(
            [
                "run",
                "--scenario",
                "happy-path",
                "--seed",
                "42",
                "--output",
                str(output),
                "--adapter",
                "scripted",
                "--script",
                str(path),
            ]
        )
        == 2
    )
    assert not output.exists()


@pytest.mark.parametrize(
    "options", [["--adapter", "scripted"], ["--adapter", "reference", "--script", str(SCRIPT)]]
)
def test_adapter_script_option_mismatch_rejected(tmp_path, options):
    output = tmp_path / "run"
    assert (
        main(["run", "--scenario", "happy-path", "--seed", "42", "--output", str(output), *options])
        == 2
    )
    assert not output.exists()
