"""Tamper-evident (not tamper-proof) event ledger and local evidence bundle.

Keep the final digest separately to detect an attacker rewriting the whole chain.
The ledger attests to recorded bytes, not the truth of simulated events.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

from agent_rehearsal.canonical import canonical_json, sha256_hex
from agent_rehearsal.recovery import crash_if
from agent_rehearsal.store import Store

GENESIS = "0" * 64
EXPORT_POINTS = (
    "report",
    "summary",
    "anchor",
    "manifest",
    "before-publish",
    "after-backup",
    "after-publish",
)
BUNDLE_FILES = {
    "run.sqlite",
    "report.json",
    "evidence.jsonl",
    "summary.md",
    "manifest.json",
    "evidence-anchor.json",
}


def _discard_export_directory(target: Path, run_dir: Path) -> None:
    # Only delete these two generated sibling directories; reject symlink/path escapes
    # and foreign files before any recursive filesystem operation (also on Windows).
    allowed = {run_dir.name + ".export-stage", run_dir.name + ".export-backup"}
    if (
        target.is_symlink()
        or target.name not in allowed
        or target.resolve().parent != run_dir.resolve().parent
    ):
        raise ValueError("unsafe export cleanup path")
    if target.exists():
        if not target.is_dir() or any(
            p.name not in BUNDLE_FILES or not p.is_file() or p.is_symlink()
            for p in target.iterdir()
        ):
            raise ValueError("foreign files in export directory; refusing cleanup")
        shutil.rmtree(target)


def recover_export(run_dir: Path) -> None:
    if run_dir.is_symlink():
        raise ValueError("symlink run directories are unsupported")
    backup = run_dir.with_name(run_dir.name + ".export-backup")
    stage = run_dir.with_name(run_dir.name + ".export-stage")
    if backup.is_symlink() or stage.is_symlink():
        raise ValueError("symlink export directories are unsupported")
    if backup.exists():
        if not run_dir.exists():
            backup.replace(run_dir)
        elif all((run_dir / name).is_file() for name in BUNDLE_FILES):
            _discard_export_directory(backup, run_dir)
        else:
            raise ValueError("incomplete published bundle with backup; manual review required")
    _discard_export_directory(stage, run_dir)


def export_bundle(run_dir: Path, report: dict, *, crash_during_export: str | None = None) -> None:
    """Publish a whole directory generation, never individual public bundle files.

    The two directory renames can leave the public path absent. Resume restores the
    backup in that case. This is process-crash recovery, not a power-loss guarantee.
    All database handles must be closed, and only one writer may use a run.
    """
    recover_export(run_dir)
    stage = run_dir.with_name(run_dir.name + ".export-stage")
    backup = run_dir.with_name(run_dir.name + ".export-backup")
    stage.mkdir()
    shutil.copyfile(run_dir / "run.sqlite", stage / "run.sqlite")
    _populate_bundle(stage, report, crash_during_export)
    for path in stage.iterdir():
        with path.open("r+b") as handle:
            os.fsync(handle.fileno())
    crash_if(crash_during_export, "before-publish")
    run_dir.replace(backup)
    crash_if(crash_during_export, "after-backup")
    stage.replace(run_dir)
    crash_if(crash_during_export, "after-publish")
    _discard_export_directory(backup, run_dir)


def _populate_bundle(run_dir: Path, report: dict, crash_during_export: str | None) -> None:
    (run_dir / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    crash_if(crash_during_export, "report")
    store = Store.open_readonly(run_dir / "run.sqlite")
    try:
        records = [
            json.loads(r["record_json"])
            for r in store.rows("SELECT record_json FROM evidence_chain ORDER BY record_index")
        ]
    finally:
        store.close()
    ledger = run_dir / "evidence.jsonl"
    ledger.write_text("".join(canonical_json(r) + "\n" for r in records), encoding="utf-8")
    anchor = {"count": len(records), "head": records[-1]["hash"] if records else GENESIS}
    (run_dir / "evidence-anchor.json").write_text(canonical_json(anchor) + "\n", encoding="utf-8")
    crash_if(crash_during_export, "anchor")
    summary = (
        "# Agent Rehearsal evidence\n\nDeterministic controller; synthetic data only.\n\n"
        f"Execution: {report['execution_status']}\n\nOutcome: {report['business_outcome']}\n\n"
        f"Evaluation: {report['verdict']}\n\nEvents: {anchor['count']}\n\n"
        f"Final SHA-256: `{anchor['head']}`\n\n"
        "Tamper-evident (not tamper-proof). Retain the digest separately; a file owner can "
        "rewrite the entire chain and local anchor. No proof of external truth.\n"
    )
    (run_dir / "summary.md").write_text(summary, encoding="utf-8")
    crash_if(crash_during_export, "summary")
    names = ("run.sqlite", "report.json", "evidence.jsonl", "evidence-anchor.json", "summary.md")
    manifest = {name: hashlib.sha256((run_dir / name).read_bytes()).hexdigest() for name in names}
    (run_dir / "manifest.json").write_text(canonical_json(manifest) + "\n", encoding="utf-8")
    crash_if(crash_during_export, "manifest")


def verify_ledger(path: Path, expected_head: str, expected_count: int) -> tuple[bool, int, str]:
    """Return success, first affected zero-based index, and explanation."""
    previous = GENESIS
    count = 0
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        try:
            record = json.loads(line)
            if not isinstance(record, dict) or set(record) != {
                "index",
                "event",
                "previous_hash",
                "hash",
            }:
                return False, index, "invalid record schema"
            if record["index"] != index or record["previous_hash"] != previous:
                return False, index, "index or previous hash mismatch"
            body = {k: v for k, v in record.items() if k != "hash"}
            if record["hash"] != sha256_hex(body):
                return False, index, "record hash mismatch"
            previous = record["hash"]
            count += 1
        except (ValueError, TypeError):
            return False, index, "invalid JSON record"
    if count != expected_count or previous != expected_head:
        return False, min(count, expected_count), "retained count or final digest mismatch"
    return True, count, "chain verified"
