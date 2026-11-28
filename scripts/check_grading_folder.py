#!/usr/bin/env python3
"""Check the grading notes are filed under one '채점' folder document (AOM-40, AOM-46).

Usage: check_grading_folder.py {nested|index|intact|count|filed|judge}
  nested  the four grading notes sit under '채점' in the hub's .tree.json
  index   '채점' links each note with [[Name]] and lists the tickets that note grades
  intact  each note's text is byte-for-byte what it was before the move
  count   no document was removed: the four notes plus '채점' are all there
  filed   every note named '... 채점 ...' (new ones too) sits under '채점' and has a
          '[[Name]]: AOM-n' line in its list
  judge   the @judge bot's role tells it to write notes with doc_write parent '채점'
          and to add the note and its tickets to the '채점' list
PQ_DOCS points at another docs folder (default: the user's auto-ontology-mcp project).
PQ_BOTS points at another bots folder (default: ~/.preconquer/bots).
"""

import hashlib
import json
import os
import re
import sys
import unicodedata

DOCS = os.environ.get(
    "PQ_DOCS", os.path.expanduser("~/.preconquer/hub/auto-ontology-mcp-6805a3ce/docs")
)
BOTS = os.environ.get("PQ_BOTS", os.path.expanduser("~/.preconquer/bots"))
FOLDER = "채점"
TICKET = re.compile(r"[A-Z][A-Z0-9]*-\d+")
# note name -> (sha256 of its text before the move, tickets it grades)
NOTES = {
    "AOM-28 채점": ("f53578278305f2c807f16f8c7169482a08cfa1767b48ca55d7374a4f8993bb10", ["AOM-28"]),
    "AOM-25 채점": ("308de6b802bc6477ddd9459b816299a565e4a87f4d2fbab4697769674e9d76d6", ["AOM-25"]),
    "AOM-20, AOM-21, AOM-22 채점": (
        "32acc02547ca44defd1ef132293b1c95afb7f4ed62bf9ea13a226507d8bb7d24",
        ["AOM-20", "AOM-21", "AOM-22"],
    ),
    "AOM-19 채점- AOM-13, AOM-5, AOM-14, AOM-17": (
        "e7abf3c6e9c3be2910eee4412b0a17ea521378a40cb268575f65033e64b69ed3",
        ["AOM-5", "AOM-13", "AOM-14", "AOM-17"],
    ),
}


def nfc(text):
    return unicodedata.normalize("NFC", text)


def files():
    return {nfc(f[:-3]): os.path.join(DOCS, f) for f in os.listdir(DOCS) if f.endswith(".md")}


def fail(msg):
    print(msg)
    sys.exit(1)


def nested():
    tree = json.load(open(os.path.join(DOCS, ".tree.json"), encoding="utf-8"))
    parents = {nfc(k): nfc(v) for k, v in tree.get("parents", {}).items()}
    wrong = [n for n in NOTES if parents.get(n) != FOLDER]
    if FOLDER in parents:
        fail(f"'{FOLDER}' is not at the top: under '{parents[FOLDER]}'")
    if wrong:
        fail(f"not under '{FOLDER}': {wrong}")
    print(f"nested ok: {len(NOTES)} notes under '{FOLDER}'")


def index():
    path = files().get(FOLDER)
    if not path:
        fail(f"no '{FOLDER}' document")
    text = nfc(open(path, encoding="utf-8").read())
    for name, (_, tickets) in NOTES.items():
        line = next((ln for ln in text.splitlines() if f"[[{name}]]" in ln), None)
        if line is None:
            fail(f"'{FOLDER}' does not link [[{name}]]")
        listed = set(re.findall(r"AOM-\d+", line.replace(f"[[{name}]]", "")))
        if listed != set(tickets):
            fail(f"line for '{name}' lists {sorted(listed)}, want {tickets}")
    print(f"index ok: '{FOLDER}' links {len(NOTES)} notes with their tickets")


def intact():
    have = files()
    for name, (sha, _) in NOTES.items():
        if name not in have:
            fail(f"missing note '{name}'")
        got = hashlib.sha256(open(have[name], "rb").read()).hexdigest()
        if got != sha:
            fail(f"text of '{name}' changed")
    print(f"intact ok: {len(NOTES)} notes unchanged")


def count():
    missing = [n for n in [*NOTES, FOLDER] if n not in files()]
    if missing:
        fail(f"missing: {missing}")
    print(f"count ok: {len(files())} documents, none removed")


def parents():
    path = os.path.join(DOCS, ".tree.json")
    tree = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
    return {nfc(k): nfc(v) for k, v in tree.get("parents", {}).items()}


def filed():
    have = files()
    notes = sorted(n for n in have if FOLDER in n and n != FOLDER)
    if FOLDER not in have:
        fail(f"no '{FOLDER}' document")
    up = parents()
    if FOLDER in up:
        fail(f"'{FOLDER}' is not at the top: under '{up[FOLDER]}'")
    loose = [n for n in notes if up.get(n) != FOLDER]
    if loose:
        fail(f"not under '{FOLDER}': {loose}")
    lines = nfc(open(have[FOLDER], encoding="utf-8").read()).splitlines()
    for name in notes:
        line = next((ln for ln in lines if f"[[{name}]]" in ln), None)
        if line is None:
            fail(f"'{FOLDER}' does not list [[{name}]]")
        if not TICKET.search(line.replace(f"[[{name}]]", "")):
            fail(f"line for '{name}' names no ticket")
    print(f"filed ok: {len(notes)} grading notes under '{FOLDER}' and listed with tickets")


def judge():
    path = os.path.join(BOTS, "judge.json")
    if not os.path.exists(path):
        fail(f"no @judge bot at {path}")
    role = nfc(json.load(open(path, encoding="utf-8")).get("role", ""))
    need = {
        "doc_write": "doc_write",
        "parent": f"parent '{FOLDER}'",
        "list line": "티켓 줄",
    }
    missing = [k for k, v in need.items() if v not in role]
    if missing:
        fail(f"@judge role lacks {missing}: {role}")
    print(f"judge ok: @judge files new notes under '{FOLDER}' and lists them")


if __name__ == "__main__":
    cmds = {
        "nested": nested,
        "index": index,
        "intact": intact,
        "count": count,
        "filed": filed,
        "judge": judge,
    }
    if len(sys.argv) != 2 or sys.argv[1] not in cmds:
        fail(__doc__)
    cmds[sys.argv[1]]()
