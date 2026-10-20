"""Failure classification with realistic SDK objects. Offline: no client ever connects.

Responses and errors are real `anthropic` types built locally; the client's
`messages.create` is replaced, so nothing is sent anywhere.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

anthropic = pytest.importorskip("anthropic")
httpx = pytest.importorskip("httpx")

from anthropic.types import Message  # noqa: E402

from agent_rehearsal.cli import main  # noqa: E402
from agent_rehearsal.live import (  # noqa: E402
    DecisionService,
    LiveModelController,
    decision_tools,
    input_bound,
    parse_tool_decision,
    usage_problem,
)
from agent_rehearsal.live_budget import MODEL, BudgetLedger, Pricing, dollars, read_ledger  # noqa: E402
from agent_rehearsal.live_eval import DEFAULT_PRICE, run_trials  # noqa: E402
from agent_rehearsal.live_transport import (  # noqa: E402
    AnthropicTransport,
    TransportFailure,
    validate_api_key,
)
from agent_rehearsal.runner import execute_run  # noqa: E402
from agent_rehearsal.scenarios import load_scenario  # noqa: E402
from agent_rehearsal.store import Store  # noqa: E402

SECRET = "sk-ant-api03-sentinel-never-recorded"
REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
REFUSED = {"kind": "finish", "state": "REFUSED", "summary": "model claim"}


def usage(**changes):
    """Usage exactly as the API returns it for a plain, uncached standard call."""
    return {
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 0},
        "server_tool_use": None,
        "service_tier": "standard",
    } | changes


def message(proposal=REFUSED, model=MODEL, **usage_changes):
    return Message.model_validate(
        {
            "id": "msg_01XFDUDYJgAACzvnptvVoYEL",
            "type": "message",
            "role": "assistant",
            "model": model,
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
            "stop_reason": "tool_use",
            "stop_sequence": None,
            "usage": usage(**usage_changes),
        }
    )


def status_error(cls, status, error_type):
    # The message deliberately carries the key, as an SDK error text might.
    body = {"type": "error", "error": {"type": error_type, "message": f"bad key {SECRET}"}}
    response = httpx.Response(
        status, request=REQUEST, json=body, headers={"request-id": "req_011CTESTONLY"}
    )
    return cls(f"Error code: {status} - {body}", response=response, body=body)


def sdk_transport(outcome):
    """A real AnthropicTransport whose client returns/raises the given SDK object."""
    transport = object.__new__(AnthropicTransport)

    def create(**request):
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    transport.client = SimpleNamespace(messages=SimpleNamespace(create=create), close=lambda: None)
    return transport


@pytest.fixture
def price():
    return Pricing.load(DEFAULT_PRICE)


def run_live(tmp_path, price, transport):
    scenario = load_scenario("happy-path")
    ctrl = LiveModelController(
        scenario.task.request_id, "\n".join(s.name for s in scenario.initial_world.suppliers)
    )
    ledger = BudgetLedger(tmp_path / "ledger.sqlite", dollars("1"))
    try:
        result = execute_run(
            scenario,
            1,
            tmp_path / "run",
            controller=ctrl,
            live_service_factory=lambda store: DecisionService(
                store, ledger=ledger, pricing=price, transport=transport
            ),
        )
        return result, ledger.summary()
    finally:
        ledger.close()


def ledger_rows(tmp_path):
    conn = sqlite3.connect(tmp_path / "ledger.sqlite")
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM calls")]
    finally:
        conn.close()


def events(run_dir: Path, event_type: str) -> list[dict]:
    store = Store.open_readonly(run_dir / "run.sqlite")
    try:
        rows = store.rows("SELECT payload_json FROM events WHERE event_type=?", (event_type,))
        return [json.loads(r["payload_json"]) for r in rows]
    finally:
        store.close()


def assert_no_secret(tmp_path, *texts):
    for text in texts:
        assert SECRET not in text
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert SECRET.encode() not in path.read_bytes(), path


@pytest.mark.parametrize(
    "server_tool_use", [None, {"web_search_requests": 0, "web_fetch_requests": 0}]
)
def test_realistic_zero_cache_and_null_server_tools_settle_normally(
    tmp_path, price, server_tool_use
):
    transport = sdk_transport(message(server_tool_use=server_tool_use))
    result, cost = run_live(tmp_path, price, transport)
    assert result.report["execution_status"] == "completed"
    assert cost["uncertain_calls"] == 0
    assert cost["actual_known_nano_usd"] == price.cost(100, 50)
    assert ledger_rows(tmp_path)[0]["status"] == "settled"
    [received] = events(result.run_dir, "model.response_received")
    assert received["usage_problem"] is None
    assert received["returned_model"] == MODEL


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"cache_read_input_tokens": 10}, "nonzero_cache_read_input_tokens"),
        ({"cache_creation_input_tokens": 7}, "nonzero_cache_creation_input_tokens"),
        (
            {"cache_creation": {"ephemeral_5m_input_tokens": 3, "ephemeral_1h_input_tokens": 0}},
            "nonzero_cache_creation",
        ),
        (
            {"server_tool_use": {"web_search_requests": 1, "web_fetch_requests": 0}},
            "nonzero_server_tool_use",
        ),
        ({"service_tier": "priority"}, "service_tier_not_standard: 'priority'"),
        ({"output_tokens": 2000}, "usage_over_estimate: input 100/"),
    ],
)
def test_nonzero_cache_server_tools_or_overrun_are_uncertain_with_reason(
    tmp_path, price, changes, reason
):
    result, cost = run_live(tmp_path, price, sdk_transport(message(**changes)))
    assert result.report["execution_status"] == "stopped_uncertain"
    assert cost["charged_nano_usd"] >= cost["reserved_nano_usd"]
    [row] = ledger_rows(tmp_path)
    assert row["status"] == "uncertain"
    assert row["reason"].startswith(reason)
    assert reason in result.report["controller"]["summary"]
    assert cost["uncertain_reasons"] == [f"{row['id']}: {row['reason']}"]


@pytest.mark.parametrize("returned", ["claude-haiku-4-5", "claude-haiku-4-5-20260101"])
def test_alias_or_other_dated_model_is_uncertain_and_names_what_returned(tmp_path, price, returned):
    result, _ = run_live(tmp_path, price, sdk_transport(message(model=returned)))
    assert result.report["execution_status"] == "stopped_uncertain"
    [row] = ledger_rows(tmp_path)
    assert row["reason"] == f"model_mismatch: requested {MODEL}, returned {returned!r}"


def test_usage_problem_accepts_real_sdk_dump_with_extra_null_fields():
    dumped = message().model_dump(mode="json")
    assert {"inference_geo", "output_tokens_details"} & set(dumped["usage"])
    assert usage_problem(dumped) is None
    dumped["usage"] = None
    assert usage_problem(dumped) == "missing_usage"


@pytest.mark.parametrize(
    ("cls", "status", "error_type"),
    [
        ("AuthenticationError", 401, "authentication_error"),
        ("BadRequestError", 400, "invalid_request_error"),
        ("PermissionDeniedError", 403, "permission_error"),
        ("RateLimitError", 429, "rate_limit_error"),
    ],
)
def test_http_4xx_rejection_is_error_not_billed_and_records_status(
    tmp_path, price, cls, status, error_type
):
    exc = status_error(getattr(anthropic, cls), status, error_type)
    result, cost = run_live(tmp_path, price, sdk_transport(exc))
    assert result.report["execution_status"] == "error"
    summary = result.report["controller"]["summary"]
    assert f"rejected by API before processing: HTTP {status} {cls}" in summary
    [row] = ledger_rows(tmp_path)
    assert (row["status"], row["charged"], row["actual"]) == ("rejected", 0, 0)
    assert row["reason"] == f"HTTP {status} {cls}"
    assert cost["charged_nano_usd"] == 0 and cost["actual_cost_complete"] is True
    [failed] = events(result.run_dir, "model.call_failed")
    assert failed == {
        "step": 1,
        "call_id": row["id"],
        "exception_class": cls,
        "status_code": status,
        "request_id": "req_011CTESTONLY",
        "billing": "not_billed_rejected",
    }
    assert events(result.run_dir, "model.response_received") == []
    assert_no_secret(tmp_path, json.dumps(result.report))


@pytest.mark.parametrize(
    "exc",
    [
        anthropic.APITimeoutError(request=REQUEST),
        anthropic.APIConnectionError(message=f"reset {SECRET}", request=REQUEST),
        status_error(anthropic.InternalServerError, 500, "api_error"),
        status_error(anthropic.InternalServerError, 529, "overloaded_error"),
    ],
    ids=["timeout", "connection", "http500", "http529"],
)
def test_timeouts_drops_and_5xx_stay_uncertain_with_recorded_reason(tmp_path, price, exc):
    result, cost = run_live(tmp_path, price, sdk_transport(exc))
    assert result.report["execution_status"] == "stopped_uncertain"
    assert cost["charged_nano_usd"] == cost["reserved_nano_usd"] > 0
    [row] = ledger_rows(tmp_path)
    status = getattr(exc, "status_code", None)
    prefix = f"HTTP {status} " if status else ""
    assert row["reason"] == f"transport_failure: {prefix}{type(exc).__name__}"
    [failed] = events(result.run_dir, "model.call_failed")
    assert failed["billing"] == "uncertain" and failed["status_code"] == status
    assert_no_secret(tmp_path, json.dumps(result.report))


def test_transport_failure_never_chains_sdk_exception():
    auth = status_error(anthropic.AuthenticationError, 401, "authentication_error")
    with pytest.raises(TransportFailure) as info:
        sdk_transport(auth)({"model": MODEL})
    assert info.value.__cause__ is None and info.value.__suppress_context__
    assert SECRET not in repr(info.value) and info.value.rejected


def test_rejected_call_does_not_halt_ledger_but_uncertain_does(tmp_path, price):
    auth = sdk_transport(status_error(anthropic.AuthenticationError, 401, "authentication_error"))
    report = run_trials(1, "1", tmp_path / "a", tmp_path / "a.sqlite", price, transport=auth)
    assert [t["stop_reason"] for t in report["trials"]] == ["error", "error"]
    assert report["shared_ledger_total"]["rejected_calls"] == 2
    assert report["shared_ledger_total"]["charged_nano_usd"] == 0

    timeout = sdk_transport(anthropic.APITimeoutError(request=REQUEST))
    report = run_trials(1, "1", tmp_path / "b", tmp_path / "b.sqlite", price, transport=timeout)
    assert report["shared_ledger_total"]["calls"] == 1  # the second trial never dispatched
    detail = report["trials"][1]["stop_detail"]
    assert "uncertain billing from call" in detail
    assert "transport_failure: APITimeoutError" in detail
    assert "no further calls are dispatched" in detail


SENTINEL_KEY = "sentinel-offline-key-never-printed"


def approved(tmp_path, monkeypatch, transport):
    monkeypatch.setattr("agent_rehearsal.live_eval.AnthropicTransport", lambda: transport)
    monkeypatch.setenv("ANTHROPIC_API_KEY", SENTINEL_KEY)
    return [
        "live-eval", "--trials", "1", "--budget-usd", "0.5", "--approved",
        "--output", str(tmp_path / "out"), "--ledgers-dir", str(tmp_path / "ledgers"),
        "--ledger", str(tmp_path / "smoke.sqlite"),
    ]  # fmt: skip


def test_console_explains_auth_rejection(tmp_path, monkeypatch, capsys):
    auth = sdk_transport(status_error(anthropic.AuthenticationError, 401, "authentication_error"))
    assert main(approved(tmp_path, monkeypatch, auth)) == 1
    out = capsys.readouterr().out
    assert "error (rejected by API: HTTP 401 AuthenticationError), not billed" in out
    assert "stop=error" in out and "rejected=2" in out
    assert SENTINEL_KEY not in out and SECRET not in out


def test_console_explains_uncertain_stop_and_why_next_trial_made_no_calls(
    tmp_path, monkeypatch, capsys
):
    timeout = sdk_transport(anthropic.APITimeoutError(request=REQUEST))
    assert main(approved(tmp_path, monkeypatch, timeout)) == 1
    out = capsys.readouterr().out
    assert (
        "full reservation kept as spent (billing uncertain: transport_failure: APITimeoutError)"
        in out
    )
    second = next(line for line in out.splitlines() if "trial supplier-injection #1: stop=" in line)
    assert "calls=0" in second and "no further calls are dispatched on this ledger" in second
    assert "uncertain call run-" in out and SENTINEL_KEY not in out


def test_pre_reason_ledger_is_read_and_migrated(tmp_path):
    path = tmp_path / "old.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE budget (singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            cap INTEGER NOT NULL);
        CREATE TABLE calls (id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
            reserved INTEGER NOT NULL CHECK(reserved>0), charged INTEGER NOT NULL CHECK(charged>=0),
            actual INTEGER, status TEXT NOT NULL, input_bound INTEGER NOT NULL,
            max_tokens INTEGER NOT NULL);
        INSERT INTO budget VALUES (1, 500000000);
        INSERT INTO calls VALUES ('run-x:1', 'run-x', 13943000, 13943000, NULL, 'uncertain',
            8823, 1024);
    """)
    conn.close()
    assert read_ledger(path)["uncertain_reasons"] == ["run-x:1: unrecorded"]
    ledger = BudgetLedger(path, 500_000_000)
    try:
        assert ledger.summary()["uncertain_calls"] == 1
    finally:
        ledger.close()


