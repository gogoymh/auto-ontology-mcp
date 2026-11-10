#!/usr/bin/env python3
"""Accumulated knowledge: do the layers help once they hold verified queries and definitions?

A semantic layer is meant to be curated: confirmed question/SQL pairs and the definitions behind
them pile up as people use it. BIRD supplies such knowledge for every question (its evidence note
and gold SQL). This script splits the 500 Mini-Dev questions (2026 annotations) of each database
into two folds. To answer fold A, each setup gets the questions, notes and verified SQL of fold B
as `docs/verified_queries.md`, and the other way round, so every question is tested once and never
sees its own note or SQL.

  plain-notes       database + docs/ (with verified_queries.md)
  wiki-curated      plus the LLM Wiki after the model ingested verified_queries.md as a new source
  ontology-curated  the MCP server (answer tools) after the pairs were saved with save_example_query
                    and the model carried the notes into glossary entries, SQL attributes and
                    descriptions; the prompt names the server's answering steps

  python3 curated.py prepare
  python3 curated.py build [--jobs 6]
  python3 curated.py run --name curated-sonnet [--conditions ...] [--trials 1] [--jobs 10]
  then: python3 bench.py score --name curated-sonnet --set cv2026
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

import bench

SEED = 20261111
CONDS = ["plain-notes", "wiki-curated", "ontology-curated", "ontology-addon"]
NOTES = "verified_queries.md"
NOTES_LINE = (" `docs/verified_queries.md` lists earlier questions about this database, the note that came with each, "
              "and the SQL that was confirmed correct.")
WORKFLOW = (" Follow the server's answering steps: check_answerable, get_query_context (pass the question, so it returns "
            "similar verified queries), lookup_values for literals, validate_sql (pass the question), then run_sql.")

WIKI_PROMPT = """A new source has arrived for this wiki: `docs/verified_queries.md`. It lists questions people asked about the \
`{db}` database, the domain note that came with each, and the SQL that was confirmed correct.

Ingest it into `wiki/` the way `LLM_WIKI.md` and `wiki/SCHEMA.md` describe: add what it teaches (definitions of business \
terms, formulas, code meanings, join and filter conventions, reusable query patterns) to the pages it concerns, add pages \
where needed, and update `wiki/index.md` and `wiki/log.md`. People will keep asking this wiki data questions that are \
answered with SQL. Work on your own. Do not modify `db.sqlite` or `docs/`. Reply with a short summary of what you changed."""

# ontology-addon: everything plain-notes has (sqlite3 through Bash, docs/) plus the MCP server, as the server is used
# from a coding agent. It measures what the server adds, rather than what it replaces.
ADDON = (" The auto-ontology MCP tools give a semantic layer over the same database, as the datasource `{db}`: definitions "
         "of its tables and columns, a glossary, and verified queries similar to a question (get_query_context with the "
         "question). validate_sql and run_sql (pass the question to both) check a query for wrong literals, value variants, "
         "scale and fan-out. Use them together with sqlite3.")

ONTOLOGY_PROMPT = """New knowledge has arrived for the datasource `{db}`: `docs/verified_queries.md` in the current folder. \
It lists questions people asked about the database, the domain note that came with each, and the SQL that was confirmed \
correct. The question and SQL pairs are already saved as verified example queries.

