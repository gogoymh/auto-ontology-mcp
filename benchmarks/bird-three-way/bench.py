#!/usr/bin/env python3
"""Plain vs LLM Wiki vs auto-ontology MCP on BIRD Mini-Dev (SQLite, 2026 annotations).

Every condition runs the same model through headless Claude Code (`claude -p`) with the
user's CLAUDE.md, plugins and other MCP servers switched off, and has the same raw sources:
the database and BIRD's column description CSVs. What differs is the layer in between:

  plain     the database (sqlite3 through Bash) and docs/ only
  wiki      plus wiki/, an LLM Wiki (Karpathy's pattern) the same model built from those sources
  ontology  the auto-ontology MCP server with an ontology the same model built from those
            sources; no Bash, so SQL goes through run_sql

Questions are asked without BIRD's evidence hints: the knowledge they carry has to come
from the docs, the wiki or the ontology. Each question runs in a fresh copy of the
workspace (and of the ontology store), so nothing one answer writes reaches another.

Scoring is BIRD's strict execution accuracy (EX): the predicted query's rows, as a set,
equal the gold query's rows.

Usage (data goes to $BENCH_ROOT, default ~/auto-ontology-bench):
  python3 bench.py prepare
  python3 bench.py build --condition wiki|ontology [--db NAME ...]
  python3 bench.py run --name main [--conditions plain wiki ontology] [--trials 1] [--jobs 4]
  python3 bench.py score --name main
  python3 bench.py report --name main
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import random
import shutil
import sqlite3
import statistics
import subprocess
import sys
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
ROOT = Path(os.environ.get("BENCH_ROOT", "~/auto-ontology-bench")).expanduser()
DATA = ROOT / "data"
BUILDS = ROOT / "builds"
RUNS = ROOT / "runs"
GOLD_FILE = ROOT / "hf" / "data" / "mini_dev_sqlite.jsonl"
# Karpathy's LLM Wiki gist, handed to the wiki builder as LLM_WIKI.md. Downloaded, not vendored (no license stated).
GIST_URL = "https://gist.githubusercontent.com/karpathy/442a6bf555914893e9891c11519de94f/raw"
GIST_FILE = ROOT / "llm_wiki_gist.md"
GIST_SHA256 = "dc3efe98ae62f23dd08acad13aba2e95287beb20b6bec2f4af0423557fe37401"  # the revision the published results used
ZIP_FILE = ROOT / "download" / "minidev.zip"
RESULTS = HERE / "results"
# 2025-07-04 release of the same questions' databases: terse questions in business terms, meaning in the evidence.
GOLD_2025_FILE = ROOT / "hf" / "archive" / "mini_dev_sqlite_2025.json"
QUESTION_SETS = {"dev": HERE / "questions.json", "test": HERE / "questions_test.json", "bird2025": HERE / "questions_bird2025.json"}
GOLD_FILES = {"dev": GOLD_FILE, "test": GOLD_FILE, "bird2025": GOLD_2025_FILE}

DBS = ["california_schools", "financial", "thrombosis_prediction", "toxicology"]
PER_DB = 15
SEED = 20261109
MODEL = os.environ.get("BENCH_MODEL", "sonnet")
SERVER = Path(os.environ.get("BENCH_SERVER", REPO / ".venv" / "bin" / "auto-ontology-mcp")).expanduser()
CONDITIONS = ["plain", "wiki", "ontology", "ontology-answer", "ontology-v1", "plain-evidence"]
# plain-evidence: plain with BIRD's evidence hint appended to the question, as an upper bound for what the
# knowledge layers are meant to supply.
# ontology-v1: the server before the changes in this branch (BENCH_SERVER_V1, a checkout of d6bf3d0) with the
# ontologies that version built (builds/ontology-run1), to compare versions on questions neither was tuned on.
SERVERS = {"ontology-v1": Path(os.environ.get("BENCH_SERVER_V1", "~/auto-ontology-bench/server-v1/.venv/bin/auto-ontology-mcp")).expanduser()}
ONTOLOGY_BUILD = {"ontology-v1": "ontology-run1"}
# ontology-answer: the same ontology, served with AUTO_ONTOLOGY_TOOLSETS=answer (question-answering tools only).
TOOLSETS = {"ontology": None, "ontology-answer": "answer"}

QUESTION_BUDGET_USD = 5.0
BUILD_BUDGET_USD = 25.0
QUESTION_TIMEOUT_S = 20 * 60
BUILD_TIMEOUT_S = 90 * 60
SQL_TIMEOUT_S = 60

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {"sql": {"type": "string"}, "answer": {"type": "string"}},
    "required": ["sql", "answer"],
}

PREAMBLE = {
    "plain": (
        "You are answering a question about a SQLite database. The current folder is all you have: "
        "`db.sqlite` (query it read-only with the `sqlite3` command) and `docs/`, CSV files that describe "
        "each table's columns and the meaning of their values."
    ),
    "wiki": (
        "You are answering a question about a SQLite database. The current folder holds `wiki/`, an LLM Wiki "
        "about this database (start with `wiki/index.md`; `wiki/SCHEMA.md` describes its conventions), and the "
        "wiki's raw sources: `db.sqlite` (query it read-only with the `sqlite3` command) and `docs/`, CSV files "
        "that describe each table's columns and the meaning of their values. Do not edit the wiki."
    ),
    "ontology": (
        "You are answering a question about a SQLite database through the auto-ontology MCP tools. The database "
        "is the datasource `{db}`, and its ontology has been built. The current folder holds `docs/`, CSV files "
        "that describe each table's columns and the meaning of their values."
    ),
}

# Strings that would show a session reaching past its workspace, e.g. for the gold SQL.
LEAK_MARKERS = ["mini_dev", "MINIDEV", "dev_databases", "orca/auto-ontology-mcp", "auto-ontology-bench/hf",
                "auto-ontology-bench/download", "auto-ontology-bench/data", "auto-ontology-bench/builds"]

_lock = threading.Lock()


# ----------------------------------------------------------------------------- helpers
def log(msg: str) -> None:
    with _lock:
        print(time.strftime("%H:%M:%S"), msg, flush=True)


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def append_jsonl(path: Path, row: dict) -> None:
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def link_db(db: str, dest: Path) -> None:
    """Hard-link the read-only database so the workspace path does not reveal where the data lives."""
    os.link(DATA / db / "db.sqlite", dest / "db.sqlite")


def copy_docs(db: str, dest: Path) -> None:
    shutil.copytree(DATA / db / "docs", dest / "docs")


def server_env(home: Path, *, datasource_tools: bool) -> dict:
    return {
        "AUTO_ONTOLOGY_HOME": str(home),
        "AUTO_ONTOLOGY_ALLOW_DATASOURCE_TOOLS": "1" if datasource_tools else "0",
    }


def mcp_config(home: Path | None, *, datasource_tools: bool = False, toolsets: str | None = None, server: Path = SERVER) -> str:
    if home is None:
        return json.dumps({"mcpServers": {}})
    env = server_env(home, datasource_tools=datasource_tools)
    if toolsets:
        env["AUTO_ONTOLOGY_TOOLSETS"] = toolsets
    server = {"command": str(server), "args": [], "env": env}
    return json.dumps({"mcpServers": {"auto-ontology": server}})


def claude(prompt: str, *, cwd: Path, tools: str, mcp: str, transcript: Path, budget: float, timeout: int,
           schema: dict | None = None) -> dict:
    """Run one headless Claude Code session and return its result event plus tool-call stats."""
    cmd = [
        "claude", "-p", prompt,
        "--model", MODEL,
        "--setting-sources", "project",
        "--disable-slash-commands",
        "--strict-mcp-config", "--mcp-config", mcp,
        "--tools", tools,
        "--permission-mode", "bypassPermissions",
        "--output-format", "stream-json", "--verbose",
        "--no-session-persistence",
        "--max-budget-usd", str(budget),
    ]
    if schema:
        cmd += ["--json-schema", json.dumps(schema)]
    env = {**os.environ, "ENABLE_CLAUDEAI_MCP_SERVERS": "false"}
    transcript.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    timed_out = False
    with transcript.open("w") as out:
        proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT)
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            timed_out = True
    return parse_transcript(transcript) | {"wall_s": round(time.time() - started, 1), "timed_out": timed_out}


def parse_transcript(path: Path) -> dict:
    result: dict = {}
    tools: collections.Counter = collections.Counter()
    init: dict = {}
    leaks: set[str] = set()
    for line in path.read_text(errors="replace").splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "system" and ev.get("subtype") == "init":
            init = {"model": ev.get("model"), "tools": ev.get("tools"), "mcp_servers": ev.get("mcp_servers")}
        elif ev.get("type") == "assistant":
            for block in ev.get("message", {}).get("content", []):
                if block.get("type") == "tool_use":
                    tools[block["name"]] += 1
                    text = json.dumps(block.get("input"))
                    leaks.update(m for m in LEAK_MARKERS if m in text)
        elif ev.get("type") == "result":
            result = ev
    structured = result.get("structured_output") or {}
    usage = result.get("usage") or {}
    return {
        "sql": structured.get("sql", ""),
        "answer": structured.get("answer", ""),
        "subtype": result.get("subtype"),
        "is_error": result.get("is_error", True) if result else True,
        "cost_usd": result.get("total_cost_usd"),
        "duration_ms": result.get("duration_ms"),
        "num_turns": result.get("num_turns"),
        "output_tokens": usage.get("output_tokens"),
        "tool_calls": sum(tools.values()),
        "tools": dict(tools),
        "model": init.get("model"),
        "mcp_servers": init.get("mcp_servers"),
        "leaks": sorted(leaks),
        "final_text": result.get("result") if not structured else None,
    }


def read_gold(name: str) -> list[dict]:
    """BIRD's records for a question set: a JSON Lines file (2026) or a JSON array (2025 release)."""
    text = GOLD_FILES[name].read_text()
    return json.loads(text) if text.lstrip().startswith("[") else [json.loads(x) for x in text.splitlines() if x.strip()]


