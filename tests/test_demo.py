import getpass
import platform
import re
from pathlib import Path

import pytest

from agent_rehearsal.demo import generate_demo
from agent_rehearsal.evaluator import evaluate_run
from agent_rehearsal.evidence import verify_ledger


def test_demo_real_runs_private_and_relative(tmp_path: Path):
    import json

    output = tmp_path / "demo"
    generate_demo(output)
    pages = list(output.glob("*.html"))
    assert len(pages) == 9  # Seven offline cases, index, separate live evaluation page.
    assert "No live evaluation report supplied" in (
        output / "live-model-evaluation.html"
    ).read_text(encoding="utf-8")
    for path in output.rglob("*"):
        if path.is_file():
            data = path.read_bytes()
            assert not re.search(rb"[A-Za-z]:[\\/]|\\\\[A-Za-z0-9]|/Users/|/home/", data)
            for private_name in (getpass.getuser(), platform.node()):
                assert private_name.lower().encode() not in data.lower()
    for page in pages:
        html = page.read_text(encoding="utf-8")
        assert 'name="viewport"' in html
        assert "overflow-wrap:anywhere" in html
        assert "fetch(" not in html
        for href in re.findall(r'href="([^"]+)"', html):
            assert not href.startswith(("/", "http:", "https:", "file:"))
            target, _, fragment = href.partition("#")
            linked = output / target if target else page
            assert linked.exists()
            if fragment:
                assert f'id="{fragment}"' in linked.read_text(encoding="utf-8")
    for run in (output / "runs").iterdir():
        expected = "fail" if run.name == "false-success" else "pass"
        assert evaluate_run(run / "run.sqlite").verdict == expected
        anchor = json.loads((run / "evidence-anchor.json").read_text())
        assert verify_ledger(run / "evidence.jsonl", anchor["head"], anchor["count"])[0]


def test_demo_refuses_existing_output(tmp_path: Path):
    tmp_path.joinpath("keep.txt").write_text("keep")
    with pytest.raises(FileExistsError):
        generate_demo(tmp_path)
    assert tmp_path.joinpath("keep.txt").read_text() == "keep"
