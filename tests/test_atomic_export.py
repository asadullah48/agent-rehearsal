import hashlib
import json
from contextlib import closing

import pytest

from agent_rehearsal.cli import main
from test_cli import cli

BOUNDARIES = (
    "report",
    "summary",
    "anchor",
    "manifest",
    "before-publish",
    "after-backup",
    "after-publish",
)
BUNDLE = {"report.json", "summary.md", "manifest.json", "evidence-anchor.json"}


@pytest.mark.parametrize("boundary", BOUNDARIES)
@pytest.mark.parametrize("existing", [False, True])
def test_export_process_interruption_never_publishes_half_bundle(tmp_path, boundary, existing):
    output = tmp_path / "run"
    old = None
    if existing:
        assert (
            cli(
                "run", "--scenario", "happy-path", "--seed", "42", "--output", str(output)
            ).returncode
            == 0
        )
        old = {name: (output / name).read_bytes() for name in BUNDLE}
        result = cli("resume", "--run-dir", str(output), "--crash-during-export", boundary)
    else:
        result = cli(
            "run",
            "--scenario",
            "happy-path",
            "--seed",
            "42",
            "--output",
            str(output),
            "--crash-during-export",
            boundary,
        )
    assert result.returncode == 86, result.stdout + result.stderr
    present = {name for name in BUNDLE if (output / name).exists()}
    assert present in (set(), BUNDLE)
    if old and present and boundary != "after-publish":
        assert old == {name: (output / name).read_bytes() for name in BUNDLE}
    result = cli("resume", "--run-dir", str(output))
    assert result.returncode == 0, result.stdout + result.stderr
    for command in ("verify", "evaluate"):
        assert main([command, "--run-dir", str(output)]) == 0
    manifest = json.loads((output / "manifest.json").read_text())
    for name, digest in manifest.items():
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == digest


def test_export_resume_does_not_repeat_finished_work(tmp_path):
    import sqlite3

    output = tmp_path / "run"
    result = cli(
        "run",
        "--scenario",
        "response-lost",
        "--seed",
        "42",
        "--output",
        str(output),
        "--crash-during-export",
        "summary",
    )
    assert result.returncode == 86
    with closing(sqlite3.connect(output / "run.sqlite")) as conn:
        before = conn.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0]
    assert main(["resume", "--run-dir", str(output)]) == 0
    with closing(sqlite3.connect(output / "run.sqlite")) as conn:
        assert before == conn.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0]
