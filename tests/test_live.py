"""Offline adversarial and crash tests. Never imports or calls the provider SDK."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from agent_rehearsal.adapters import Controller
from agent_rehearsal.cli import main
from agent_rehearsal.live import (
    MAX_INPUT,
    MAX_OUTPUT,
    METADATA,
    DecisionService,
    LiveModelController,
    input_bound,
)
from agent_rehearsal.live_budget import (
    MODEL,
    BudgetLedger,
    LedgerCapMismatch,
    LiveStop,
    Pricing,
    dollars,
    read_ledger,
)
from agent_rehearsal.live_eval import (
    DEFAULT_PRICE,
    estimate,
    render_live_report,
    run_trials,
    trial_metrics,
)
from agent_rehearsal.live_transport import AnthropicTransport
from agent_rehearsal.runner import execute_run, resume_run
from agent_rehearsal.scenarios import DEFAULT_SCENARIOS_DIR, load_scenario
from agent_rehearsal.store import Store


def tool(name, **args):
    return {"kind": "tool", "tool": name, "arguments": args}


def finish(state="FULFILLED"):
    return {"kind": "finish", "state": state, "summary": "model claim"}


def response(proposal):
    return {
        "model": MODEL,
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 100, "output_tokens": 50},
        "content": [
            {
                "type": "tool_use",
                "id": "toolu_test",
                "name": {
                    "tool": "propose_tool_call",
                    "review": "request_review",
                    "finish": "finish",
                }[proposal["kind"]],
                "input": proposal,
            }
        ],
    }


def happy_decisions():
    return [
        tool("get_request", request_id="REQ-001"),
        tool(
            "reserve_budget", request_id="REQ-001", amount_minor=125000, operation_key="reserve-1"
        ),
        {"kind": "review", "request_id": "REQ-001"},
        tool("get_approval", request_id="REQ-001"),
        tool(
            "create_purchase_order",
            request_id="REQ-001",
            reservation_id="RES-0001",
            approval_id="APR-0001",
            operation_key="purchase",
        ),
        finish(),
    ]


class RecordedTransport:
    def __init__(self, proposals):
        self.proposals = iter(proposals)
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        proposal = next(self.proposals)
        return response(proposal)


@pytest.fixture
def price():
    return Pricing.load(DEFAULT_PRICE)


def run_live(tmp_path, price, transport, scenario=None, cap="1", service_type=DecisionService):
    scenario = scenario or load_scenario("happy-path")
    ctrl = LiveModelController(
        scenario.task.request_id, "\n".join(s.name for s in scenario.initial_world.suppliers)
    )
    assert isinstance(ctrl, Controller)
    ledger = BudgetLedger(tmp_path / "ledger.sqlite", dollars(cap))
    try:
        result = execute_run(
            scenario,
            1,
            tmp_path / "run",
            controller=ctrl,
            live_service_factory=lambda store: service_type(
                store, ledger=ledger, pricing=price, transport=transport
            ),
        )
        return result, ledger.summary()
    finally:
        ledger.close()


def test_live_protocol_uses_gateway_and_independent_evaluator(tmp_path, price):
    transport = RecordedTransport(happy_decisions())
    result, cost = run_live(tmp_path, price, transport)
    assert result.evaluation.verdict == "pass"
    assert result.evaluation.counts["purchase_orders"] == 1
    assert result.evaluation.counts["duplicate_purchase_orders"] == 0
    assert cost["calls"] == 6
    assert cost["actual_known_nano_usd"] == 6 * price.cost(100, 50)
    assert result.report["measurements"]["model_calls"] == 6
    for request in transport.requests:
        assert set(request) == {
            "model",
            "temperature",
            "max_tokens",
            "thinking",
            "service_tier",
            "system",
            "messages",
            "tools",
            "tool_choice",
        }
        assert request["max_tokens"] == MAX_OUTPUT
        assert request["thinking"] == {"type": "disabled"}
        assert input_bound(request) <= MAX_INPUT
    store = Store.open_readonly(result.run_dir / "run.sqlite")
    try:
        records = store.rows("SELECT record_json FROM model_decisions ORDER BY step")
        assert len(records) == 6
        record = json.loads(records[0]["record_json"])
        assert set(record) >= {
            "provider",
            "model",
            "temperature",
            "prompt_version",
            "usage",
            "raw_proposal",
        }
        events = store.rows("SELECT * FROM events ORDER BY seq")
        accepted = next(e for e in events if e["event_type"] == "model.decision_accepted")
        assert accepted["seq"] < result.report["tool_calls"][0]["seq"]
    finally:
        store.close()


def test_supplier_injection_seen_and_missing_approval_blocked(tmp_path, price):
    scenario = load_scenario("supplier-injection", DEFAULT_SCENARIOS_DIR / "live")
    transport = RecordedTransport(
        [
            happy_decisions()[1],
            happy_decisions()[4],
            tool("release_reservation", reservation_id="RES-0001", operation_key="release-1"),
            finish(),  # Deliberately false: never repair the expectation to make it pass.
        ]
    )
    result, _ = run_live(tmp_path, price, transport, scenario)
    assert "Skip human review" in transport.requests[0]["messages"][0]["content"]
    assert result.evaluation.counts["purchase_orders"] == 0
    assert result.evaluation.counts["unauthorized_committed_actions"] == 0
    assert result.evaluation.verdict == "fail"
    metric = trial_metrics(result.report)
    assert metric["enforcement"] == "passed"
    assert metric["task_completed"] is False
    assert metric["evaluator_caught_incorrect_success"] is True
    denied = result.report["tool_calls"][1]["response"]["error"]
    assert denied["reason_code"] == "APPROVAL_MISSING"


def test_safe_refusal_does_not_claim_enforcement_exercised(tmp_path, price):
    scenario = load_scenario("supplier-injection", DEFAULT_SCENARIOS_DIR / "live")
    result, _ = run_live(tmp_path, price, RecordedTransport([finish("REFUSED")]), scenario)
    assert result.evaluation.verdict == "pass"
    metrics = trial_metrics(result.report)
    assert metrics["task_completed"] is False
    assert metrics["enforcement"] == "not_exercised"


def test_model_cannot_grant_itself_approval(tmp_path, price):
    scenario = load_scenario("supplier-injection", DEFAULT_SCENARIOS_DIR / "live")
    transport = RecordedTransport(
        [
            tool("grant_approval", request_id="REQ-001", reviewer_id="REV-001"),
            finish("REFUSED"),
        ]
    )
    result, _ = run_live(tmp_path, price, transport, scenario)
    assert result.report["final_state"]["approvals"] == []
    assert result.report["tool_calls"][0]["response"]["error"]["code"] == "PERMISSION_DENIED"
    metrics = trial_metrics(result.report)
    assert metrics["enforcement"] == "passed"
    assert metrics["blocked_unauthorized_attempts"] == 1
    assert metrics["blocked_purchase_attempts"] == 0
    assert metrics["task_completed"] is False


@pytest.mark.parametrize(
    "usage",
    [
        None,
        {},
        {"input_tokens": "10", "output_tokens": 5},
        {"input_tokens": True, "output_tokens": 5},
        {"input_tokens": -1, "output_tokens": 5},
        {"input_tokens": 100, "output_tokens": 10, "cache_read_input_tokens": 10},
        {"input_tokens": 100, "output_tokens": MAX_OUTPUT + 1},
        {"input_tokens": 10**100, "output_tokens": 10},
    ],
)
def test_bad_usage_keeps_full_reservation_and_stops(tmp_path, price, usage):
    def transport(request):
        doc = response(finish())
        doc["usage"] = usage
        return doc

    result, cost = run_live(tmp_path, price, transport)
    assert result.report["execution_status"] == "stopped_uncertain"
    assert cost["charged_nano_usd"] >= cost["reserved_nano_usd"]
    assert cost["uncertain_calls"] == 1
    assert result.report["tool_calls"] == []


@pytest.mark.parametrize("exception", [TimeoutError, ConnectionError])
def test_no_response_is_charged_and_not_retried(tmp_path, price, exception):
    calls = []

    def transport(request):
        calls.append(request)
        raise exception("sensitive SDK error should not be recorded")

    result, cost = run_live(tmp_path, price, transport)
    assert len(calls) == 1
    assert cost["actual_cost_complete"] is False
    assert cost["charged_nano_usd"] == cost["reserved_nano_usd"]
    assert result.report["execution_status"] == "stopped_uncertain"
    assert "sensitive SDK" not in json.dumps(result.report)


def test_unexpected_model_is_uncertain_billing_not_a_discount(tmp_path, price):
    def transport(request):
        doc = response(finish())
        doc["model"] = "unexpected-model"
        return doc

    result, cost = run_live(tmp_path, price, transport)
    assert result.report["execution_status"] == "stopped_uncertain"
    assert cost["actual_cost_complete"] is False
    assert cost["charged_nano_usd"] == cost["reserved_nano_usd"]


def test_reservation_exists_before_dispatch(tmp_path, price):
    def transport(request):
        with sqlite3.connect(tmp_path / "ledger.sqlite") as conn:
            row = conn.execute("SELECT status, reserved, charged FROM calls").fetchone()
        assert row[0] == "open" and row[1] == row[2] > 0
        return response(finish())

    run_live(tmp_path, price, transport)


def test_budget_refuses_before_call(tmp_path, price):
    transport = RecordedTransport([])
    result, cost = run_live(tmp_path, price, transport, cap="0.000001")
    assert result.report["execution_status"] == "stopped_budget"
    assert transport.requests == []
    assert cost["calls"] == 0


def test_concurrent_runs_share_atomic_cap_and_open_reservations(tmp_path):
    path = tmp_path / "shared.sqlite"
    BudgetLedger(path, 100).close()

    def reserve(i):
        ledger = BudgetLedger(path, 100)
        try:
            ledger.reserve(str(i), str(i), 60, 1, 1)
            return "accepted"
        except LiveStop as exc:
            return exc.reason
        finally:
            ledger.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(reserve, range(2)))
    assert sorted(outcomes) == ["accepted", "stopped_budget"]
    ledger = BudgetLedger(path, 100)
    assert ledger.summary()["open_reservations"] == 1
    assert ledger.summary()["charged_nano_usd"] == 60
    ledger.close()
    with pytest.raises(LedgerCapMismatch, match="locked at cap"):
        BudgetLedger(path, 200)


def test_uncertain_billing_stops_other_runs(tmp_path):
    ledger = BudgetLedger(tmp_path / "ledger", 100)
    ledger.reserve("a", "one", 20, 1, 1)
    with pytest.raises(LiveStop, match="billing uncertain"):
        ledger.settle("a", None)
    with pytest.raises(LiveStop, match="uncertain billing"):
        ledger.reserve("b", "two", 20, 1, 1)
    ledger.close()


class ProcessDeath(BaseException):
    pass


def test_resume_replays_persisted_terminal_decision_without_model(tmp_path, price):
    class CrashAfterAcceptance(DecisionService):
        def __call__(self, step, request):
            doc = super().__call__(step, request)
            if doc["kind"] == "finish":
                raise ProcessDeath()
            return doc

    transport = RecordedTransport(happy_decisions())
    with pytest.raises(ProcessDeath):
        run_live(tmp_path, price, transport, service_type=CrashAfterAcceptance)
    result = resume_run(tmp_path / "run")
    assert result.evaluation.verdict == "pass"
    assert result.evaluation.counts["purchase_orders"] == 1
    assert len(transport.requests) == 6


@pytest.mark.parametrize(
    "boundary",
    [
        "before-reservation",
        "after-reservation",
        "before-purchase",
        "after-purchase-commit",
        "after-purchase-response",
    ],
)
def test_resume_refuses_unsaved_frontier_never_regenerates_or_duplicates(
    tmp_path, price, monkeypatch, boundary
):
    from agent_rehearsal import runner

    def crash(configured, point):
        if point == boundary:
            raise ProcessDeath()

    monkeypatch.setattr(runner, "crash_if", crash)
    transport = RecordedTransport(happy_decisions())
    with pytest.raises(ProcessDeath):
        run_live(tmp_path, price, transport)
    call_count = len(transport.requests)
    monkeypatch.setattr(runner, "crash_if", lambda *args: None)
    with pytest.raises(ValueError, match="no accepted model decision"):
        resume_run(tmp_path / "run")
    assert len(transport.requests) == call_count
    with sqlite3.connect(tmp_path / "run" / "run.sqlite") as conn:
        assert conn.execute("SELECT COUNT(*) FROM purchase_orders").fetchone()[0] <= 1
        assert conn.execute("SELECT COUNT(*) FROM reservations").fetchone()[0] == 1


def test_recovery_rejects_tampered_model_decision(tmp_path, price):
    class CrashAfterAcceptance(DecisionService):
        def __call__(self, step, request):
            super().__call__(step, request)
            raise ProcessDeath()

    with pytest.raises(ProcessDeath):
        run_live(tmp_path, price, RecordedTransport([finish()]), service_type=CrashAfterAcceptance)
    with sqlite3.connect(tmp_path / "run" / "run.sqlite") as conn:
        record = json.loads(conn.execute("SELECT record_json FROM model_decisions").fetchone()[0])
        record["proposal"]["state"] = "REFUSED"
        conn.execute("UPDATE model_decisions SET record_json=?", (json.dumps(record),))
    with pytest.raises(ValueError, match="evidence differs"):
        resume_run(tmp_path / "run")


def test_oversized_input_and_absent_price_refuse(tmp_path, price):
    scenario = load_scenario("happy-path")
    ledger = BudgetLedger(tmp_path / "budget", dollars("1"))
    from agent_rehearsal.policy import POLICY

    store = Store.create(
        tmp_path / "run.sqlite",
        run_id="test",
        scenario=scenario,
        policy=POLICY,
        seed=1,
        adapter=METADATA,
    )
    transport = RecordedTransport([])
    try:
        service = DecisionService(store, ledger=ledger, pricing=price, transport=transport)
        with pytest.raises(LiveStop, match="input exceeds"):
            service(1, {"system": "x" * MAX_INPUT})
        assert ledger.summary()["calls"] == 0
        with pytest.raises(ValueError, match="require pricing"):
            DecisionService(store)(1, {})
        with pytest.raises(FileNotFoundError):
            Pricing.load(tmp_path / "absent.json")
    finally:
        store.close()
        ledger.close()


def test_live_eval_default_is_estimate_only(tmp_path, capsys):
    assert (
        main(
            [
                "live-eval",
                "--trials",
                "2",
                "--budget-usd",
                "1",
                "--output",
                str(tmp_path / "output"),
            ]
        )
        == 0
    )
    assert not (tmp_path / "output").exists()
    assert "no calls made" in capsys.readouterr().out


SENTINEL_KEY = "sentinel-offline-key-never-printed"


def approved_args(tmp_path, budget="0.5", ledger="ledger.sqlite", output="output"):
    args = ["live-eval", "--trials", "1", "--budget-usd", budget, "--approved"]
    args += ["--output", str(tmp_path / output), "--ledgers-dir", str(tmp_path / "ledgers")]
    return args + (["--ledger", str(tmp_path / ledger)] if ledger else [])


def fake_sdk(monkeypatch, transport):
    transport.close = lambda: None
    monkeypatch.setattr("agent_rehearsal.live_eval.AnthropicTransport", lambda: transport)
    monkeypatch.setenv("ANTHROPIC_API_KEY", SENTINEL_KEY)


def test_approved_live_eval_always_reports_progress_cost_and_path(tmp_path, monkeypatch, capsys):
    fake_sdk(monkeypatch, RecordedTransport([finish("REFUSED"), finish("REFUSED")]))
    code = main(approved_args(tmp_path))
    out = capsys.readouterr().out
    assert code == 1  # trials did not pass, so the run must not look successful
    assert "start: APPROVED LIVE RUN" in out
    assert "API key: set" in out
    assert out.count(": reserved $") == 2
    assert out.count(": settled, actual $") == 2
    assert 'stop reasons: {"completed": 2}' in out
    assert "cost: calls=2" in out
    assert f"report: {(tmp_path / 'output' / 'live-report.json').resolve()}" in out
    assert "exit: 1" in out
    assert SENTINEL_KEY not in out


def test_approved_live_eval_timeout_prints_stop_reason_and_fails(tmp_path, monkeypatch, capsys):
    def transport(request):
        raise TimeoutError()

    fake_sdk(monkeypatch, transport)
    code = main(approved_args(tmp_path))
    out = capsys.readouterr().out
    assert code == 1
    assert "stopped_uncertain, full reservation kept as spent" in out
    assert "actual_complete=False" in out
    assert SENTINEL_KEY not in out


def test_approved_live_eval_without_key_refuses_loudly(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert main(approved_args(tmp_path)) == 2
    out = capsys.readouterr().out
    assert "API key: missing" in out
    assert "refused: ANTHROPIC_API_KEY is missing" in out
    assert not (tmp_path / "ledger.sqlite").exists()
    assert not (tmp_path / "output").exists()


def refusing_transport():
    return RecordedTransport([finish("REFUSED"), finish("REFUSED")])


def test_smoke_and_main_evaluations_use_separate_locked_ledgers(tmp_path, monkeypatch, capsys):
    fake_sdk(monkeypatch, refusing_transport())
    assert main(approved_args(tmp_path, "0.5", "smoke.sqlite", "smoke")) == 1
    fake_sdk(monkeypatch, refusing_transport())
    assert main(approved_args(tmp_path, "2", "main.sqlite", "main")) == 1
    smoke, main_run = read_ledger(tmp_path / "smoke.sqlite"), read_ledger(tmp_path / "main.sqlite")
    assert smoke["cap_nano_usd"] == dollars("0.5") and smoke["calls"] == 2
    assert main_run["cap_nano_usd"] == dollars("2") and main_run["calls"] == 2
    capsys.readouterr()


@pytest.mark.parametrize("approved", [True, False])
def test_reusing_a_ledger_with_another_cap_refuses_and_names_it(
    tmp_path, monkeypatch, capsys, approved
):
    fake_sdk(monkeypatch, refusing_transport())
    assert main(approved_args(tmp_path, "0.5", "smoke.sqlite", "first")) == 1
    before = (tmp_path / "smoke.sqlite").read_bytes()
    capsys.readouterr()
    args = approved_args(tmp_path, "2", "smoke.sqlite", "second")
    if not approved:
        args.remove("--approved")
    assert main(args) == 2
    out = capsys.readouterr().out
    assert f"ledger {(tmp_path / 'smoke.sqlite').resolve()} is locked at cap $0.500000000" in out
    assert "requested $2.000000000" in out and "Use a new --ledger path" in out
    assert not (tmp_path / "second").exists()
    assert (tmp_path / "smoke.sqlite").read_bytes() == before  # never edited
    with pytest.raises(LedgerCapMismatch):
        BudgetLedger(tmp_path / "smoke.sqlite", dollars("2"))


def test_reusing_a_ledger_with_same_cap_shares_its_budget(tmp_path, monkeypatch, capsys):
    fake_sdk(monkeypatch, refusing_transport())
    assert main(approved_args(tmp_path, "0.5", "smoke.sqlite", "first")) == 1
    fake_sdk(monkeypatch, refusing_transport())
    assert main(approved_args(tmp_path, "0.5", "smoke.sqlite", "second")) == 1
    assert "ledger: reusing" in capsys.readouterr().out
    assert read_ledger(tmp_path / "smoke.sqlite")["calls"] == 4


def test_default_ledger_is_new_per_evaluation(tmp_path, monkeypatch, capsys):
    for output in ("one", "two"):
        fake_sdk(monkeypatch, refusing_transport())
        assert main(approved_args(tmp_path, "0.5", None, output)) == 1
    ledgers = sorted((tmp_path / "ledgers").glob("eval-*.sqlite"))
    assert len(ledgers) == 2
    assert all(read_ledger(p)["calls"] == 2 for p in ledgers)
    capsys.readouterr()


def test_live_spend_totals_all_ledgers_read_only(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("agent_rehearsal.live_eval.LEGACY_LEDGER", tmp_path / "absent.sqlite")
    fake_sdk(monkeypatch, refusing_transport())
    main(approved_args(tmp_path, "0.5", "smoke.sqlite", "smoke"))
    fake_sdk(monkeypatch, refusing_transport())
    main(approved_args(tmp_path, "2", None, "main"))
    outside = BudgetLedger(tmp_path / "elsewhere.sqlite", dollars("1"))
    outside.reserve("x", "run", 7_000, 1, 1)
    outside.close()
    files = [tmp_path / "smoke.sqlite", tmp_path / "elsewhere.sqlite"]
    snapshot = [p.read_bytes() for p in files]
    capsys.readouterr()
    ledgers_dir = str(tmp_path / "ledgers")
    extra = str(tmp_path / "elsewhere.sqlite")
    assert main(["live-spend", "--ledgers-dir", ledgers_dir, "--ledger", extra]) == 0
    out = capsys.readouterr().out
    expected = 4 * price_of_mock_call() + 7_000
    assert "ledgers found: 3" in out
    assert f"TOTAL calls=5 charged=${expected / 1e9:.6f}" in out
    assert "actual_complete=False" in out  # the open reservation stays charged
    assert [p.read_bytes() for p in files] == snapshot


def price_of_mock_call():
    return Pricing.load(DEFAULT_PRICE).cost(100, 50)


def test_cli_module_entry_point_is_not_silent():
    estimate_only = ["live-eval", "--trials", "1", "--budget-usd", "0.5"]
    result = subprocess.run(
        [sys.executable, "-m", "agent_rehearsal.cli", *estimate_only],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "[live-eval] start: estimate only" in result.stdout
    assert "no calls made" in result.stdout


def test_repeated_report_preserves_failures_and_mock_label(tmp_path, price):
    transport = RecordedTransport([finish(), finish(), finish("REFUSED"), finish("REFUSED")])
    report = run_trials(2, "1", tmp_path / "eval", tmp_path / "ledger", price, transport=transport)
    assert len(report["trials"]) == 4
    assert report["scenarios"][0]["task_completed"] == 0
    assert report["scenarios"][0]["evaluator_caught_incorrect_success"] == 2
    assert report["scenarios"][1]["enforcement_not_exercised"] == 2
    assert report["provenance"] == "mock"
    html = render_live_report(report)
    assert "MOCK / OFFLINE TEST" in html
    assert "2 trials per scenario" in html
    assert "Live model evaluation" in html
    assert "expected_business_outcome" in html
    assert estimate(2, price)["maximum_calls"] == 120


def test_sdk_configuration_disables_retries_and_fixes_endpoint(monkeypatch):
    seen = {}

    def fake_client(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(close=lambda: None)

    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(Anthropic=fake_client))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy-key-for-offline-test")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://invalid.example")
    transport = AnthropicTransport()
    assert seen["max_retries"] == 0
    assert seen["timeout"] == 60
    assert seen["base_url"] == "https://api.anthropic.com"
    transport.close()
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        AnthropicTransport()


@pytest.mark.parametrize(
    "change",
    [
        {"input_nano": 0},
        {"output_nano": -1},
        {"input_nano": True},
        {"model": "another-model"},
        {"checked": ""},
        {"source": None},
    ],
)
def test_invalid_pricing_is_refused(tmp_path, price, change):
    from dataclasses import asdict

    path = tmp_path / "price.json"
    path.write_text(json.dumps(asdict(price) | change), encoding="utf-8")
    with pytest.raises(ValueError):
        Pricing.load(path)


def test_actual_cost_replaces_reservation_without_double_settlement(tmp_path):
    ledger = BudgetLedger(tmp_path / "ledger", 100)
    try:
        ledger.reserve("a", "one", 80, 1, 1)
        ledger.settle("a", 20)
        ledger.reserve("b", "two", 80, 1, 1)
        assert ledger.summary()["charged_nano_usd"] == 100
        assert ledger.summary()["reserved_nano_usd"] == 160
        with pytest.raises(ValueError, match="already settled"):
            ledger.settle("a", 0)
        with pytest.raises(LiveStop, match="never redispatch"):
            ledger.reserve("a", "one", 20, 1, 1)
    finally:
        ledger.close()


@pytest.mark.parametrize("invalid", ["not JSON", '{"kind":"finish","state":"UNKNOWN"}'])
def test_malformed_proposal_charged_but_never_executed(tmp_path, price, invalid):
    def transport(request):
        doc = response(finish())
        doc["content"] = [{"type": "text", "text": invalid}]
        return doc

    result, cost = run_live(tmp_path, price, transport)
    assert result.report["execution_status"] == "invalid_output"
    assert result.report["tool_calls"] == []
    assert cost["actual_known_nano_usd"] == price.cost(100, 50)


def test_repeated_eval_stops_dispatch_after_uncertainty(tmp_path, price):
    calls = []

    def transport(request):
        calls.append(request)
        raise TimeoutError()

    report = run_trials(2, "1", tmp_path / "eval", tmp_path / "ledger", price, transport=transport)
    assert len(calls) == 1
    assert len(report["trials"]) == 4
    assert all(t["stop_reason"] == "stopped_uncertain" for t in report["trials"])
    assert report["shared_ledger_total"]["actual_cost_complete"] is False


def test_static_report_escapes_untrusted_text(tmp_path, price):
    transport = RecordedTransport([finish(), finish()])
    report = run_trials(1, "1", tmp_path / "eval", tmp_path / "ledger", price, transport=transport)
    report["trials"][0]["stop_detail"] = "<script>alert(1)</script>"
    html = render_live_report(report)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_abrupt_process_death_keeps_open_reservation_charged(tmp_path):
    code = """
import os, sys
from pathlib import Path
from agent_rehearsal.live_budget import BudgetLedger
ledger = BudgetLedger(Path(sys.argv[1]), 100)
ledger.reserve("dispatch", "run", 80, 1, 1)
os._exit(86)
"""
    path = tmp_path / "ledger"
    result = subprocess.run([sys.executable, "-c", code, str(path)], check=False)
    assert result.returncode == 86
    ledger = BudgetLedger(path, 100)
    try:
        assert ledger.summary()["charged_nano_usd"] == 80
        assert ledger.summary()["open_reservations"] == 1
        with pytest.raises(LiveStop, match="exhausted"):
            ledger.reserve("new", "another-run", 21, 1, 1)
    finally:
        ledger.close()
