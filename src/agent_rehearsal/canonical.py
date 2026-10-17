"""Serialization helpers shared by every layer.

These are formatting utilities only. They contain no policy decisions, so the
evaluator may use them without inheriting the gateway's judgement.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
TIMESTAMP_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"


def canonical_json(value: object) -> str:
    """Stable JSON: sorted keys, no insignificant whitespace, UTF-8 preserved."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def format_ts(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime(TIMESTAMP_FORMAT)


def parse_ts(text: str) -> datetime:
    return datetime.strptime(text, TIMESTAMP_FORMAT).replace(tzinfo=UTC)
