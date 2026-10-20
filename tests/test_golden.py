"""Golden canonical traces for every deterministic and scripted run.

Each file in tests/golden/ is ``canonical_semantic_trace`` of a seed-42 run. It omits
only the fields that are meant to vary (run ids, source revision, Python version and
wall-clock duration); simulated timestamps, scenario and policy hashes stay in. So a
scenario, policy or behaviour change fails here until the golden file is regenerated
on purpose:

    REHEARSAL_UPDATE_GOLDEN=1 python -m pytest tests/test_golden.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agent_rehearsal.adapters import load_scripted
from agent_rehearsal.canonical import canonical_json
from agent_rehearsal.report import canonical_semantic_trace
from agent_rehearsal.runner import execute_run
from agent_rehearsal.scenarios import DEFAULT_SCENARIOS_DIR, list_scenarios, load_scenario

GOLDEN = Path(__file__).resolve().parent / "golden"
SCRIPTS = DEFAULT_SCENARIOS_DIR.parent / "scripts"
UPDATE = os.environ.get("REHEARSAL_UPDATE_GOLDEN") == "1"

CASES = {
    **{f"deterministic-{s.id}": (s.id, None, None) for s in list_scenarios()},
    "deterministic-supplier-injection": ("supplier-injection", "live", None),
    "scripted-revoked-approval": ("approval-revoked", None, "revoked-approval.json"),
    "scripted-false-success": ("approval-revoked", None, "false-success.json"),
}


def _trace(case: str, tmp_path: Path) -> dict:
    scenario_id, subdir, script = CASES[case]
    directory = DEFAULT_SCENARIOS_DIR / subdir if subdir else DEFAULT_SCENARIOS_DIR
    controller = load_scripted(SCRIPTS / script) if script else None
    report = execute_run(
        load_scenario(scenario_id, directory), 42, tmp_path / "run", controller=controller
    ).report
    return canonical_semantic_trace(report)


def _differences(old, new, path=""):
    if type(old) is not type(new):
        yield path or "<root>"
    elif isinstance(old, dict):
        for key in sorted(set(old) | set(new)):
            yield from _differences(old.get(key), new.get(key), f"{path}.{key}")
    elif isinstance(old, list):
        if len(old) != len(new):
            yield f"{path} (length {len(old)} -> {len(new)})"
        for i, (a, b) in enumerate(zip(old, new, strict=False)):
            yield from _differences(a, b, f"{path}[{i}]")
    elif old != new:
        yield path


@pytest.mark.parametrize("case", sorted(CASES))
def test_run_matches_golden_trace(case, tmp_path):
    current = _trace(case, tmp_path)
    path = GOLDEN / f"{case}.json"
    if UPDATE:
        GOLDEN.mkdir(exist_ok=True)
        path.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    assert path.is_file(), f"missing golden file {path.name}; regenerate on purpose"
    golden = json.loads(path.read_text(encoding="utf-8"))
    # Parsed comparison is exact for every value and immune to checkout line endings.
    if canonical_json(current) != canonical_json(golden):
        changed = list(_differences(golden, current))[:15]
        pytest.fail(f"{case} differs from {path.name} at: {', '.join(changed)}")


def test_every_golden_file_has_a_case():
    assert sorted(p.stem for p in GOLDEN.glob("*.json")) == sorted(CASES)
