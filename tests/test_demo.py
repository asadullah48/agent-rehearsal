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
    assert len(pages) == 6
    for path in output.rglob("*"):
        if path.is_file():
            data = path.read_bytes()
            assert not re.search(rb"[A-Za-z]:[\\/]|\\\\[A-Za-z0-9]|/Users/|/home/", data)
    for page in pages:
        html = page.read_text(encoding="utf-8")
        assert 'name="viewport"' in html
        assert "overflow-wrap:anywhere" in html
        assert "fetch(" not in html
        for href in re.findall(r'href="([^"]+)"', html):
            assert not href.startswith(("/", "http:", "https:", "file:"))
            assert (output / href).exists()
    for run in (output / "runs").iterdir():
        assert evaluate_run(run / "run.sqlite").verdict == "pass"
        anchor = json.loads((run / "evidence-anchor.json").read_text())
        assert verify_ledger(run / "evidence.jsonl", anchor["head"], anchor["count"])[0]


def test_demo_refuses_existing_output(tmp_path: Path):
    tmp_path.joinpath("keep.txt").write_text("keep")
    with pytest.raises(FileExistsError):
        generate_demo(tmp_path)
    assert tmp_path.joinpath("keep.txt").read_text() == "keep"
