"""Keep current tracked text portable; historical commits are intentionally unchanged."""

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRIVACY_FIXTURE = "tests/fixtures/privacy-paths.json"
URL = re.compile(r"\b[A-Za-z][A-Za-z0-9+.-]+://[^\s<>\"]+")
LOCAL_PATH = re.compile(r"\b[A-Za-z]:[\\/]|\bUsers[\\/]+Asad\b", re.IGNORECASE)


def has_local_path(line: str) -> bool:
    return LOCAL_PATH.search(URL.sub("", line)) is not None


def test_privacy_detector_classifies_explicit_fixtures():
    fixtures = json.loads((ROOT / PRIVACY_FIXTURE).read_text(encoding="utf-8"))
    for fixture in fixtures:
        assert has_local_path(fixture["text"]) == fixture["private"], fixture


def test_tracked_text_files_have_no_local_paths():
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
    failures = []
    for name in tracked.decode("utf-8").split("\0"):
        if not name or name == PRIVACY_FIXTURE:
            continue
        data = (ROOT / name).read_bytes()
        if data.startswith((b"\xff\xfe", b"\xfe\xff")):
            text = data.decode("utf-16")
        elif b"\0" in data:
            continue
        else:
            text = data.decode("utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), 1):
            if has_local_path(line):
                failures.append(f"{name}:{number}: {line}")
    assert not failures, "Tracked local paths\n" + "\n".join(failures)
