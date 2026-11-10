#!/usr/bin/env python3
"""The pre-registered analysis of PREREGISTRATION-curated.md. Writes results/curated-summary.md."""

import random
import statistics
from collections import defaultdict

import bench

SEED, RESAMPLES, MARGIN = 20261111, 20_000, 3.0


def scores(run):
    return bench.read_jsonl(bench.RESULTS / run / "scores.jsonl")


def per_question(rows, cond):
    acc = defaultdict(list)
    for s in rows:
        if s["condition"] == cond and not s.get("leaks"):
            acc[s["question_id"]].append(s["ex"])
    return {q: statistics.fmean(v) for q, v in acc.items()}


def interval(diffs, level):
    rng = random.Random(SEED)
    boots = sorted(statistics.fmean(rng.choices(diffs, k=len(diffs))) for _ in range(RESAMPLES))
    return 100 * boots[int(RESAMPLES * (1 - level) / 2)], 100 * boots[int(RESAMPLES * (1 + level) / 2) - 1]


def compare(label, pa, pb):
    common = sorted(set(pa) & set(pb))
    d = [pb[q] - pa[q] for q in common]
    lo95, hi95 = interval(d, 0.95)
    lo90, hi90 = interval(d, 0.90)
    verdict = ("better" if lo95 > 0 else "worse" if hi95 < 0
               else f"equivalent within {MARGIN:g} points" if -MARGIN <= lo90 and hi90 <= MARGIN else "inconclusive")
    return (f"| {label} | {len(common)} | {100 * statistics.fmean(pa[q] for q in common):.1f}% | "
            f"{100 * statistics.fmean(pb[q] for q in common):.1f}% | {100 * statistics.fmean(d):+.1f} | {lo95:+.1f} to {hi95:+.1f} | "
            f"{lo90:+.1f} to {hi90:+.1f} | {sum(x > 0 for x in d)} / {sum(x < 0 for x in d)} | {verdict} |")


def cost(rows, cond):
    xs = [s["cost_usd"] for s in rows if s["condition"] == cond and not s.get("rerun") and s.get("cost_usd") is not None]
    return f"${statistics.fmean(xs):.3f}" if xs else "-"


head = ["| Comparison (B − A) | questions | A | B | B − A (points) | 95% interval | 90% interval | questions B better / worse | verdict |",
        "| --- | ---: | ---: | ---: | ---: | --- | --- | ---: | --- |"]
out = ["# Accumulated knowledge: pre-registered results", ""] + head
runs = {"Sonnet": ("curated-sonnet", "final-sonnet"), "Haiku": ("curated-haiku", "final-haiku")}
data = {}
for model, (run, _) in runs.items():
    rows = scores(run) if (bench.RESULTS / run / "scores.jsonl").exists() else []
    data[model] = rows
    if not rows:
        continue
    p, w, o = (per_question(rows, c) for c in ("plain-notes", "wiki-curated", "ontology-curated"))
    out.append(compare(("**Primary**: " if model == "Sonnet" else "") + f"{model}, ontology-curated − plain-notes", p, o))
    out.append(compare(f"{model}, ontology-curated − wiki-curated", w, o))
    out.append(compare(f"{model}, wiki-curated − plain-notes", p, w))
out += ["", "Descriptive: against plain access without accumulated knowledge (fourth round, shared questions)", ""] + head
for model, (run, old) in runs.items():
    if data[model] and (bench.RESULTS / old / "scores.jsonl").exists():
        base = per_question(scores(old), "plain")
        for c in ("plain-notes", "wiki-curated", "ontology-curated"):
            out.append(compare(f"{model}, {c} − plain (no knowledge)", base, per_question(data[model], c)))
out += ["", "| Cost per question, mean of first attempts | plain-notes | wiki-curated | ontology-curated |", "| --- | ---: | ---: | ---: |"]
for model, rows in data.items():
    if rows:
        out.append(f"| {model} | " + " | ".join(cost(rows, c) for c in ("plain-notes", "wiki-curated", "ontology-curated")) + " |")
out += ["", "| Sessions | total | errored | re-run | read outside the workspace |", "| --- | ---: | ---: | ---: | ---: |"]
for model, rows in data.items():
    if rows:
        out.append(f"| {model} | {len(rows)} | {sum(1 for s in rows if s.get('is_error'))} | {sum(1 for s in rows if s.get('rerun'))} | "
                   f"{sum(1 for s in rows if s.get('leaks'))} |")
text = "\n".join(out) + "\n"
(bench.RESULTS / "curated-summary.md").write_text(text)
print(text)
