"""Report semantic test step time and fastembed cache step time of ci runs from Actions logs.

Usage:
  uv run python scripts/ci_semantic_timing.py --sha <commit> [--event push|pull_request]
      [--require-miss-and-hit] [--require-success]

Every attempt of every ci run for the commit is listed with the "Run semantic tests" step
duration and the "Cache fastembed model" / "Post Cache fastembed model" step durations, plus
separate averages for successful cache misses and hits. Needs an authenticated gh.
Exit 1 when a --require-* condition does not hold.
"""

from __future__ import annotations

import argparse
import json
import logging
import statistics
import subprocess
import sys
from datetime import datetime
from typing import Any

log = logging.getLogger("ci_semantic_timing")

SEMANTIC_STEP = "Run semantic tests"
CACHE_STEP = "Cache fastembed model"
POST_CACHE_STEP = f"Post {CACHE_STEP}"
CACHE_KEY = "fastembed-paraphrase-multilingual-MiniLM-L12-v2"


def gh(*args: str) -> str:
    log.debug("gh %s", " ".join(args))
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def seconds(step: dict[str, Any]) -> float | None:
    if not step.get("started_at") or not step.get("completed_at"):
        return None
    parse = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00"))  # noqa: E731
    return (parse(step["completed_at"]) - parse(step["started_at"])).total_seconds()


def step_seconds(steps: list[dict[str, Any]], *names: str) -> float | None:
    matched = [s for s in steps if s.get("name") in names]
    if not matched:
        return None
    vals = [d for s in matched if (d := seconds(s)) is not None]
    return sum(vals) if vals else None


def cache_status(log_text: str) -> str:
    for line in log_text.splitlines():
        if CACHE_KEY not in line:
            continue
        if "Cache hit for" in line or "Cache restored from key" in line:
            return "hit"
        if "Cache not found" in line:
            return "miss"
    return "unknown"


def attempts(sha: str, event: str | None) -> list[dict[str, Any]]:
    repo = gh("repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner").strip()
    query = f"repos/{repo}/actions/workflows/ci.yml/runs?head_sha={sha}&per_page=50"
    runs = json.loads(gh("api", query))["workflow_runs"]
    rows: list[dict[str, Any]] = []
    for run in runs:
        if event and run["event"] != event:
            continue
        for n in range(1, run["run_attempt"] + 1):
            jobs = json.loads(gh("api", f"repos/{repo}/actions/runs/{run['id']}/attempts/{n}/jobs"))
            steps = [s for j in jobs["jobs"] for s in j["steps"]]
            step = next((s for s in steps if s["name"] == SEMANTIC_STEP), None)
            has_cache = any(s.get("name") in (CACHE_STEP, POST_CACHE_STEP) for s in steps)
            text = gh("run", "view", str(run["id"]), "--attempt", str(n), "--log") if has_cache else ""
            rows.append(
                {
                    "run": run["id"],
                    "attempt": n,
                    "event": run["event"],
                    "conclusion": jobs["jobs"][0]["conclusion"] if jobs["jobs"] else None,
                    "semantic_step": step and step["conclusion"],
                    "semantic_seconds": step and seconds(step),
                    "cache": cache_status(text) if has_cache else "none",
                    "cache_seconds": step_seconds(steps, CACHE_STEP, POST_CACHE_STEP),
                    "cache_restore_seconds": step_seconds(steps, CACHE_STEP),
                    "cache_save_seconds": step_seconds(steps, POST_CACHE_STEP),
                }
            )
    rows.sort(key=lambda r: (r["run"], r["attempt"]))
    return rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Aggregate semantic and cache step durations separately for successful cache misses and hits."""
    ok = [
        r
        for r in rows
        if r.get("conclusion") == "success"
        and r.get("semantic_step") == "success"
        and r.get("semantic_seconds") is not None
    ]
    out: dict[str, dict[str, Any]] = {}
    for kind in ("miss", "hit"):
        group = [r for r in ok if r.get("cache") == kind]
        if not group:
            continue
        sem = round(statistics.fmean(float(r["semantic_seconds"]) for r in group), 2)
        cache_sec = round(statistics.fmean(float(r.get("cache_seconds") or 0.0) for r in group), 2)
        restore = round(statistics.fmean(float(r.get("cache_restore_seconds") or 0.0) for r in group), 2)
        save = round(statistics.fmean(float(r.get("cache_save_seconds") or 0.0) for r in group), 2)
        out[kind] = {
            "count": len(group),
            "semantic_seconds": sem,
            "cache_seconds": cache_sec,
            "restore_seconds": restore,
            "save_seconds": save,
            "total_seconds": round(sem + cache_sec, 2),
        }
    if "miss" in out and "hit" in out:
        sem_saved = round(out["miss"]["semantic_seconds"] - out["hit"]["semantic_seconds"], 2)
        hit_cache = out["hit"]["cache_seconds"]
        net_saved = round(out["miss"]["semantic_seconds"] - out["hit"]["total_seconds"], 2)
        out["delta"] = {
            "semantic_saved_seconds": sem_saved,
            "hit_cache_seconds": hit_cache,
            "net_saved_seconds": net_saved,
        }
    return out


def format_report(rows: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for r in rows:
        if r.get("cache_seconds") is not None:
            cache_part = (
                f"cache step {r['cache_seconds']}s "
                f"(restore {r.get('cache_restore_seconds') or 0.0}s, save {r.get('cache_save_seconds') or 0.0}s)"
            )
        else:
            cache_part = "cache step 0.0s"
        lines.append(
            f"run {r['run']} attempt {r['attempt']} ({r['event']}): {r['conclusion']}, "
            f"semantic step {r['semantic_step']} in {r['semantic_seconds']}s, "
            f"fastembed cache {r['cache']}, {cache_part}"
        )
    summary = summarize(rows)
    for kind in ("miss", "hit"):
        if kind in summary:
            s = summary[kind]
            lines.append(
                f"successful cache {kind} (n={s['count']}): semantic {s['semantic_seconds']}s, "
                f"cache step {s['cache_seconds']}s "
                f"(restore {s['restore_seconds']}s, save {s['save_seconds']}s, total {s['total_seconds']}s)"
            )
    if "delta" in summary:
        d = summary["delta"]
        lines.append(
            f"cache net vs uncached semantic: {d['net_saved_seconds']:+.1f}s "
            f"(semantic saved {d['semantic_saved_seconds']:+.1f}s, hit cache step {d['hit_cache_seconds']:.1f}s)"
        )
    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sha", required=True)
    ap.add_argument("--event")
    ap.add_argument("--require-miss-and-hit", action="store_true")
    ap.add_argument("--require-success", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(message)s")

    rows = attempts(a.sha, a.event)
    for line in format_report(rows):
        log.info("%s", line)
    ok = [r for r in rows if r["conclusion"] == "success" and r["semantic_step"] == "success"]
    if not rows:
        log.error("no ci runs for %s", a.sha)
        return 1
    if a.require_success and not ok:
        log.error("no successful ci run with a passing semantic step")
        return 1
    if a.require_miss_and_hit:
        miss = [r for r in ok if r["cache"] == "miss"]
        hit = [r for r in ok if r["cache"] == "hit"]
        if not (
            miss
            and hit
            and min(h["run"] * 100 + h["attempt"] for h in hit) > min(m["run"] * 100 + m["attempt"] for m in miss)
        ):
            log.error("need a successful cache miss followed by a successful cache hit")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
