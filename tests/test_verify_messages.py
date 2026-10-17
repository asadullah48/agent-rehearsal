import json

from agent_rehearsal.cli import main


def test_verify_success_reports_count_and_head(completed_run, capsys):
    anchor = json.loads((completed_run.run_dir / "evidence-anchor.json").read_text())
    assert main(["verify", "--run-dir", str(completed_run.run_dir)]) == 0
    assert (
        f"PASS: {anchor['count']} records verified (head index {anchor['count'] - 1})"
        in capsys.readouterr().out
    )


def test_verify_failure_keeps_first_bad_index(completed_run, capsys):
    path = completed_run.run_dir / "evidence.jsonl"
    lines = path.read_text().splitlines()
    lines[1], lines[2] = lines[2], lines[1]
    path.write_text("\n".join(lines) + "\n")
    assert main(["verify", "--run-dir", str(completed_run.run_dir)]) == 1
    assert (
        "FAIL: index or previous hash mismatch; zero-based record index 1"
        in capsys.readouterr().out
    )