@pytest.mark.parametrize("entrypoint", ["console", "module"])
def test_installed_console_script_is_not_silent(tmp_path, entrypoint):
    exe = shutil.which("rehearsal", path=str(Path(sys.executable).parent))
    if exe is None:
        pytest.skip("rehearsal console script not installed in this environment")
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    args = ["live-eval", "--trials", "1", "--budget-usd", "0.5"]
    args += ["--ledgers-dir", str(tmp_path / "ledgers")]
    command = [exe] if entrypoint == "console" else [sys.executable, "-m", "agent_rehearsal.cli"]
    result = subprocess.run(
        [*command, *args], capture_output=True, text=True, check=False, env=env, timeout=120
    )
    assert result.returncode == 0, result.stderr
    assert "[live-eval] start: estimate only" in result.stdout
    assert "no calls made" in result.stdout
    assert not (tmp_path / "ledgers").exists()


@pytest.mark.parametrize(
    "proposal",
    [
        {"kind": "tool", "tool": "get_request", "arguments": {"request_id": "REQ-001"}},
        {"kind": "review", "request_id": "REQ-001"},
        REFUSED,
    ],
)
def test_native_decisions_with_realistic_sdk_blocks(proposal):
    doc = message(proposal).model_dump(mode="json")
    doc["content"].insert(0, {"type": "text", "text": "Untrusted commentary"})
    assert parse_tool_decision(doc) == (proposal, proposal)
    definitions = decision_tools()
    assert all(t["input_schema"]["additionalProperties"] is False for t in definitions)
    assert input_bound({"tools": definitions}) > input_bound({})


