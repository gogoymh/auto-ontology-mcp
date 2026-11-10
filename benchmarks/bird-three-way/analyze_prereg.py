#!/usr/bin/env python3
"""The pre-registered analysis (PREREGISTRATION.md). Run after `bench.py score` for both runs.

  python3 analyze_prereg.py            # writes results/final-summary.md
"""

import json
import random
import statistics
from collections import defaultdict

import bench

SEED = 20261110
RESAMPLES = 20_000
MARGIN = 3.0  # points, for equivalence
QUESTIONS = {q["question_id"]: q for q in bench.load_questions("full2026")}


def scores(run: str) -> list[dict]:
    path = bench.RESULTS / run / "scores.jsonl"
    return bench.read_jsonl(path) if path.exists() else []


def per_question(rows: list[dict], cond: str) -> dict[int, float]:
    acc = defaultdict(list)
    for s in rows:
        if s["condition"] == cond and not s.get("leaks"):
            acc[s["question_id"]].append(s["ex"])
    return {q: statistics.fmean(v) for q, v in acc.items()}


def interval(diffs: list[float], level: float) -> tuple[float, float]:
    rng = random.Random(SEED)
    boots = sorted(statistics.fmean(rng.choices(diffs, k=len(diffs))) for _ in range(RESAMPLES))
    lo = boots[int(RESAMPLES * (1 - level) / 2)]
    hi = boots[int(RESAMPLES * (1 + level) / 2) - 1]
    return 100 * lo, 100 * hi


def compare(rows_a, a, rows_b, b) -> dict:
    pa, pb = per_question(rows_a, a), per_question(rows_b, b)
    common = sorted(set(pa) & set(pb) & set(QUESTIONS))
    diffs = [pb[q] - pa[q] for q in common]
    lo95, hi95 = interval(diffs, 0.95)
    lo90, hi90 = interval(diffs, 0.90)
    if lo95 > 0:
        verdict = "better"
    elif hi95 < 0:
        verdict = "worse"
    elif -MARGIN <= lo90 and hi90 <= MARGIN:
        verdict = f"equivalent within {MARGIN:g} points"
    else:
        verdict = "inconclusive"
    return {
        "n": len(common), "a": 100 * statistics.fmean(pa[q] for q in common), "b": 100 * statistics.fmean(pb[q] for q in common),
        "diff": 100 * statistics.fmean(diffs), "ci95": (lo95, hi95), "ci90": (lo90, hi90), "verdict": verdict,
        "b_better": sum(d > 0 for d in diffs), "b_worse": sum(d < 0 for d in diffs),
    }


def cost(rows: list[dict], cond: str) -> float | None:
    xs = [s["cost_usd"] for s in rows if s["condition"] == cond and not s.get("rerun") and s.get("cost_usd") is not None]
    return statistics.fmean(xs) if xs else None


def line(label: str, r: dict) -> str:
    return (f"| {label} | {r['n']} | {r['a']:.1f}% | {r['b']:.1f}% | {r['diff']:+.1f} | {r['ci95'][0]:+.1f} to {r['ci95'][1]:+.1f} | "
            f"{r['ci90'][0]:+.1f} to {r['ci90'][1]:+.1f} | {r['b_better']} / {r['b_worse']} | {r['verdict']} |")


def main() -> None:
    sonnet, haiku = scores("final-sonnet"), scores("final-haiku")
    out = ["# Pre-registered comparison: results", "",
           "| Comparison (B − A) | questions | A | B | B − A (points) | 95% interval | 90% interval | questions B better / worse | verdict |",
           "| --- | ---: | ---: | ---: | ---: | --- | --- | ---: | --- |"]
    out.append(line("**Primary**: Sonnet, ontology − plain", compare(sonnet, "plain", sonnet, "ontology-answer")))
    out.append(line("Sonnet, wiki − plain", compare(sonnet, "plain", sonnet, "wiki")))
    out.append(line("Sonnet, ontology − wiki", compare(sonnet, "wiki", sonnet, "ontology-answer")))
    if haiku:
        out.append(line("Haiku, ontology − plain", compare(haiku, "plain", haiku, "ontology-answer")))
    out += ["", "| Cost per question, mean of first attempts | plain | wiki | ontology |", "| --- | ---: | ---: | ---: |"]
    for label, rows in (("Sonnet", sonnet), ("Haiku", haiku)):
        if rows:
            vals = [cost(rows, c) for c in ("plain", "wiki", "ontology-answer")]
            out.append(f"| {label} | " + " | ".join(f"${v:.3f}" if v is not None else "-" for v in vals) + " |")
    out += ["", "| Sessions | errored | re-run | read outside the workspace |", "| --- | ---: | ---: | ---: |"]
    for label, rows in (("Sonnet", sonnet), ("Haiku", haiku)):
        if rows:
            out.append(f"| {label} | {sum(1 for s in rows if s.get('is_error'))} | {sum(1 for s in rows if s.get('rerun'))} | "
                       f"{sum(1 for s in rows if s.get('leaks'))} |")
    out += ["", "| Database | questions | Sonnet plain | Sonnet ontology | Sonnet wiki | Haiku plain | Haiku ontology |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    by_db = defaultdict(list)
    for q in QUESTIONS.values():
        by_db[q["db_id"]].append(q["question_id"])
    cols = [(sonnet, "plain"), (sonnet, "ontology-answer"), (sonnet, "wiki"), (haiku, "plain"), (haiku, "ontology-answer")]
    pq = [per_question(rows, c) for rows, c in cols]
    for db in sorted(by_db):
        cells = []
        for p in pq:
            xs = [p[q] for q in by_db[db] if q in p]
            cells.append(f"{100 * statistics.fmean(xs):.1f}%" if xs else "-")
        out.append(f"| {db} | {len(by_db[db])} | " + " | ".join(cells) + " |")
    text = "\n".join(out) + "\n"
    (bench.RESULTS / "final-summary.md").write_text(text)
    print(text)


if __name__ == "__main__":
    main()
