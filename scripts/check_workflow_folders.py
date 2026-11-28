#!/usr/bin/env python3
"""Check the '루틴' workflows are split into topic folders (AOM-43).

Usage: check_workflow_folders.py {folders|routines|intact|count}
  folders   1 'Preconquer to main' is in '브랜치 병합', 2 'Upstream scout' in '원본 레포 감시',
            and no workflow is left in '루틴'
  routines  both routines keep their schedule (daily 02:00, weekly Monday 09:00)
  intact    each workflow's steps and suggestion setting are as expected (Upstream scout
            carries the copy line of AOM-51)
  count     no workflow was removed: workflows 1 and 2 are both there
PQ_WORKFLOWS points at another workflows folder (default: the user's auto-ontology-mcp project).
"""

import hashlib
import json
import os
import sys
import unicodedata

DIR = os.environ.get(
    "PQ_WORKFLOWS", os.path.expanduser("~/.preconquer/workflows/auto-ontology-mcp-6805a3ce")
)
# id -> (name, folder, routine, sha256 of steps before the move, suggests)
EXPECTED = {
    1: (
        "Preconquer to main",
        "브랜치 병합",
        {"kind": "daily", "hour": 2, "minute": 0},
        "ec13a42d3f70fe8ce703bbcbb1b007ed2fd3fb44d1f7ec957c7132610161289f",
        None,
    ),
    2: (
        "Upstream scout",
        "원본 레포 감시",
        {"kind": "weekly", "weekdays": [2], "hour": 9, "minute": 0},
        "2916ee5ec7c65ce592f78496fbcd2e11991f41028f4b89c99c573c7e8e07d535",
        False,
    ),
}
OLD = "루틴"


def nfc(text):
    return unicodedata.normalize("NFC", text or "")


def fail(msg):
    print(msg)
    sys.exit(1)


def load():
    out = {}
    for f in os.listdir(DIR):
        if f.endswith(".json") and f[:-5].isdigit():
            out[int(f[:-5])] = json.load(open(os.path.join(DIR, f), encoding="utf-8"))
    return out


def by_id(wfs, i):
    if i not in wfs:
        fail(f"workflow {i} is missing")
    if wfs[i].get("name") != EXPECTED[i][0]:
        fail(f"workflow {i} is named {wfs[i].get('name')!r}, expected {EXPECTED[i][0]!r}")
    return wfs[i]


def folders(wfs):
    for i, (name, folder, *_rest) in EXPECTED.items():
        got = nfc(by_id(wfs, i).get("folder"))
        if got != folder:
            fail(f"{name!r} is in {got!r}, expected {folder!r}")
    left = [w.get("name") for w in wfs.values() if nfc(w.get("folder")) == OLD]
    if left:
        fail(f"still in {OLD!r}: {left}")
    print("folders ok")


def routines(wfs):
    for i, (name, _f, routine, *_rest) in EXPECTED.items():
        got = by_id(wfs, i).get("routine")
        if got != routine:
            fail(f"{name!r} routine is {got}, expected {routine}")
    print("routines ok")


def intact(wfs):
    for i, (name, _f, _r, sha, suggests) in EXPECTED.items():
        w = by_id(wfs, i)
        got = hashlib.sha256(
            json.dumps(w.get("steps"), sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        if got != sha:
            fail(f"{name!r} steps changed")
        if w.get("suggests") != suggests:
            fail(f"{name!r} suggests is {w.get('suggests')}, expected {suggests}")
    print("intact ok")


def count(wfs):
    for i in EXPECTED:
        by_id(wfs, i)
    print(f"count ok: {len(wfs)} workflows")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    checks = {"folders": folders, "routines": routines, "intact": intact, "count": count}
    if mode not in checks:
        fail(__doc__)
    checks[mode](load())