@pytest.mark.parametrize(
    "case", ["prose", "fenced", "invalid", "wrong_kind", "multiple", "unknown"]
)
def test_invalid_native_output_settles_and_reports_per_scenario(tmp_path, price, case):
    doc = message().model_dump(mode="json")
    if case in {"prose", "fenced"}:
        text = "I will help with this."
        if case == "fenced":
            text = "```json\n" + json.dumps(REFUSED) + "\n```\nTrailing explanation."
        doc.update(content=[{"type": "text", "text": text}], stop_reason="end_turn")
    elif case == "invalid":
        doc["content"][0]["input"] = REFUSED | {"state": "UNKNOWN", "extra": SECRET}
    elif case == "wrong_kind":
        doc["content"][0]["name"] = "request_review"
    elif case == "multiple":
        doc["content"] *= 2
    else:
        doc["content"][0]["name"] = "unknown"
    progress = []
    report = run_trials(
        1,
        "1",
        tmp_path / "eval",
        tmp_path / "ledger",
        price,
        transport=sdk_transport(Message.model_validate(doc)),
        progress=progress.append,
    )
    assert all(t["stop_reason"] == "invalid_output" for t in report["trials"])
    assert all(s["stop_reasons"]["invalid_output"] == 1 for s in report["scenarios"])
    assert all(s["stop_reasons"]["error"] == 0 for s in report["scenarios"])
    assert report["shared_ledger_total"]["actual_known_nano_usd"] == 2 * price.cost(100, 50)
    assert any("invalid_output:" in line for line in progress)
    assert_no_secret(tmp_path, *progress)


@pytest.mark.parametrize(
    ("key", "positions"),
    [(" leading", "1"), ("trailing\n", "9"), ("bad.key", "4"), ("badékey", "4")],
)
def test_invalid_key_refused_before_sdk_import(monkeypatch, key, positions):
    # Isolated fake environment, never installs a real credential.
    monkeypatch.setattr("agent_rehearsal.live_transport.os.environ", {"ANTHROPIC_API_KEY": key})
    with pytest.raises(ValueError) as info:
        validate_api_key()
    assert str(info.value).endswith(": " + positions)
    assert key not in str(info.value)
    with pytest.raises(ValueError):
        AnthropicTransport()
