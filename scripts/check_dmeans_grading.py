#!/usr/bin/env python3
"""Check the d-means project's grading notes sit under one top-level '채점' document (AOM-60).

Usage: check_dmeans_grading.py {nested|backup|intact|index}
  nested  '채점' is at the top and the four DM grading notes sit right under it
  backup  docs-backup-2026-11-28 holds the docs folder as it was before the move
  intact  no document was removed and every document's text is what it was before the move
  index   '채점' links each note with [[Name]] and names the DM tickets it grades
PQ_HUB points at another hub folder (default: the user's d-means project).
"""

import hashlib
import json
import os
import re
import sys
import unicodedata

HUB = os.environ.get("PQ_HUB", os.path.expanduser("~/.preconquer/hub/d-means-26bce00f"))
DOCS = os.path.join(HUB, "docs")
BACKUP = os.path.join(HUB, "docs-backup-2026-11-28")
FOLDER = "채점"
# note name -> tickets it grades
NOTES = {
    "DM-22와 DM-25 채점 결과": ["DM-22", "DM-25"],
    "DM-28 채점 결과": ["DM-28"],
    "DM-30 채점 결과": ["DM-30"],
    "DM-32 채점 결과": ["DM-32"],
}
# sha256 of every document before the move
BEFORE = {
    "d-means 조사와 벤치마크 결과": "1eb66d88aa15e02c46951d5e1c40ef088392b2dd85f049cd9ce39c803bac45e3",
    "d-means 의사결정 모델 3종 벤치마크": "9169cdf80474fa7b463aa5e1fa8db43f4fae6c88645db643fe8eac060091763e",
    "DM-22와 DM-25 채점 결과": "200c27b1c01476cd74e5fbfb5a5ab45fb8509684fabeb5b6db2c4aee5c816af1",
    "DM-28 채점 결과": "42753835419260ba11ad6e64a14586e01a84c0364af1fa7253477316c11f6474",
    "DM-30 채점 결과": "b76082a671a71402981ab098a94d752d4e0e5cb4c1f70af19a3631554a76879b",
    "DM-32 채점 결과": "7e9b857b08a1fdd177857521c1c6d4f7dd915160a01b970581f5637f1d373c16",
}
TREE_BEFORE = "887eeef1736469af7fc297bb75037c79b359ce6ff6667a4a39a8971668c9dddc"


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
    wrong = {n: up.get(n) for n in NOTES if up.get(n) != FOLDER}
    if wrong:
        fail(f"not under '{FOLDER}': {wrong}")
    print(f"nested ok: {len(NOTES)} notes under top-level '{FOLDER}'")


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
    lines = nfc(open(path, encoding="utf-8").read()).splitlines()
    for name, tickets in NOTES.items():
        line = next((ln for ln in lines if f"[[{name}]]" in ln), None)
        if line is None:
            fail(f"'{FOLDER}' does not link [[{name}]]")
        listed = set(re.findall(r"DM-\d+", line.replace(f"[[{name}]]", "")))
        if listed != set(tickets):
            fail(f"line for '{name}' lists {sorted(listed)}, want {tickets}")
    print(f"index ok: '{FOLDER}' links {len(NOTES)} notes with their tickets")


if __name__ == "__main__":
    cmds = {"nested": nested, "backup": backup, "intact": intact, "index": index}
    if len(sys.argv) != 2 or sys.argv[1] not in cmds:
        fail(__doc__)
    cmds[sys.argv[1]]()