def load_questions(name: str) -> list[dict]:
    """A question set (ids and metadata, as committed) joined with BIRD's question text and evidence."""
    gold = {r["question_id"]: r for r in read_gold(name)}
    return [q | {"question": gold[q["question_id"]]["question"], "evidence": gold[q["question_id"]]["evidence"]}
            for q in json.loads(QUESTION_SETS[name].read_text())]


def fetch_gist() -> Path:
    if not GIST_FILE.exists():
        import urllib.request

        GIST_FILE.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(GIST_URL, timeout=60) as resp:
            GIST_FILE.write_bytes(resp.read())
    import hashlib

    if hashlib.sha256(GIST_FILE.read_bytes()).hexdigest() != GIST_SHA256:
        log(f"note: {GIST_FILE} differs from the gist revision the published results used")
    return GIST_FILE


# ----------------------------------------------------------------------------- prepare
def cmd_prepare(args: argparse.Namespace) -> None:
    with zipfile.ZipFile(ZIP_FILE) as z:
        names = z.namelist()
        for db in DBS:
            dest = DATA / db
            if (dest / "db.sqlite").exists():
                log(f"{db}: already extracted")
                continue
            (dest / "docs").mkdir(parents=True, exist_ok=True)
            sqlite_name = next(n for n in names if n.endswith(f"/dev_databases/{db}/{db}.sqlite"))
            with z.open(sqlite_name) as src, (dest / "db.sqlite").open("wb") as out:
                shutil.copyfileobj(src, out)
            (dest / "db.sqlite").chmod(0o444)
            for n in names:
                if f"/dev_databases/{db}/database_description/" in n and n.endswith(".csv"):
                    with z.open(n) as src, (dest / "docs" / Path(n).name).open("wb") as out:
                        shutil.copyfileobj(src, out)
            log(f"{db}: {(dest / 'db.sqlite').stat().st_size / 1e6:.1f} MB, docs {sorted(p.name for p in (dest / 'docs').iterdir())}")

    rows = read_gold(args.set)
    # dev: the questions the first run used (and the fixes were made against); test: held out from dev;
    # bird2025: every question of the four databases in the 2025 release.
    rng = random.Random(SEED if args.set == "dev" else SEED + 1)
    exclude = {q["question_id"] for q in load_questions("dev")} if args.set == "test" else set()
    picked = []
    for db in DBS:
        pool = sorted((r for r in rows if r["db_id"] == db and r["question_id"] not in exclude), key=lambda r: r["question_id"])
        for r in pool if args.set == "bird2025" else sorted(rng.sample(pool, PER_DB), key=lambda r: r["question_id"]):
            picked.append({"question_id": r["question_id"], "db_id": db,
                           "difficulty": r["difficulty"], "has_evidence": bool(r["evidence"])})
    out = QUESTION_SETS[args.set]
    if out.exists() and not args.force:
        log(f"{out} exists; keeping it (pass --force to resample)")
        return
    out.write_text(json.dumps(picked, indent=1, ensure_ascii=False) + "\n")
    log(f"{len(picked)} questions written to {out}")


