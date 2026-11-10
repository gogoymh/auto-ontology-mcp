#!/usr/bin/env python3
"""Put conditions from different runs side by side (EX, cost, tokens), paired over the same questions.

  python3 compare.py main:plain main:wiki main:ontology=ontology-v1 v2-dev:ontology v2-dev:ontology-answer
"""

import json
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import bench


def load(spec: str):
    run, _, rest = spec.partition(":")
    cond, _, label = rest.partition("=")
    scores = [s for s in bench.read_jsonl(bench.RESULTS / run / "scores.jsonl") if s["condition"] == cond]
    tokens = {}
    for p in (bench.RUNS / run / cond).glob("*.jsonl"):
        for line in p.read_text().splitlines():
            ev = json.loads(line)
            if ev.get("type") == "result":
                u = ev.get("usage") or {}
                tokens[p.stem] = sum(u.get(k, 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    for s in scores:
        s["input_tokens"] = tokens.get(f"{s['question_id']}-t{s['trial']}")
    return label or f"{run}:{cond}", scores


cols = [load(a) for a in sys.argv[1:]]
per_q = {}
for label, scores in cols:
    acc = defaultdict(list)
    for s in scores:
        acc[s["question_id"]].append(s["ex"])
    per_q[label] = {q: sum(v) / len(v) for q, v in acc.items()}
common = sorted(set.intersection(*(set(v) for v in per_q.values())))
print(f"{len(common)} questions in common\n")
print("| | " + " | ".join(label for label, _ in cols) + " |")
print("| --- | " + " | ".join("---:" for _ in cols) + " |")
rows = {
    "EX": lambda sc: f"{100 * statistics.fmean(s['ex'] for s in sc):.1f}% ({sum(s['ex'] for s in sc)}/{len(sc)})",
    "cost per question, mean": lambda sc: f"${statistics.fmean(s['cost_usd'] or 0 for s in sc):.3f}",
    "cost per question, median": lambda sc: f"${statistics.median(s['cost_usd'] or 0 for s in sc):.3f}",
    "input tokens, median": lambda sc: f"{statistics.median(s['input_tokens'] or 0 for s in sc) / 1000:.0f}K",
    "time per question, median (s)": lambda sc: f"{statistics.median((s['duration_ms'] or 0) / 1000 for s in sc):.0f}",
}
for name, fn in rows.items():
    # EX over every answer; cost, tokens and time over first attempts (a re-run in the same path hits the prompt cache).
    pick = (lambda s: True) if name == "EX" else (lambda s: not s.get("rerun"))
    print(f"| {name} | " + " | ".join(fn([s for s in sc if s["question_id"] in common and pick(s)]) for _, sc in cols) + " |")

base = cols[0][0]
rng = random.Random(bench.SEED)
print(f"\n| difference from {base} | points | 95% bootstrap interval |\n| --- | ---: | --- |")
for label, _ in cols[1:]:
    diffs = [per_q[label][q] - per_q[base][q] for q in common]
    boots = sorted(statistics.fmean(rng.choices(diffs, k=len(diffs))) for _ in range(10_000))
    print(f"| {label} | {100 * statistics.fmean(diffs):+.1f} | {100 * boots[249]:+.1f} to {100 * boots[9749]:+.1f} |")
