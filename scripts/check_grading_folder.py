#!/usr/bin/env python3
"""Check a project's grading notes are filed under one top-level '채점' document (AOM-40, AOM-46, AOM-64).

Usage: check_grading_folder.py [--project NAME] {nested|index|intact|backup|count|filed|judge}
  nested  '채점' is at the top and the project's grading notes sit right under it
  index   '채점' links each note with [[Name]] and names the tickets it grades
  intact  no document was removed and every document's text is what it was before the move
  backup  the backup folder holds the docs folder as it was before the move
  count   no document was removed: the recorded documents plus '채점' are all there
  filed   every note named '... 채점 ...' (new ones too) sits under '채점' and has a
          '[[Name]]: AOM-n' line in its list
  judge   the @judge bot's role tells it to write notes with doc_write parent '채점'
          and to add the note and its tickets to the '채점' list
Per-project values (hub, notes and their tickets, sha256 before the move, backup) live in
grading_projects.json next to this script; --project defaults to auto-ontology-mcp.
PQ_GRADING_CONFIG points at another config file, PQ_HUB at another hub folder,
PQ_DOCS at another docs folder, PQ_BOTS at another bots folder (default: ~/.preconquer/bots).
"""

import hashlib
import json
import os
import re
import sys
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
FOLDER = "채점"
TICKET = re.compile(r"[A-Z][A-Z0-9]*-\d+")
DEFAULT_PROJECT = "auto-ontology-mcp"


def nfc(text):
    return unicodedata.normalize("NFC", text)


def fail(msg):
    print(msg)
    sys.exit(1)


class Project:
    def __init__(self, name, cfg):
        self.name = name
        self.hub = os.environ.get("PQ_HUB") or os.path.expanduser(cfg["hub"])
        self.docs = os.environ.get("PQ_DOCS") or os.path.join(self.hub, "docs")
        self.backup = os.path.join(self.hub, cfg["backup"]) if cfg.get("backup") else None
        self.tree_before = cfg.get("tree_before")
        self.old_folder = cfg.get("old_folder")
        self.notes = {nfc(k): v for k, v in cfg.get("notes", {}).items()}
        self.before = {nfc(k): v for k, v in cfg.get("before", {}).items()}
        prefixes = sorted({t.rsplit("-", 1)[0] for ts in self.notes.values() for t in ts})
        self.ticket = re.compile(r"(?:%s)-\d+" % "|".join(map(re.escape, prefixes))) if prefixes else TICKET


def load(name):
    path = os.environ.get("PQ_GRADING_CONFIG") or os.path.join(HERE, "grading_projects.json")
    cfg = json.load(open(path, encoding="utf-8"))
    if name not in cfg:
        fail(f"unknown project '{name}' in {path}: {sorted(cfg)}")
    return Project(name, cfg[name])


def files(folder):
    return {nfc(f[:-3]): os.path.join(folder, f) for f in os.listdir(folder) if f.endswith(".md")}


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def parents(p):
    path = os.path.join(p.docs, ".tree.json")
    tree = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
    return {nfc(k): nfc(v) for k, v in tree.get("parents", {}).items()}


def nested(p):
    up = parents(p)
    if FOLDER not in files(p.docs):
        fail(f"no '{FOLDER}' document")
    if FOLDER in up:
        fail(f"'{FOLDER}' is not at the top: under '{up[FOLDER]}'")
    extra = [p.old_folder] if p.old_folder else []
    wrong = {n: up.get(n) for n in [*p.notes, *extra] if up.get(n) != FOLDER}
    if wrong:
        fail(f"not under '{FOLDER}': {wrong}")
    also = f" and '{p.old_folder}'" if p.old_folder else ""
    print(f"nested ok: {len(p.notes)} notes{also} under top-level '{FOLDER}'")


def backup(p):
    if not p.backup:
        fail(f"no backup folder configured for '{p.name}'")
    if not os.path.isdir(p.backup):
        fail(f"no backup at {p.backup}")
    have = files(p.backup)
    for name, digest in p.before.items():
        if name not in have or sha(have[name]) != digest:
            fail(f"backup copy of '{name}' missing or different")
    if set(have) != set(p.before):
        fail(f"backup holds {sorted(have)}, want {sorted(p.before)}")
    if p.tree_before and sha(os.path.join(p.backup, ".tree.json")) != p.tree_before:
        fail("backup .tree.json is not the pre-move tree")
    print(f"backup ok: {len(have)} documents and the old tree in {p.backup}")


def intact(p):
    have = files(p.docs)
    for name, digest in p.before.items():
        if name not in have:
            fail(f"missing '{name}'")
        if sha(have[name]) != digest:
            fail(f"text of '{name}' changed")
    print(f"intact ok: all {len(p.before)} documents kept with unchanged text")


def index(p):
    path = files(p.docs).get(FOLDER)
    if not path:
        fail(f"no '{FOLDER}' document")
    text = nfc(open(path, encoding="utf-8").read())
    lines = text.splitlines()
    for name, tickets in p.notes.items():
        line = next((ln for ln in lines if f"[[{name}]]" in ln), None)
        if line is None:
            fail(f"'{FOLDER}' does not link [[{name}]]")
        listed = set(p.ticket.findall(line.replace(f"[[{name}]]", "")))
        if listed != set(tickets):
            fail(f"line for '{name}' lists {sorted(listed)}, want {tickets}")
    if p.old_folder and f"[[{p.old_folder}]]" not in text:
        fail(f"'{FOLDER}' does not link [[{p.old_folder}]]")
    also = f" and '{p.old_folder}'" if p.old_folder else ""
    print(f"index ok: '{FOLDER}' links {len(p.notes)} notes with their tickets{also}")


def count(p):
    have = files(p.docs)
    missing = [n for n in [*p.before, FOLDER] if n not in have]
    if missing:
        fail(f"missing: {missing}")
    print(f"count ok: {len(have)} documents, none removed")


def filed(p):
    have = files(p.docs)
    notes = sorted(n for n in have if FOLDER in n and n != FOLDER)
    if FOLDER not in have:
        fail(f"no '{FOLDER}' document")
    up = parents(p)
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


def judge(_p):
    bots = os.environ.get("PQ_BOTS", os.path.expanduser("~/.preconquer/bots"))
    path = os.path.join(bots, "judge.json")
    if not os.path.exists(path):
        fail(f"no @judge bot at {path}")
    role = nfc(json.load(open(path, encoding="utf-8")).get("role", ""))
    need = {"doc_write": "doc_write", "parent": f"parent '{FOLDER}'", "list line": "티켓 줄"}
    missing = [k for k, v in need.items() if v not in role]
    if missing:
        fail(f"@judge role lacks {missing}: {role}")
    print(f"judge ok: @judge files new notes under '{FOLDER}' and lists them")


CMDS = {f.__name__: f for f in (nested, index, intact, backup, count, filed, judge)}


def main(argv):
    project = DEFAULT_PROJECT
    if len(argv) == 3 and argv[0] == "--project":
        project, argv = argv[1], argv[2:]
    if len(argv) != 1 or argv[0] not in CMDS:
        fail(__doc__)
    cmd = CMDS[argv[0]]
    cmd(None if cmd is judge else load(project))


if __name__ == "__main__":
    main(sys.argv[1:])
