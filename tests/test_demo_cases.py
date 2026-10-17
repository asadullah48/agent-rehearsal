import json

from agent_rehearsal.cli import main
from agent_rehearsal.demo import SCRIPTED_LABEL, generate_demo


def test_demo_false_success_fails_and_stale_approval_is_blocked(tmp_path):
    generate_demo(tmp_path / "demo")
    for slug in ("false-success", "stale-approval"):
        run = tmp_path / "demo" / "runs" / slug
        report = json.loads((run / "report.json").read_text())
        assert report["adapter"]["id"] == "scripted-offline"
        assert report["measurements"]["model_calls"] == 0
        assert SCRIPTED_LABEL in (tmp_path / "demo" / f"{slug}.html").read_text()
        assert not report["final_state"]["purchase_orders"]
        purchases = [c for c in report["tool_calls"] if c["tool"] == "create_purchase_order"]
        assert len(purchases) == 2
        assert all(c["response"]["error"]["reason_code"] == "APPROVAL_REVOKED" for c in purchases)
        assertion = next(a for a in report["assertions"] if a["id"] == "expected_business_outcome")
        failed = slug == "false-success"
        assert report["controller"]["final_state"] == ("FULFILLED" if failed else "REFUSED")
        assert report["verdict"] == ("fail" if failed else "pass")
        assert assertion["status"] == ("fail" if failed else "pass")
        for command in ("evaluate", "verify", "replay"):
            assert main([command, "--run-dir", str(run)]) == int(failed and command == "evaluate")