# ----------------------------------------------------------------------------- build
def render_build_ontology_prompt(db: str) -> str:
    """The server's own build_ontology prompt, so the build follows the product's documented workflow."""
    code = (
        "import asyncio, json, sys\n"
        "from fastmcp import Client\n"
        "from auto_ontology_mcp.server import build_server\n"
        "async def main():\n"
        "    async with Client(build_server()) as c:\n"
        "        r = await c.get_prompt('build_ontology', {'datasource': sys.argv[1]})\n"
        "        print(r.messages[0].content.text)\n"
        "asyncio.run(main())\n"
    )
    env = {**os.environ, **server_env(BUILDS / "ontology" / db / "home", datasource_tools=True)}
    out = subprocess.run([str(REPO / ".venv" / "bin" / "python"), "-c", code, db], env=env, capture_output=True, text=True, check=True)
    return out.stdout.strip()


def build_one(condition: str, db: str) -> dict:
    base = BUILDS / condition / db
    ws = base / "workspace"
    if (base / "build.json").exists():
        log(f"build {condition}/{db}: done already")
        return json.loads((base / "build.json").read_text())
    if ws.exists():
        shutil.rmtree(ws)
    ws.mkdir(parents=True)
    copy_docs(db, ws)
    if condition == "wiki":
        link_db(db, ws)
        shutil.copy(fetch_gist(), ws / "LLM_WIKI.md")
        prompt = (HERE / "prompts" / "build_wiki.md").read_text().format(db=db)
        res = claude(prompt, cwd=ws, tools="Bash,Read,Write,Edit,Grep,Glob", mcp=mcp_config(None),
                     transcript=base / "transcript.jsonl", budget=BUILD_BUDGET_USD, timeout=BUILD_TIMEOUT_S)
        pages = sorted(str(p.relative_to(ws / "wiki")) for p in (ws / "wiki").rglob("*.md")) if (ws / "wiki").exists() else []
        res["pages"] = len(pages)
        res["wiki_bytes"] = sum(p.stat().st_size for p in (ws / "wiki").rglob("*") if p.is_file()) if pages else 0
    else:
        home = base / "home"
        if home.exists():
            shutil.rmtree(home)
        env = {**os.environ, **server_env(home, datasource_tools=True)}
        url = f"sqlite:///{DATA / db / 'db.sqlite'}"
        subprocess.run([str(SERVER), "add", db, url], env=env, check=True, capture_output=True)
        prompt = (HERE / "prompts" / "build_ontology.md").read_text().format(
            build_ontology_prompt=render_build_ontology_prompt(db), db=db)
        res = claude(prompt, cwd=ws, tools="Read,Grep,Glob", mcp=mcp_config(home, datasource_tools=True),
                     transcript=base / "transcript.jsonl", budget=BUILD_BUDGET_USD, timeout=BUILD_TIMEOUT_S)
        res["readiness"] = ontology_stats(home, db)
    res = {"condition": condition, "db": db, **res}
    (base / "build.json").write_text(json.dumps(res, indent=1, ensure_ascii=False))
    log(f"build {condition}/{db}: ${res.get('cost_usd')} {res.get('wall_s')}s {res.get('tool_calls')} tool calls")
    return res


