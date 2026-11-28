#!/usr/bin/env python3
"""Watch NVIDIA Auto Ontology, the upstream this project derives from.

The scout bot runs this to turn new upstream work into ticket candidates:

    python3 scripts/upstream_scout.py --up-to-date    # exit 0 when nothing new (cheap: one ls-remote)
    python3 scripts/upstream_scout.py                 # markdown digest of upstream PRs since the last review
    python3 scripts/upstream_scout.py --json          # the same digest as JSON
    python3 scripts/upstream_scout.py --mark-reviewed # record the fetched upstream head as reviewed
    python3 scripts/upstream_scout.py --diff guidance.py         # upstream changes not yet ported there
    python3 scripts/upstream_scout.py --mark-synced guidance.py  # after porting them

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


def state_file() -> Path:
    """The state beside this script, else the working folder's scripts/upstream_state.json.

    A routine on a main that lacks this script runs a temporary copy taken from the work branch
    (git show ...:scripts/upstream_scout.py > $TMPDIR/upstream_scout.py): that copy keeps reading and
    writing the state of the folder it runs in.
    """
    beside = Path(__file__).with_name("upstream_state.json")
    return beside if beside.exists() else Path.cwd() / "scripts" / "upstream_state.json"


STATE_FILE = state_file()
DEFAULT_REMOTE = "https://github.com/NVIDIA/auto-ontology"

# Upstream files this project adapted (see NOTICE): a change there may need porting. Each entry in
# upstream_state.json "ported" names the local files and the last upstream commit ported into them.
def ported_table(state: dict) -> list[tuple[str, list[str]]]:
    return [(path, entry["local"]) for path, entry in state.get("ported", {}).items()]


PORTED: list[tuple[str, list[str]]] = (
    ported_table(json.loads(STATE_FILE.read_text())) if STATE_FILE.exists() else []
)

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


def ported_entries(state: dict, local: str) -> dict[str, dict]:
    """Upstream paths adapted into one local file (given as a name or a path)."""
    name = Path(local).name
    return {p: e for p, e in state.get("ported", {}).items() if name in e["local"]}


def local_files(state: dict) -> list[str]:
    return sorted({f for e in state.get("ported", {}).values() for f in e["local"]})


def file_diff(mirror: Path, entries: dict[str, dict], head: str, web: str) -> str:
    """Markdown of the upstream commits and diff after each entry's synced commit."""
    lines: list[str] = []
    for path, entry in entries.items():
        synced = entry["synced"]
        log_out = git("log", "--format=%H%x1f%ad%x1f%s", "--date=short", f"{synced}..{head}", "--", path,
                      cwd=mirror)
        commits = [c.split("\x1f", 2) for c in log_out.splitlines() if c.strip()]
        lines += [f"## {path}", "", f"Synced {synced[:12]}, upstream head {head[:12]}: "
                  f"{len(commits)} new commit{'s' if len(commits) != 1 else ''}."]
        if not commits:
            lines += ["", "Up to date.", ""]
            continue
        lines += [f"Compare: {web}/compare/{synced[:12]}...{head[:12]}", ""]
        lines += [f"- {date} [{sha[:8]}]({web}/commit/{sha}) {subject}" for sha, date, subject in commits]
        patch = git("diff", synced, head, "--", path, cwd=mirror)
        lines += ["", "```diff", patch.rstrip("\n"), "```", ""]
    return "\n".join(lines)


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
    mode.add_argument("--diff", metavar="FILE",
                      help="show upstream changes after the commit last ported into a local FILE")
    mode.add_argument("--mark-synced", metavar="FILE",
                      help="record the upstream head (or --at SHA) as ported into a local FILE")
    ap.add_argument("--at", metavar="SHA", help="upstream commit for --mark-synced (default: head)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, stream=sys.stderr,
                        format="upstream_scout: %(message)s")

    if not args.state.exists():
        log.error("no state file at %s: run from the project folder, which keeps scripts/upstream_state.json",
                  args.state)
        return 2
    log.debug("state file %s", args.state)
    state = load_state(args.state)
    if "ported" in state:
        global PORTED
        PORTED = ported_table(state)
    target = args.diff or args.mark_synced
    entries = ported_entries(state, target) if target else {}
    if target and not entries:
        log.error("%s has no ported upstream code; ported files: %s", target, ", ".join(local_files(state)))
        return 2
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

    if args.diff:
        log.info("%d upstream paths ported into %s", len(entries), Path(args.diff).name)
        print(file_diff(mirror, entries, head, web_url(remote)), end="")
        return 0
    if args.mark_synced:
        sha = git("rev-parse", args.at or head, cwd=mirror).strip()
        for entry in entries.values():
            entry["synced"] = sha
        args.state.write_text(json.dumps(state, indent=2) + "\n")
        log.info("marked %s as synced for %s (%d upstream paths)", sha[:12], Path(args.mark_synced).name,
                 len(entries))
        print(sha)
        return 0

    d = digest(mirror, reviewed, head, web_url(remote))
    log.info("%d new upstream PRs since %s", len(d["items"]), reviewed[:12])
    print(json.dumps(d, indent=2) if args.json else render(d), end="" if not args.json else "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
