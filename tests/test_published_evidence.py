"""Published live-eval evidence: renders, leaks nothing private, and is hash-pinned."""

import getpass
import hashlib
import json
import platform
import re
from pathlib import Path

import pytest

from agent_rehearsal.demo import generate_demo
from agent_rehearsal.live_eval import render_live_report

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence"
# Drive-letter fixtures live in the one file the repository privacy scan exempts.
PRIVACY_FIXTURE = ROOT / "tests" / "fixtures" / "privacy-paths.json"
PINNED = {
    "live-eval-2026-10-20.json": (
        "e86a74acd6596df8199665ac041381adb6b636c5b36cd7593b38a1c574fa930e"
    ),
}
URL = re.compile(r"\b[A-Za-z][A-Za-z0-9+.-]+://[^\s<>\"]+")
# Drive letters, UNC shares and user-home paths, matched after URLs are removed.
LOCAL_PATH = re.compile(
    r"\b[A-Za-z]:[\\/]|\\\\[A-Za-z0-9]|\bUsers[\\/]|/home/|~[\\/]|%USERPROFILE%|\$HOME\b",
    re.IGNORECASE,
)
KEY_LIKE = re.compile(
    r"sk-ant|x-api-key|\bbearer\b|api[_-]?key|\"[a-z_]*token[a-z_]*\"\s*:",
    re.IGNORECASE,
)
# Known false positives: pricing field names, not credentials.
ALLOWED = ('"input_nano_usd_per_token":', '"output_nano_usd_per_token":')
REPORTS = sorted(EVIDENCE.glob("*.json"))


def leaks(text: str) -> list[str]:
    for allowed in ALLOWED:
        text = text.replace(allowed, "")
    text = URL.sub("", text)
    found = [m.group() for m in LOCAL_PATH.finditer(text)]
    found += [m.group() for m in KEY_LIKE.finditer(text)]
    for private_name in (getpass.getuser(), platform.node()):
        if private_name and private_name.lower() in text.lower():
            found.append(private_name)
    return found


def test_evidence_directory_holds_pinned_reports():
    assert set(PINNED) <= {path.name for path in REPORTS}


@pytest.mark.parametrize(
    "text, leaked",
    [
        ('"evidence_directory": "happy-path-001"', False),
        ('"pricing_source": "https://platform.claude.com/docs"', False),
        ('"input_nano_usd_per_token": 1000', False),
        ('"path": "/home/runner/out"', True),
        ('"path": "~/out"', True),
        ('"x": "sk-ant-api03-abc"', True),
        ('"headers": {"x-api-key": "v"}', True),
        ('"auth": "Bearer abc"', True),
        ('"access_token": "abc"', True),
    ],
)
def test_leak_detector_classifies_fixtures(text, leaked):
    assert bool(leaks(text)) == leaked


def test_leak_detector_agrees_with_privacy_fixtures():
    for fixture in json.loads(PRIVACY_FIXTURE.read_text(encoding="utf-8")):
        assert bool(leaks(fixture["text"])) == fixture["private"], fixture


@pytest.mark.parametrize("path", REPORTS, ids=lambda p: p.name)
def test_evidence_renders_without_leaks(path: Path):
    text = path.read_text(encoding="utf-8")
    html = render_live_report(json.loads(text))
    assert "Live model evaluation" in html
    assert not leaks(text), path.name
    assert not leaks(html), path.name


@pytest.mark.parametrize("name, digest", PINNED.items())
def test_pinned_evidence_is_byte_identical(name: str, digest: str):
    assert hashlib.sha256((EVIDENCE / name).read_bytes()).hexdigest() == digest


@pytest.mark.parametrize("name, digest", PINNED.items())
def test_site_publishes_pinned_evidence_byte_for_byte(tmp_path: Path, name, digest):
    generate_demo(tmp_path / "site", EVIDENCE / name)
    published = (tmp_path / "site" / "live-report.json").read_bytes()
    assert hashlib.sha256(published).hexdigest() == digest


def test_site_does_not_reserialize_live_report(tmp_path: Path):
    # Compact LF-only input: any re-serialization changes these bytes on every OS.
    source = tmp_path / "compact.json"
    document = json.loads(next(iter(REPORTS)).read_text(encoding="utf-8"))
    source.write_bytes(json.dumps(document, separators=(",", ":")).encode())
    generate_demo(tmp_path / "site", source)
    assert (tmp_path / "site" / "live-report.json").read_bytes() == source.read_bytes()
