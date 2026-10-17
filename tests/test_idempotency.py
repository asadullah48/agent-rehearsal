"""Idempotency and logical-intent uniqueness, in the domain layer and in SQLite."""

from __future__ import annotations

import sqlite3

import pytest

from conftest import commit, reserve, snapshot


def _count(world, table: str) -> int:
    return world.store.row(f"SELECT COUNT(*) AS n FROM {table}")["n"]


def _approved(world) -> tuple[str, str]:
    res = reserve(world)
    apr = world.reviewer.grant_approval("REQ-001", 3600)
    return res.data["reservation"]["id"], apr


def test_reserve_same_key_same_payload_replays_original(world):
    first = reserve(world, key="reserve-key-0001")
    second = reserve(world, key="reserve-key-0001")
    assert second.status == "ok" and second.idempotent_replay
    assert second.data == first.data
    assert second.event_ids == first.event_ids
    assert _count(world, "reservations") == 1
    assert world.store.row("SELECT available_minor FROM budgets")["available_minor"] == 875000


def test_reserve_same_key_changed_payload_conflicts_without_mutation(world):
    reserve(world, key="reserve-key-0001")
    before = snapshot(world.store)
    result = reserve(world, key="reserve-key-0001", amount=1)
    assert (result.error.code, result.error.reason_code) == (
        "IDEMPOTENCY_CONFLICT",
        "KEY_REUSED_WITH_DIFFERENT_PAYLOAD",
    )
    assert snapshot(world.store) == before


def test_reserve_new_key_cannot_double_reserve(world):
    reserve(world, key="reserve-key-0001")
    before = snapshot(world.store)
    result = reserve(world, key="reserve-key-0002")
    assert result.error.reason_code == "RESERVATION_EXISTS"
    assert snapshot(world.store) == before


def test_commit_same_key_same_payload_returns_original_po(world):
    rid, apr = _approved(world)
    first = commit(world, rid, apr, key="commit-key-0001")
    again = commit(world, rid, apr, key="commit-key-0001")
    assert first.status == again.status == "ok"
    assert again.idempotent_replay and not first.idempotent_replay
    assert again.data == first.data and again.event_ids == first.event_ids
    assert _count(world, "purchase_orders") == 1
    assert world.store.row("SELECT spent_minor FROM budgets")["spent_minor"] == 125000


def test_commit_same_key_changed_payload_conflicts(world):
    rid, apr = _approved(world)
    commit(world, rid, apr, key="commit-key-0001")
    before = snapshot(world.store)
    result = commit(world, rid, "APR-0002", key="commit-key-0001")
    assert result.error.code == "IDEMPOTENCY_CONFLICT"
    assert snapshot(world.store) == before


def test_new_key_cannot_duplicate_logical_intent(world):
    rid, apr = _approved(world)
    first = commit(world, rid, apr, key="commit-key-0001")
    before = snapshot(world.store)
    dup = commit(world, rid, apr, key="commit-key-0002")
    assert dup.status == "error"
    assert dup.error.reason_code == "DUPLICATE_INTENT"
    assert dup.data["existing_purchase_order_id"] == first.data["purchase_order"]["id"]
    assert snapshot(world.store) == before
    assert _count(world, "purchase_orders") == 1


def test_release_cannot_refund_consumed_reservation(world):
    rid, apr = _approved(world)
    commit(world, rid, apr)
    before = snapshot(world.store)
    result = world.gateway.call(
        "release_reservation", {"reservation_id": rid, "operation_key": "release-key-0001"}
    )
    assert result.error.reason_code == "RESERVATION_CONSUMED"
    assert snapshot(world.store) == before


def test_release_is_idempotent_and_cannot_double_refund(world):
    rid = reserve(world).data["reservation"]["id"]
    args = {"reservation_id": rid, "operation_key": "release-key-0001"}
    first = world.gateway.call("release_reservation", args)
    replay = world.gateway.call("release_reservation", args)
    assert first.status == "ok" and replay.idempotent_replay
    before = snapshot(world.store)
    fresh_key = world.gateway.call(
        "release_reservation", {"reservation_id": rid, "operation_key": "release-key-0002"}
    )
    assert fresh_key.error.reason_code == "RESERVATION_ALREADY_RELEASED"
    assert snapshot(world.store) == before
    budget = world.store.row("SELECT available_minor, spent_minor FROM budgets")
    assert (budget["available_minor"], budget["spent_minor"]) == (1000000, 0)


def test_released_reservation_cannot_be_consumed(world):
    rid, apr = _approved(world)
    world.gateway.call(
        "release_reservation", {"reservation_id": rid, "operation_key": "release-key-0001"}
    )
    result = commit(world, rid, apr)
    assert result.error.reason_code == "RESERVATION_RELEASED"
    assert _count(world, "purchase_orders") == 0


# --- database-level enforcement (second line of defence) ----------------------------


def _raw(world) -> sqlite3.Connection:
    conn = sqlite3.connect(str(world.path))
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def test_sqlite_enforces_unique_logical_intent(world):
    rid, apr = _approved(world)
    commit(world, rid, apr)
    conn = _raw(world)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO purchase_orders VALUES ('PO-0999', 'PI-REQ-001', 'REQ-001', "
                "?, ?, 'SUP-001', 1, 'AED', 'demo-procurement-v1', '2026-01-05T09:00:00Z', "
                "'raw-key-0001')",
                (rid, apr),
            )
    finally:
        conn.close()


def test_sqlite_enforces_idempotency_key_scope(world):
    reserve(world, key="reserve-key-0001")
    conn = _raw(world)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO idempotency VALUES ('CTRL-REF:reserve_budget', 'reserve-key-0001', "
                "'x', '{}')"
            )
    finally:
        conn.close()


def test_sqlite_enforces_one_live_reservation_per_request(world):
    reserve(world)
    conn = _raw(world)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO reservations VALUES ('RES-0999', 'REQ-001', 'BUD-001', 125000, "
                "'active', 1, '2026-01-05T09:00:00Z')"
            )
    finally:
        conn.close()


def test_sqlite_rejects_negative_balances(world):
    conn = _raw(world)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE budgets SET available_minor = -1")
    finally:
        conn.close()