def ontology_stats(home: Path, db: str) -> dict:
    con = sqlite3.connect(f"file:{home / 'ontology.db'}?mode=ro", uri=True)
    try:
        ds = con.execute("SELECT id FROM datasource WHERE name = ?", (db,)).fetchone()[0]
        q = lambda sql: con.execute(sql, (ds,)).fetchone()[0]  # noqa: E731
        return {
            "terms": q("SELECT COUNT(*) FROM term WHERE datasource_id = ?"),
            "column_attributes": q("SELECT COUNT(*) FROM column_attribute ca JOIN term t ON t.id = ca.term_id WHERE t.datasource_id = ?"),
            "described_columns": q("SELECT COUNT(*) FROM column_attribute ca JOIN term t ON t.id = ca.term_id "
                                   "WHERE t.datasource_id = ? AND COALESCE(ca.description, '') <> ''"),
            "relationships": dict(con.execute(
                "SELECT status, COUNT(*) FROM relationship WHERE datasource_id = ? GROUP BY status", (ds,)).fetchall()),
            "sql_attributes": q("SELECT COUNT(*) FROM sql_attribute sa JOIN term t ON t.id = sa.term_id WHERE t.datasource_id = ?"),
            "glossary": q("SELECT COUNT(*) FROM glossary WHERE datasource_id = ?"),
        }
    except sqlite3.Error as e:  # schema names differ between versions; stats are informational
        return {"error": str(e)}
    finally:
        con.close()


