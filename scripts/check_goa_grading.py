#!/usr/bin/env python3
"""Check the git-ontology-agent grading notes sit under one top-level '채점' document (AOM-62).

Usage: check_goa_grading.py {nested|backup|intact|index}
  nested  '채점' is at the top and the four GOA grading notes and '미션 채점 기록' sit right under it
  backup  docs-backup-2026-11-28 holds the docs folder as it was before the move
  intact  no document was removed and every document's text is what it was before the move
  index   '채점' links each note with [[Name]] and names the GOA tickets it grades
PQ_HUB points at another hub folder (default: the user's git-ontology-agent project).
"""

import hashlib
import json
import os
import re
import sys
import unicodedata

HUB = os.environ.get("PQ_HUB", os.path.expanduser("~/.preconquer/hub/git-ontology-agent-ba60dcde"))
DOCS = os.path.join(HUB, "docs")
BACKUP = os.path.join(HUB, "docs-backup-2026-11-28")
FOLDER = "채점"
OLD_FOLDER = "미션 채점 기록"
# note name -> tickets it grades
NOTES = {
    "GOA-18 채점": ["GOA-18"],
    "GOA-22 채점 다섯 미션": ["GOA-11", "GOA-14", "GOA-15", "GOA-17", "GOA-20"],
    "GOA-23 채점- 76점": ["GOA-23"],
    "GOA-28과 GOA-35 채점": ["GOA-28", "GOA-35"],
}
# sha256 of every document before the move
BEFORE = {
    "미션 채점 기록": "eb64eb6ffebad4c11e9869d739685faffe51640c77c8a64a77fc1b15e5330589",
    "GOA 성능 벤치마크 가이드와 초기 기준 점수": "861a1c3071eb8853afdd6a7a770502c8acdb0b9edd17a7ef6902439d2a1802cd",
    "GOA-18 채점": "ba73d7d96fee199c335e371f5d683bfa5925f9c70f7113eda65e272bdad7034e",
    "GOA-22 채점 다섯 미션": "e25598522e9c4cd0cc3df901e83feeb385297dc11a2b04af34c0777248b55eae",
    "GOA-23 채점- 76점": "fceab55e151734cc7b839a365e0a5fc73dd107e44779e587ae0a12d2e1c29652",
    "GOA-28과 GOA-35 채점": "a5903c2f00a8d63a66bf007b1072597e6a9a320973590747576f399beadd8a04",
}
TREE_BEFORE = "f2cc1b2f9ec7f95bc90193c813b6f6015950912119e8d7b194515d4c4bee1ae0"


def nfc(text):
    return unicodedata.normalize("NFC", text)


def fail(msg):
    print(msg)
    sys.exit(1)


def files(folder):
    return {nfc(f[:-3]): os.path.join(folder, f) for f in os.listdir(folder) if f.endswith(".md")}


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def parents():
    tree = json.load(open(os.path.join(DOCS, ".tree.json"), encoding="utf-8"))
    return {nfc(k): nfc(v) for k, v in tree.get("parents", {}).items()}


def nested():
    up = parents()
    if FOLDER not in files(DOCS):
        fail(f"no '{FOLDER}' document")
    if FOLDER in up:
        fail(f"'{FOLDER}' is not at the top: under '{up[FOLDER]}'")
    wrong = {n: up.get(n) for n in [*NOTES, OLD_FOLDER] if up.get(n) != FOLDER}
    if wrong:
        fail(f"not under '{FOLDER}': {wrong}")
    print(f"nested ok: {len(NOTES)} notes and '{OLD_FOLDER}' under top-level '{FOLDER}'")


def backup():
    if not os.path.isdir(BACKUP):
        fail(f"no backup at {BACKUP}")
    have = files(BACKUP)
    for name, digest in BEFORE.items():
        if name not in have or sha(have[name]) != digest:
            fail(f"backup copy of '{name}' missing or different")
    if set(have) != set(BEFORE):
        fail(f"backup holds {sorted(have)}, want {sorted(BEFORE)}")
    if sha(os.path.join(BACKUP, ".tree.json")) != TREE_BEFORE:
        fail("backup .tree.json is not the pre-move tree")
    print(f"backup ok: {len(have)} documents and the old tree in {BACKUP}")


def intact():
    have = files(DOCS)
    for name, digest in BEFORE.items():
        if name not in have:
            fail(f"missing '{name}'")
        if sha(have[name]) != digest:
            fail(f"text of '{name}' changed")
    print(f"intact ok: all {len(BEFORE)} documents kept with unchanged text")


def index():
    path = files(DOCS).get(FOLDER)
    if not path:
        fail(f"no '{FOLDER}' document")
    text = nfc(open(path, encoding="utf-8").read())
    lines = text.splitlines()
    for name, tickets in NOTES.items():
        line = next((ln for ln in lines if f"[[{name}]]" in ln), None)
        if line is None:
            fail(f"'{FOLDER}' does not link [[{name}]]")
        listed = set(re.findall(r"GOA-\d+", line.replace(f"[[{name}]]", "")))
        if listed != set(tickets):
            fail(f"line for '{name}' lists {sorted(listed)}, want {tickets}")
    if f"[[{OLD_FOLDER}]]" not in text:
        fail(f"'{FOLDER}' does not link [[{OLD_FOLDER}]]")
    print(f"index ok: '{FOLDER}' links {len(NOTES)} notes with their tickets and '{OLD_FOLDER}'")


if __name__ == "__main__":
    cmds = {"nested": nested, "backup": backup, "intact": intact, "index": index}
    if len(sys.argv) != 2 or sys.argv[1] not in cmds:
        fail(__doc__)
    cmds[sys.argv[1]]()
