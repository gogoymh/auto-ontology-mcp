#!/usr/bin/env python3
"""Watch NVIDIA Auto Ontology, the upstream this project derives from.

The scout bot runs this to turn new upstream work into ticket candidates:

    python3 scripts/upstream_scout.py --up-to-date    # exit 0 when nothing new (cheap: one ls-remote)
    python3 scripts/upstream_scout.py                 # markdown digest of upstream PRs since the last review
    python3 scripts/upstream_scout.py --json          # the same digest as JSON
    python3 scripts/upstream_scout.py --mark-reviewed # record the fetched upstream head as reviewed

The last reviewed upstream commit lives in scripts/upstream_state.json, so it moves only when a
scout mission that filed the tickets is merged. Upstream history is kept in a blobless bare clone
under the user cache folder (a few MB), never in the working tree.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("upstream_scout")

STATE_FILE = Path(__file__).with_name("upstream_state.json")
DEFAULT_REMOTE = "https://github.com/NVIDIA/auto-ontology"

# Upstream files this project adapted (see NOTICE): a change there may need porting.
PORTED: list[tuple[str, list[str]]] = [
    ("auto_ontology/semantic/term_extractor.py", ["guidance.py"]),
    ("auto_ontology/semantic/fk_suggester.py", ["guidance.py", "inference.py"]),
    ("auto_ontology/semantic/semantic_fk.py", ["guidance.py", "inference.py"]),
    ("auto_ontology/semantic/sql_attribute_suggester.py", ["guidance.py", "sqlcheck.py"]),
    ("auto_ontology/retrieval/entity_coverage/prompts.py", ["guidance.py"]),
    ("auto_ontology/retrieval/text_to_sql/prompts.py", ["guidance.py"]),
    ("skills/nvidia-ontology-query/", ["guidance.py"]),
    ("auto_ontology/semantic/visit_enter.py", ["profiling.py"]),
    ("auto_ontology/semantic/date_format.py", ["profiling.py"]),
    ("auto_ontology/retrieval/text_to_sql/db_probe/", ["validation.py", "structure.py"]),
    ("auto_ontology/retrieval/text_to_sql/agents/sql_parse_validation.py", ["structure.py"]),
]

# Upstream areas with a counterpart here that was written independently: ideas may carry over.
RELATED: list[tuple[str, list[str]]] = [
    ("auto_ontology/retrieval/text_to_sql/", ["sqlcheck.py", "validation.py", "structure.py"]),
    ("auto_ontology/retrieval/entity_coverage/", ["service.py"]),
    ("auto_ontology/semantic/", ["semantic.py", "inference.py", "service.py"]),
    ("auto_ontology/catalog/", ["catalog.py"]),
    ("auto_ontology/connectors/", ["datasources.py"]),
    ("auto_ontology/vdb/", ["search.py", "textindex.py"]),
    ("auto_ontology/pii_detection/", ["profiling.py"]),
    ("mcp/", ["server.py"]),
    ("skills/", ["guidance.py"]),
]


def classify(path: str) -> tuple[str, list[str]]:
    """Return ('ported' | 'related' | 'other', local files) for one upstream path."""
    for level, table in (("ported", PORTED), ("related", RELATED)):
        for prefix, local in table:
            if path == prefix or (prefix.endswith("/") and path.startswith(prefix)):
                return level, local
            # semantic/tests/test_fk_suggester.py follows semantic/fk_suggester.py
            if not prefix.endswith("/") and "/tests/" in path:
                head, name = path.rsplit("/tests/", 1)
                if name.startswith("test_") and f"{head}/{name[5:]}" == prefix:
                    return level, local
    return "other", []


def git(*args: str, cwd: Path | None = None) -> str:
    log.debug("git %s", " ".join(args))
    out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=300)
    if out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {out.stderr.strip()}")
    return out.stdout


def load_state(path: Path) -> dict:
    return json.loads(path.read_text())


def remote_head(remote: str) -> str:
    line = git("ls-remote", remote, "HEAD").split()
    if not line:
        raise RuntimeError(f"{remote} has no HEAD")
    return line[0]


def cache_dir(remote: str) -> Path:
    base = os.environ.get("UPSTREAM_SCOUT_CACHE")
    if base:
        root = Path(base)
    else:
        root = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "auto-ontology-mcp"
    name = remote.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git") or "upstream"
    return root / f"{name}.git"


def fetch(remote: str) -> tuple[Path, str]:
    """Update the blobless mirror and return (mirror path, fetched head)."""
    mirror = cache_dir(remote)
    if not (mirror / "HEAD").exists():
        mirror.parent.mkdir(parents=True, exist_ok=True)
        log.info("cloning %s into %s", remote, mirror)
        git("clone", "--quiet", "--bare", "--filter=blob:none", remote, str(mirror))
    git("fetch", "--quiet", "--filter=blob:none", remote, "+HEAD:refs/scout/head", cwd=mirror)
    head = git("rev-parse", "refs/scout/head", cwd=mirror).strip()
    log.info("upstream head %s", head)
    return mirror, head


def digest(mirror: Path, reviewed: str, head: str, web: str) -> dict:
    """Upstream PRs (first-parent commits) after `reviewed` up to `head`, most relevant first."""
    fmt = "%x1e%H%x1f%ad%x1f%s"
    raw = git("log", "--first-parent", "-m", "--name-only", "--date=short", f"--format={fmt}",
              f"{reviewed}..{head}", cwd=mirror)
    rank = {"ported": 0, "related": 1, "other": 2}
    items = []
    for block in raw.split("\x1e")[1:]:
        header, _, files = block.partition("\n")
        sha, date, subject = header.split("\x1f", 2)
        paths = sorted({p for p in files.splitlines() if p.strip()})
        level, local = "other", set()
        for p in paths:
            lv, lf = classify(p)
            if rank[lv] < rank[level]:
                level = lv
            if lv != "other":
                local.update(lf)
        items.append({
            "sha": sha, "date": date, "subject": subject, "level": level,
            "local": sorted(local), "files": paths, "url": f"{web}/commit/{sha}",
        })
    items.sort(key=lambda i: rank[i["level"]])  # stable: newest first within a level
    counts = {k: sum(1 for i in items if i["level"] == k) for k in rank}
    return {
        "remote": web, "reviewed": reviewed, "head": head,
        "compare": f"{web}/compare/{reviewed[:12]}...{head[:12]}",
        "counts": counts, "items": items,
    }


LABEL = {
    "ported": "Touches code adapted here (check whether to port)",
    "related": "Touches an area with a counterpart here (idea may carry over)",
    "other": "Upstream-only (web app, deployment, infrastructure)",
}


def render(d: dict) -> str:
    lines = [f"# Upstream digest: {d['remote']}", "",
             f"Reviewed {d['reviewed'][:12]}, upstream head {d['head'][:12]}: "
             f"{len(d['items'])} new PRs ({d['counts']['ported']} ported, "
             f"{d['counts']['related']} related, {d['counts']['other']} other).",
             f"Compare: {d['compare']}"]
    for level in ("ported", "related", "other"):
        group = [i for i in d["items"] if i["level"] == level]
        if not group:
            continue
        lines += ["", f"## {LABEL[level]}", ""]
        for i in group:
            where = f" -> {', '.join(i['local'])}" if i["local"] else ""
            lines.append(f"- {i['date']} [{i['sha'][:8]}]({i['url']}) {i['subject']}{where}")
            if level != "other":
                order = {"ported": 0, "related": 1}
                shown = sorted((p for p in i["files"] if classify(p)[0] != "other"),
                               key=lambda p: order[classify(p)[0]])[:6]
                lines += [f"  - `{p}`" for p in shown]
    if not d["items"]:
        lines += ["", "Nothing new upstream."]
    return "\n".join(lines) + "\n"


def web_url(remote: str) -> str:
    return remote.removesuffix(".git") if remote.startswith("http") else remote


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--state", type=Path, default=STATE_FILE)
    ap.add_argument("--remote", help="override the upstream remote from the state file")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--up-to-date", action="store_true",
                      help="exit 0 when upstream has nothing past the reviewed commit, 1 when it has")
    mode.add_argument("--mark-reviewed", nargs="?", const="HEAD", metavar="SHA",
                      help="record SHA (default: the fetched upstream head) as reviewed")
    mode.add_argument("--json", action="store_true", help="print the digest as JSON")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, stream=sys.stderr,
                        format="upstream_scout: %(message)s")

    state = load_state(args.state)
    remote = args.remote or state["remote"]
    reviewed = state["reviewed"]

    if args.up_to_date:
        try:
            head = remote_head(remote)
        except Exception as exc:  # offline: report current so no agent is started for nothing
            log.warning("could not reach %s, treating as up to date: %s", remote, exc)
            return 0
        if head == reviewed:
            log.info("up to date at %s", head[:12])
            return 0
        log.info("new upstream work: %s -> %s", reviewed[:12], head[:12])
        return 1

    mirror, head = fetch(remote)
    if args.mark_reviewed:
        sha = head if args.mark_reviewed == "HEAD" else git("rev-parse", args.mark_reviewed, cwd=mirror).strip()
        state.update(reviewed=sha, reviewed_at=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        args.state.write_text(json.dumps(state, indent=2) + "\n")
        log.info("marked %s as reviewed in %s", sha[:12], args.state)
        print(sha)
        return 0

    d = digest(mirror, reviewed, head, web_url(remote))
    log.info("%d new upstream PRs since %s", len(d["items"]), reviewed[:12])
    print(json.dumps(d, indent=2) if args.json else render(d), end="" if not args.json else "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
