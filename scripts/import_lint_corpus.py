#!/usr/bin/env python3
"""Add grading documents, issue bodies and completion criteria from a Preconquer hub folder to the lint corpus.

Usage: python3 scripts/import_lint_corpus.py HUB [--prefix AOM] [--corpus PATH] [--dry-run]

HUB is the project's hub folder (~/.preconquer/hub/<project>) with docs/*.md, issues/*.json and missions/*.json.
Appends to tests/data/lint_reports.jsonl one line (id, source, text) per text that is not there yet:
  doc-AOM-28     source doc        a grading document (name holds '채점' and a ticket number), whole text
  AOM-49-body    source issue      an issue body without its criteria list and the app's '(Nobody asked: …)' wrapper
  AOM-49-k1      source criterion  one completion criterion of the mission that worked on issue 49
Lines already in the corpus are never rewritten, so verdicts in lint_labels.json keep pointing at the same text.
"""
import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "tests/data/lint_reports.jsonl"
# Preconquer wraps a bot's own reason in English boilerplate and the bot's role text; only the reason is the agent's
_WRAPPER = re.compile(
    r"\(Nobody asked: @\S+ decided on its own to take this on, because: (.*?) You are @\S+: .*?"
    r"Preconquer posts the outcome to the channel \S+ when the mission ends\.\)",
    re.S,
)


def log(msg: str) -> None:
    print(f"[import_lint_corpus] {msg}", file=sys.stderr)


def issue_body(body: str, criteria: list[dict]) -> str:
    """The body as its writer wrote it: without the appended criteria list and the app's wrapper."""
    tail = "\n".join(f"{c['id']}. {c['text']}" for c in criteria)
    if tail and body.endswith(tail):
        body = body[: -len(tail)]
    return _WRAPPER.sub(lambda m: m.group(1), body).strip()


def collect(hub: Path, prefix: str) -> list[dict]:
    """Every grading document, issue body and criterion in hub, in a stable order."""
    rows: list[dict] = []
    for doc in sorted((hub / "docs").glob("*.md")):
        name = unicodedata.normalize("NFC", doc.stem)  # macOS hands file names back decomposed
        ticket = re.search(rf"{prefix}-\d+", name)
        if "채점" in name and ticket:  # the index document '채점' only lists links
            rows.append({"id": f"doc-{ticket.group(0)}", "source": "doc", "text": doc.read_text(encoding="utf-8").strip()})
    missions = {}
    for path in (hub / "missions").glob("*.json"):
        m = json.loads(path.read_text(encoding="utf-8"))
        missions[m["id"]] = m
    issues = [json.loads(p.read_text(encoding="utf-8")) for p in (hub / "issues").glob("*.json")]
    for issue in sorted(issues, key=lambda i: i["id"]):
        criteria = (missions.get(issue.get("mission")) or {}).get("criteria") or []
        body = issue_body(issue.get("body", ""), criteria)
        if body:
            rows.append({"id": f"{prefix}-{issue['id']}-body", "source": "issue", "text": body})
        for c in criteria:
            rows.append({"id": f"{prefix}-{issue['id']}-k{c['id']}", "source": "criterion", "text": c["text"].strip()})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("hub", type=Path)
    ap.add_argument("--prefix", default="AOM", help="ticket prefix of the project")
    ap.add_argument("--corpus", type=Path, default=CORPUS)
    ap.add_argument("--dry-run", action="store_true", help="count what would be added and write nothing")
    args = ap.parse_args()
    if not (args.hub / "issues").is_dir():
        print(f"no issues folder under {args.hub}")
        return 2
    have = {json.loads(line)["id"] for line in args.corpus.read_text(encoding="utf-8").splitlines() if line.strip()}
    rows = collect(args.hub, args.prefix)
    new = [r for r in rows if r["id"] not in have]
    counts = {s: sum(r["source"] == s for r in new) for s in ("doc", "issue", "criterion")}
    if new and not args.dry_run:
        with open(args.corpus, "a", encoding="utf-8") as fh:
            fh.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in new)
    print(f"found={len(rows)} added={len(new)} doc={counts['doc']} issue={counts['issue']} criterion={counts['criterion']}")
    log(f"hub={args.hub} corpus={args.corpus} found={len(rows)} new={len(new)} dry_run={args.dry_run}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