def cmd_build(args: argparse.Namespace) -> None:
    dbs = args.db or DBS
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = [pool.submit(build_one, args.condition, db) for db in dbs]
        for f in as_completed(futures):
            f.result()


# ----------------------------------------------------------------------------- run
def run_one(name: str, condition: str, q: dict, trial: int) -> None:
    db, qid = q["db_id"], q["question_id"]
    key = f"{qid}-t{trial}"
    base = RUNS / name / condition
    ws = base / key
    if ws.exists():
        shutil.rmtree(ws)
    ws.mkdir(parents=True)
    copy_docs(db, ws)
    home = None
    if condition in ("plain", "wiki", "plain-evidence"):
        link_db(db, ws)
        tools = "Bash,Read,Grep,Glob"
        if condition == "wiki":
            shutil.copytree(BUILDS / "wiki" / db / "workspace" / "wiki", ws / "wiki")
    else:
        home = base / f"{key}.home"
        if home.exists():
            shutil.rmtree(home)
        shutil.copytree(BUILDS / ONTOLOGY_BUILD.get(condition, "ontology") / db / "home", home)
        tools = "Read,Grep,Glob"
    question = q["question"]
    if condition == "plain-evidence" and q.get("evidence"):
        question += f"\n\nExternal knowledge for this question: {q['evidence']}"
    prompt = (HERE / "prompts" / "question.md").read_text().format(
        preamble=PREAMBLE[condition.split("-")[0]].format(db=db), db=db, question=question)
    res = claude(prompt, cwd=ws, tools=tools, mcp=mcp_config(home, toolsets=TOOLSETS.get(condition), server=SERVERS.get(condition, SERVER)), transcript=base / f"{key}.jsonl",
                 budget=QUESTION_BUDGET_USD, timeout=QUESTION_TIMEOUT_S, schema=ANSWER_SCHEMA)
    row = {"condition": condition, "question_id": qid, "db_id": db, "trial": trial, **res}
    append_jsonl(RESULTS / name / "answers.jsonl", row)
    log(f"{condition:8} q{qid:<4} t{trial} ${res.get('cost_usd') or 0:.3f} {res['wall_s']:>6}s "
        f"{res['tool_calls']:>3} calls{' LEAK ' + str(res['leaks']) if res['leaks'] else ''}{' ERROR' if res['is_error'] else ''}")