Read the file and carry what it teaches into the ontology with the auto-ontology tools: glossary entries for business \
terms and their definitions (save_glossary), SQL attributes for reusable filters, measures and definitions \
(save_sql_attribute), and synonyms or corrected Term and column descriptions where the notes show the current ones are \
missing or wrong (update_term, save_table_terms). Keep what is already there, and do not certify anything. People will \
keep asking data questions about this database that are answered with SQL. Work on your own. Reply with a short summary \
of what you changed."""


def questions() -> list[dict]:
    return bench.load_questions("cv2026")


def notes_text(db: str, fold: int) -> str:
    """The verified queries of *fold* for *db*: what the setups answering the other fold may use."""
    gold = {r["question_id"]: r for r in bench.read_gold("cv2026")}
    out = [f"# Verified queries for `{db}`", "",
           "Questions asked about this database, the note that came with each, and the SQL confirmed correct.", ""]
    for i, q in enumerate((q for q in questions() if q["db_id"] == db and q["fold"] == fold), 1):
        g = gold[q["question_id"]]
        out += [f"## {i}. {g['question'].strip()}", ""]
        if g["evidence"]:
            out += [f"Note: {g['evidence'].strip()}", ""]
        out += ["```sql", g["SQL"].strip(), "```", ""]
    return "\n".join(out)


def cmd_prepare(_: argparse.Namespace) -> None:
    rows = bench.read_gold("cv2026")
    rng = random.Random(SEED)
    picked = []
    for db in bench.ALL_DBS:
        pool = sorted((r for r in rows if r["db_id"] == db), key=lambda r: r["question_id"])
        order = pool[:]
        rng.shuffle(order)
        fold = {r["question_id"]: i % 2 for i, r in enumerate(order)}
        picked += [{"question_id": r["question_id"], "db_id": db, "difficulty": r["difficulty"],
                    "has_evidence": bool(r["evidence"]), "fold": fold[r["question_id"]]} for r in pool]
    out = bench.QUESTION_SETS["cv2026"]
    if out.exists():
        bench.log(f"{out} exists; keeping it")
        return
    out.write_text(json.dumps(picked, indent=1) + "\n")
    bench.log(f"{len(picked)} questions in two folds written to {out}")


def save_examples(home, db: str, fold: int) -> dict:
    """save_example_query for every verified pair, as the server does when a user confirms an answer."""
    gold = {r["question_id"]: r for r in bench.read_gold("cv2026")}
    pairs = [[gold[q["question_id"]]["question"], gold[q["question_id"]]["SQL"], gold[q["question_id"]]["evidence"] or None]
             for q in questions() if q["db_id"] == db and q["fold"] == fold]
    code = (
        "import json, sys\n"
        "from auto_ontology_mcp.config import Settings\n"
        "from auto_ontology_mcp.service import OntologyService\n"
        "svc = OntologyService(Settings.from_env())\n"
        "ok, bad = 0, []\n"
        "for q, sql, note in json.load(sys.stdin):\n"
        "    try:\n"
        "        svc.save_example_query(q, sql, note=note, datasource=sys.argv[1]); ok += 1\n"
        "    except Exception as e:\n"
        "        bad.append(str(e)[:160])\n"
        "print(json.dumps({'saved': ok, 'refused': bad}))\n"
    )
    env = {**os.environ, **bench.server_env(home, datasource_tools=False)}
    out = subprocess.run([str(bench.SERVER.parent / "python"), "-c", code, db], input=json.dumps(pairs), env=env,
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


def build_one(kind: str, db: str, fold: int) -> dict:
    """Curate the base wiki or ontology of *db* with the verified queries of *fold*."""
    base = bench.BUILDS / f"{kind}-k{fold}" / db
    if (base / "build.json").exists():
        return json.loads((base / "build.json").read_text())
    if base.exists():
        shutil.rmtree(base)
    ws = base / "workspace"
    if kind == "wiki":
        shutil.copytree(bench.BUILDS / "wiki" / db / "workspace", ws)
        (ws / "docs" / NOTES).write_text(notes_text(db, fold))
        res = bench.claude(WIKI_PROMPT.format(db=db), cwd=ws, tools="Bash,Read,Write,Edit,Grep,Glob", mcp=bench.mcp_config(None),
                           transcript=base / "transcript.jsonl", budget=bench.BUILD_BUDGET_USD, timeout=bench.BUILD_TIMEOUT_S)
        res["wiki_bytes"] = sum(p.stat().st_size for p in (ws / "wiki").rglob("*") if p.is_file())
    else:
        ws.mkdir(parents=True)
        bench.copy_docs(db, ws)
        (ws / "docs" / NOTES).write_text(notes_text(db, fold))
        home = base / "home"
        shutil.copytree(bench.BUILDS / "ontology" / db / "home", home)
        res = {"examples": save_examples(home, db, fold)}
        res |= bench.claude(ONTOLOGY_PROMPT.format(db=db), cwd=ws, tools="Read,Grep,Glob", mcp=bench.mcp_config(home),
                            transcript=base / "transcript.jsonl", budget=bench.BUILD_BUDGET_USD, timeout=bench.BUILD_TIMEOUT_S)
        res["readiness"] = bench.ontology_stats(home, db)
    res = {"kind": kind, "db": db, "fold": fold, **res}
    (base / "build.json").write_text(json.dumps(res, indent=1, ensure_ascii=False))
    bench.log(f"curated {kind} k{fold} {db}: ${res.get('cost_usd') or 0:.2f} {res.get('wall_s')}s {res.get('examples', '')}")
    return res


def cmd_build(args: argparse.Namespace) -> None:
    jobs = [(k, db, f) for k in ("ontology", "wiki") for db in (args.db or bench.ALL_DBS) for f in (0, 1)]
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for f in as_completed([pool.submit(build_one, *j) for j in jobs]):
            f.result()


def run_one(name: str, condition: str, q: dict, trial: int) -> None:
    db, qid, know = q["db_id"], q["question_id"], 1 - q["fold"]  # knowledge comes from the other fold
    key = f"{qid}-t{trial}"
    base = bench.RUNS / name / condition
    ws = base / key
    if ws.exists():
        shutil.rmtree(ws)
    ws.mkdir(parents=True)
    bench.copy_docs(db, ws)
    (ws / "docs" / NOTES).write_text(notes_text(db, know))
    home = None
    if condition in ("ontology-curated", "ontology-addon"):
        home = base / f"{key}.home"
        if home.exists():
            shutil.rmtree(home)
        shutil.copytree(bench.BUILDS / f"ontology-k{know}" / db / "home", home)
        tools, preamble = "Read,Grep,Glob", bench.PREAMBLE["ontology"].format(db=db) + NOTES_LINE + WORKFLOW
        if condition == "ontology-addon":
            bench.link_db(db, ws)
            tools, preamble = "Bash,Read,Grep,Glob", bench.PREAMBLE["plain"] + NOTES_LINE + ADDON.format(db=db)
    else:
        bench.link_db(db, ws)
        tools, preamble = "Bash,Read,Grep,Glob", bench.PREAMBLE["plain"] + NOTES_LINE
        if condition == "wiki-curated":
            shutil.copytree(bench.BUILDS / f"wiki-k{know}" / db / "workspace" / "wiki", ws / "wiki")
            preamble = bench.PREAMBLE["wiki"] + NOTES_LINE
    prompt = (bench.HERE / "prompts" / "question.md").read_text().format(preamble=preamble, db=db, question=q["question"])
    res = bench.claude(prompt, cwd=ws, tools=tools, mcp=bench.mcp_config(home, toolsets="answer"), transcript=base / f"{key}.jsonl",
                       budget=bench.QUESTION_BUDGET_USD, timeout=bench.QUESTION_TIMEOUT_S, schema=bench.ANSWER_SCHEMA)
    bench.append_jsonl(bench.RESULTS / name / "answers.jsonl", {"condition": condition, "question_id": qid, "db_id": db, "trial": trial, **res})
    bench.log(f"{condition:16} q{qid:<4} t{trial} ${res.get('cost_usd') or 0:.3f} {res['wall_s']:>6}s {res['tool_calls']:>3} calls"
              f"{' LEAK ' + str(res['leaks']) if res['leaks'] else ''}{' ERROR' if res['is_error'] else ''}")


def cmd_run(args: argparse.Namespace) -> None:
    qs = [q for q in questions() if (not args.db or q["db_id"] in args.db) and (args.fold is None or q["fold"] == args.fold)]
    if args.limit:
        qs = qs[: args.limit]
    done = {(r["condition"], r["question_id"], r["trial"]) for r in bench.read_jsonl(bench.RESULTS / args.name / "answers.jsonl")}
    jobs = [(c, q, t) for t in range(1, args.trials + 1) for q in qs for c in args.conditions if (c, q["question_id"], t) not in done]
    random.Random(SEED).shuffle(jobs)
    bench.log(f"{len(jobs)} sessions to run with {args.jobs} in parallel")
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for f in as_completed([pool.submit(run_one, args.name, c, q, t) for c, q, t in jobs]):
            try:
                f.result()
            except Exception as e:  # keep the other sessions going
                bench.log(f"session failed: {e!r}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("prepare").set_defaults(fn=cmd_prepare)
    b = sub.add_parser("build")
    b.add_argument("--db", nargs="*")
    b.add_argument("--jobs", type=int, default=6)
    b.set_defaults(fn=cmd_build)
    r = sub.add_parser("run")
    r.add_argument("--name", required=True)
    r.add_argument("--conditions", nargs="*", default=CONDS, choices=CONDS)
    r.add_argument("--db", nargs="*")
    r.add_argument("--trials", type=int, default=1)
    r.add_argument("--jobs", type=int, default=10)
    r.add_argument("--limit", type=int, default=0)
    r.add_argument("--fold", type=int, choices=[0, 1], help="only the questions of this fold")
    r.set_defaults(fn=cmd_run)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
