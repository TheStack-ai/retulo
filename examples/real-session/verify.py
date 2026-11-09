#!/usr/bin/env python3
"""Verify this published fixture's bytes, version records and schedule-only diff.

This does not authenticate historical chat execution or run an agent.
"""
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent


def main():
    record = json.loads((ROOT / "result-v3.json").read_text(encoding="utf-8"))
    v1, v2 = record["versions"]
    assert v1["revision"] == 1 and v2["revision"] == 2
    for version in (v1, v2):
        data = (ROOT / version["path"]).read_bytes()
        actual = hashlib.sha256(data).hexdigest()
        assert actual == version.get("sha256", version.get("sha256_after"))
    assert v1["sha256_before"] == v1["sha256_after"]
    source = record["source"]
    assert hashlib.sha256((ROOT / source["path"]).read_bytes()).hexdigest() == source["sha256_before"] == source["sha256_after"]
    before = (ROOT / v1["path"]).read_text(encoding="utf-8")
    after = (ROOT / v2["path"]).read_text(encoding="utf-8")
    schedule = r"## Schedule[^\n]*\n.*?(?=\n## |\Z)"
    assert re.sub(schedule, "", before, flags=re.S) == re.sub(schedule, "", after, flags=re.S)
    assert "Schedule — four weeks" in before
    assert "Schedule — three weeks" in after
    assert re.findall(r"^- Week (\d):", after, re.M) == ["1", "2", "3"]
    assert record["sessions"]["first_session_id_sha256"] != record["sessions"]["second_session_id_sha256"]
    video = record["video_file"]
    video_bytes = (ROOT / video["path"]).read_bytes()
    assert len(video_bytes) == video["bytes"]
    assert hashlib.sha256(video_bytes).hexdigest() == video["sha256"]
    print(json.dumps({"published_fixture": "passed", "versions": 2,
                      "previous_version_hash_matches": True, "source_hash_matches": True,
                      "video_hash_matches": True,
                      "only_schedule_changed": True,
                      "historical_chat_authentication": "not performed by this file checker"}, indent=2))


if __name__ == "__main__":
    main()