def cmd_run(args: argparse.Namespace) -> None:
    questions = load_questions(args.set)
    if args.db:
        questions = [q for q in questions if q["db_id"] in args.db]
    if args.limit:
        questions = questions[: args.limit]
    done = {(r["condition"], r["question_id"], r["trial"]) for r in read_jsonl(RESULTS / args.name / "answers.jsonl")
            if not r.get("is_error") or not args.retry_errors}
    if args.retry_errors:
        keep = [r for r in read_jsonl(RESULTS / args.name / "answers.jsonl") if not r.get("is_error")]
        path = RESULTS / args.name / "answers.jsonl"
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in keep))
    jobs = [(c, q, t) for t in range(1, args.trials + 1) for q in questions for c in args.conditions
            if (c, q["question_id"], t) not in done]
    random.Random(SEED).shuffle(jobs)  # interleave conditions so load and rate limits hit them alike
    log(f"{len(jobs)} sessions to run with {args.jobs} in parallel")
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = [pool.submit(run_one, args.name, c, q, t) for c, q, t in jobs]
        for f in as_completed(futures):
            try:
                f.result()
            except Exception as e:  # keep the other sessions going
                log(f"session failed: {e!r}")


# ----------------------------------------------------------------------------- score
def execute(db: str, sql: str) -> tuple[list | None, str | None]:
    if not sql.strip():
        return None, "no SQL"
    con = sqlite3.connect(f"file:{DATA / db / 'db.sqlite'}?mode=ro", uri=True)
    deadline = time.time() + SQL_TIMEOUT_S
    con.set_progress_handler(lambda: 1 if time.time() > deadline else 0, 10_000)
    try:
        return con.execute(sql).fetchall(), None
    except Exception as e:  # noqa: BLE001 - any failure is a wrong answer
        return None, f"{type(e).__name__}: {e}"
    finally:
        con.close()


def cmd_score(args: argparse.Namespace) -> None:
    gold = {r["question_id"]: r for r in read_gold(args.set)}
    gold_rows: dict[int, list] = {}
    scored = []
    for r in read_jsonl(RESULTS / args.name / "answers.jsonl"):
        qid = r["question_id"]
        if qid not in gold_rows:
            rows, err = execute(r["db_id"], gold[qid]["SQL"])
            if err:
                raise SystemExit(f"gold SQL for {qid} failed: {err}")
            gold_rows[qid] = rows
        rows, err = execute(r["db_id"], r.get("sql") or "")
        ex = rows is not None and set(rows) == set(gold_rows[qid])
        scored.append({k: r[k] for k in ("condition", "question_id", "db_id", "trial")}
                      | {"ex": int(ex), "exec_error": err, "pred_rows": len(rows) if rows is not None else None,
                         "gold_rows": len(gold_rows[qid]), "cost_usd": r.get("cost_usd"), "duration_ms": r.get("duration_ms"),
                         "tool_calls": r.get("tool_calls"), "num_turns": r.get("num_turns"), "is_error": r.get("is_error"),
                         "leaks": r.get("leaks"), "rerun": bool(r.get("rerun"))})
    out = RESULTS / args.name / "scores.jsonl"
    out.write_text("".join(json.dumps(s, ensure_ascii=False) + "\n" for s in scored))
    log(f"scored {len(scored)} answers → {out}")


