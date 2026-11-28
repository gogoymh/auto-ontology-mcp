#!/usr/bin/env python3
"""Check the 'Upstream scout' step copies scripts/upstream_scout.py when main lacks it (AOM-51).

Usage: check_scout_copy.py {line|run|present}
  line     the step's prompt carries the copy line Preconquer accepts, so saving it gives no warning
  run      in a throwaway clone whose main lacks the script, the line makes a temporary copy that lists
           new upstream PRs and whose --mark-reviewed updates the folder's scripts/upstream_state.json
  present  in a folder that has the script, the line makes no copy
PQ_WORKFLOWS points at another workflows folder (default: the user's auto-ontology-mcp project).
No network: a local repository stands in for NVIDIA Auto Ontology.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIR = os.environ.get(
    "PQ_WORKFLOWS", os.path.expanduser("~/.preconquer/workflows/auto-ontology-mcp-6805a3ce")
)
NAME = "upstream_scout"
# What Preconquer's RoutineScripts.copySentence looks for, word for word.
SENTENCE = f'git show "${{PRECONQUER_WORK_BRANCH:-origin/preconquer}}:scripts/{NAME}.py" >'
ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
    "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
}


def fail(msg):
    print(msg)
    sys.exit(1)


def prompt():
    w = json.load(open(os.path.join(DIR, "2.json"), encoding="utf-8"))
    if w.get("name") != "Upstream scout":
        fail(f"workflow 2 is {w.get('name')!r}")
    return w["steps"][0]["prompt"]


def copy_line(text):
    """The shell line the step tells the agent to run as is."""
    m = re.search(r"(\[ -f scripts/upstream_scout\.py \] \|\| \{.*?; \}; \})", text)
    if not m:
        fail("the prompt has no copy line starting with [ -f scripts/upstream_scout.py ] ||")
    return m.group(1)


def line():
    text = prompt()
    if SENTENCE not in text:
        fail(f"the prompt lacks {SENTENCE!r}, so saving it warns that main lacks the script")
    copy_line(text)
    if 'python3 "${TMPDIR:-/tmp}/upstream_scout.py"' not in text:
        fail("the prompt does not say to run the temporary copy")
    print("line ok")


def git(cwd, *args):
    out = subprocess.run(["git", *args], cwd=cwd, env={**os.environ, **ENV}, capture_output=True, text=True)
    if out.returncode:
        fail(f"git {' '.join(args)}: {out.stderr}")
    return out.stdout.strip()


def project(tmp, with_script):
    """A clone whose main has the scout script or not, and a work branch on origin that has it."""
    up = tmp / "upstream"
    up.mkdir()
    git(up, "init", "-q", "-b", "main")
    (up / "README.md").write_text("one\n")
    git(up, "add", "-A")
    git(up, "commit", "-q", "-m", "initial")
    base = git(up, "rev-parse", "HEAD")
    (up / "app.tsx").write_text("page\n")
    git(up, "add", "-A")
    git(up, "commit", "-q", "-m", "Upstream new page")
    head = git(up, "rev-parse", "HEAD")

    origin = tmp / "origin"
    origin.mkdir()
    git(origin, "init", "-q", "-b", "main")
    (origin / "README.md").write_text("project\n")
    git(origin, "add", "-A")
    git(origin, "commit", "-q", "-m", "main")
    git(origin, "checkout", "-q", "-b", "preconquer-someone")
    (origin / "scripts").mkdir()
    (origin / "scripts" / f"{NAME}.py").write_text((ROOT / "scripts" / f"{NAME}.py").read_text())
    (origin / "scripts" / "upstream_state.json").write_text(json.dumps({"remote": str(up), "reviewed": base}))
    git(origin, "add", "-A")
    git(origin, "commit", "-q", "-m", "scout")
    git(origin, "checkout", "-q", "main" if not with_script else "preconquer-someone")
    work = tmp / "work"
    git(tmp, "clone", "-q", "-b", "main" if not with_script else "preconquer-someone", str(origin), str(work))
    return work, head


def run_line(work, tmp):
    env = {k: v for k, v in os.environ.items() if k != "PRECONQUER_WORK_BRANCH"}
    env.update(ENV, TMPDIR=str(tmp / "t"), UPSTREAM_SCOUT_CACHE=str(tmp / "cache"))
    (tmp / "t").mkdir()
    out = subprocess.run(["sh", "-c", copy_line(prompt())], cwd=work, env=env, capture_output=True, text=True)
    if out.returncode:
        fail(f"the copy line failed: {out.stderr}")
    return env, tmp / "t" / f"{NAME}.py"


def run():
    with tempfile.TemporaryDirectory(prefix="pq-scout-copy.") as d:
        tmp = Path(d)
        work, head = project(tmp, with_script=False)
        if (work / "scripts" / f"{NAME}.py").exists():
            fail("the throwaway main should lack the script")
        env, copy = run_line(work, tmp)
        if not copy.exists():
            fail("no temporary copy was made")
        out = subprocess.run([sys.executable, str(copy)], cwd=work, env=env, capture_output=True, text=True)
        if out.returncode or "Upstream new page" not in out.stdout:
            fail(f"the copy did not list the new upstream PR: {out.stdout}{out.stderr}")
        out = subprocess.run([sys.executable, str(copy), "--mark-reviewed", head], cwd=work, env=env,
                             capture_output=True, text=True)
        state = json.loads((work / "scripts" / "upstream_state.json").read_text())
        if out.returncode or state["reviewed"] != head:
            fail(f"--mark-reviewed did not update the folder's state: {out.stderr}")
        print("run ok: the copy listed the new PR and marked it reviewed in the folder's state")


def present():
    with tempfile.TemporaryDirectory(prefix="pq-scout-copy.") as d:
        tmp = Path(d)
        work, _ = project(tmp, with_script=True)
        _, copy = run_line(work, tmp)
        if copy.exists():
            fail("a copy was made although the folder has the script")
        if git(work, "status", "--porcelain"):
            fail("the line changed the folder")
        print("present ok: no copy made")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    checks = {"line": line, "run": run, "present": present}
    if mode not in checks:
        fail(__doc__)
    checks[mode]()
