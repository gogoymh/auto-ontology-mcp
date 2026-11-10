#!/usr/bin/env python3
"""Show one question's gold SQL and result next to every condition's SQL, result and EX.

  python3 inspect_question.py --name main 219 204
"""

import argparse
import json

import bench

p = argparse.ArgumentParser()
p.add_argument("--name", default="main")
p.add_argument("--evidence", action="store_true", help="also print BIRD's evidence hint (never shown to the models)")
p.add_argument("qids", nargs="+", type=int)
args = p.parse_args()

gold = {r["question_id"]: r for r in bench.read_jsonl(bench.GOLD_FILE)}
answers = bench.read_jsonl(bench.RESULTS / args.name / "answers.jsonl")
scores = {(s["condition"], s["question_id"], s["trial"]): s for s in bench.read_jsonl(bench.RESULTS / args.name / "scores.jsonl")}


def show(rows):
    if rows is None:
        return "ERROR"
    head = rows[:4]
    return f"{len(rows)} rows {head}{' …' if len(rows) > 4 else ''}"


for qid in args.qids:
    g = gold[qid]
    print("=" * 100)
    print(f"q{qid} [{g['db_id']}] {g['question']}")
    if args.evidence:
        print(f"EVIDENCE: {g['evidence']}")
    print(f"GOLD: {g['SQL']}")
    print(f"  → {show(bench.execute(g['db_id'], g['SQL'])[0])}")
    for r in sorted((a for a in answers if a["question_id"] == qid), key=lambda a: (bench.CONDITIONS.index(a["condition"]), a["trial"])):
        s = scores.get((r["condition"], qid, r["trial"]), {})
        rows, err = bench.execute(r["db_id"], r["sql"])
        print(f"-- {r['condition']} t{r['trial']} EX={s.get('ex')} tools={r['tools']}")
        print(f"   {r['sql']}")
        print(f"   → {show(rows) if not err else err}")