# ----------------------------------------------------------------------------- report
def pct(xs: list[int]) -> str:
    return f"{100 * sum(xs) / len(xs):.1f}% ({sum(xs)}/{len(xs)})" if xs else "-"


def cmd_report(args: argparse.Namespace) -> None:
    scores = read_jsonl(RESULTS / args.name / "scores.jsonl")
    questions = {q["question_id"]: q for q in load_questions(args.set)}
    conds = [c for c in CONDITIONS if any(s["condition"] == c for s in scores)]
    by = collections.defaultdict(list)
    for s in scores:
        by[s["condition"]].append(s)
    models = collections.Counter(a.get("model") for a in read_jsonl(RESULTS / args.name / "answers.jsonl"))
    model = ", ".join(m for m in models if m) or MODEL
    lines = [f"# Results: {args.name}", "", f"Model: {model}. Questions: {len(questions)} from BIRD Mini-Dev (SQLite), "
             f"asked without evidence. Metric: strict execution accuracy (EX).", ""]
    lines += ["| | " + " | ".join(conds) + " |", "| --- | " + " | ".join("---:" for _ in conds) + " |"]
    lines.append("| EX, all | " + " | ".join(pct([s["ex"] for s in by[c]]) for c in conds) + " |")
    for db in DBS:
        lines.append(f"| EX, {db} | " + " | ".join(pct([s["ex"] for s in by[c] if s["db_id"] == db]) for c in conds) + " |")
    for flag, label in ((True, "with evidence in BIRD"), (False, "no evidence in BIRD")):
        lines.append(f"| EX, {label} | " + " | ".join(
            pct([s["ex"] for s in by[c] if questions[s["question_id"]]["has_evidence"] is flag]) for c in conds) + " |")
    # A session re-run in the same workspace path hits the prompt cache of its first attempt and costs about
    # half, so cost and time come from first attempts only.
    for key, label, fmt in (("cost_usd", "cost per question, median", "${:.3f}"),
                            ("duration_ms", "time per question, median (s)", "{:.0f}"),
                            ("tool_calls", "tool calls per question, median", "{:.0f}")):
        vals = []
        for c in conds:
            xs = [s[key] for s in by[c] if s.get(key) is not None and not s.get("rerun")]
            if key == "duration_ms":
                xs = [x / 1000 for x in xs]
            vals.append(fmt.format(statistics.median(xs)) if xs else "-")
        lines.append(f"| {label} | " + " | ".join(vals) + " |")
    lines.append("| cost per question, mean | " + " | ".join(
        f"${statistics.fmean(s['cost_usd'] or 0 for s in by[c] if not s.get('rerun')):.3f}" for c in conds) + " |")
    lines.append("| cost, total | " + " | ".join(f"${sum(s['cost_usd'] or 0 for s in by[c]):.2f}" for c in conds) + " |")
    if any(s.get("rerun") for s in scores):
        lines.append("| sessions re-run (left out of cost and time) | " + " | ".join(
            str(sum(1 for s in by[c] if s.get("rerun"))) for c in conds) + " |")
    lines.append("| sessions that errored | " + " | ".join(str(sum(1 for s in by[c] if s['is_error'])) for c in conds) + " |")
    lines.append("| queries that failed to execute | " + " | ".join(
        str(sum(1 for s in by[c] if s['exec_error'])) for c in conds) + " |")
    lines.append("| sessions that read outside the workspace | " + " | ".join(
        str(sum(1 for s in by[c] if s.get('leaks'))) for c in conds) + " |")

    lines += ["", "## Pairwise (same question and trial)", "", "| A vs B | A right, B wrong | A wrong, B right | both right | both wrong |",
              "| --- | ---: | ---: | ---: | ---: |"]
    idx = {(s["condition"], s["question_id"], s["trial"]): s["ex"] for s in scores}
    for i, a in enumerate(conds):
        for b in conds[i + 1:]:
            keys = [(q, t) for (c, q, t) in idx if c == a and (b, q, t) in idx]
            cnt = collections.Counter((idx[(a, q, t)], idx[(b, q, t)]) for q, t in keys)
            lines.append(f"| {a} vs {b} | {cnt[(1, 0)]} | {cnt[(0, 1)]} | {cnt[(1, 1)]} | {cnt[(0, 0)]} |")

    # Per-question EX averaged over trials, then a paired bootstrap over questions for each difference.
    per_q = {c: {} for c in conds}
    for c in conds:
        acc = collections.defaultdict(list)
        for s in by[c]:
            acc[s["question_id"]].append(s["ex"])
        per_q[c] = {q: sum(v) / len(v) for q, v in acc.items()}
    common = sorted(set.intersection(*(set(per_q[c]) for c in conds))) if conds else []
    rng = random.Random(SEED)
    lines += ["", f"## Differences in EX, paired over {len(common)} questions", "",
              "| difference | points | 95% bootstrap interval |", "| --- | ---: | --- |"]
    for i, a in enumerate(conds):
        for b in conds[i + 1:]:
            diffs = [per_q[b][q] - per_q[a][q] for q in common]
            if not diffs:
                continue
            boots = sorted(statistics.fmean(rng.choices(diffs, k=len(diffs))) for _ in range(10_000))
            lines.append(f"| {b} − {a} | {100 * statistics.fmean(diffs):+.1f} | "
                         f"{100 * boots[249]:+.1f} to {100 * boots[9749]:+.1f} |")

    lines += ["", "## Per question (share of trials with EX = 1)", "", "| question | db | " + " | ".join(conds) + " |",
              "| ---: | --- | " + " | ".join("---:" for _ in conds) + " |"]
    for q in common:
        lines.append(f"| {q} | {questions[q]['db_id']} | " + " | ".join(f"{per_q[c][q]:.1f}" for c in conds) + " |")

    builds =[json.loads(p.read_text()) for p in sorted(BUILDS.glob("*/*/build.json"))]
    if builds:
        lines += ["", "## One-time builds", "", "| condition | db | cost | time (min) | tool calls | result |", "| --- | --- | ---: | ---: | ---: | --- |"]
        for b in builds:
            extra = f"{b.get('pages')} pages, {b.get('wiki_bytes', 0) // 1024} KB" if b["condition"] == "wiki" else json.dumps(b.get("readiness"))
            lines.append(f"| {b['condition']} | {b['db']} | ${b.get('cost_usd') or 0:.2f} | {b['wall_s'] / 60:.1f} | {b.get('tool_calls')} | {extra} |")
    text = "\n".join(lines) + "\n"
    (RESULTS / args.name / "summary.md").write_text(text)
    print(text)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    pr = sub.add_parser("prepare")
    pr.add_argument("--set", choices=list(QUESTION_SETS), default="dev")
    pr.add_argument("--force", action="store_true")
    pr.set_defaults(fn=cmd_prepare)
    b = sub.add_parser("build")
    b.add_argument("--condition", choices=["wiki", "ontology"], required=True)
    b.add_argument("--db", nargs="*")
    b.add_argument("--jobs", type=int, default=4)
    b.set_defaults(fn=cmd_build)
    r = sub.add_parser("run")
    r.add_argument("--name", required=True)
    r.add_argument("--conditions", nargs="*", default=CONDITIONS, choices=CONDITIONS)
    r.add_argument("--db", nargs="*")
    r.add_argument("--trials", type=int, default=1)
    r.add_argument("--jobs", type=int, default=4)
    r.add_argument("--limit", type=int, default=0)
    r.add_argument("--retry-errors", action="store_true")
    r.add_argument("--set", choices=list(QUESTION_SETS), default="dev")
    r.set_defaults(fn=cmd_run)
    for name, fn in (("score", cmd_score), ("report", cmd_report)):
        s = sub.add_parser(name)
        s.add_argument("--name", required=True)
        s.add_argument("--set", choices=list(QUESTION_SETS), default="dev")
        s.set_defaults(fn=fn)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
