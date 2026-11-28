"""Report the semantic step time and fastembed cache status of ci runs from Actions logs.

Usage:
  uv run python scripts/ci_semantic_timing.py --sha <commit> [--event push|pull_request]
      [--require-miss-and-hit] [--require-success]

Every attempt of every ci run for the commit is listed with the "Run semantic tests" step
duration and whether "Cache fastembed model" restored the model. Needs an authenticated gh.
Exit 1 when a --require-* condition does not hold.
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from datetime import datetime

log = logging.getLogger("ci_semantic_timing")

SEMANTIC_STEP = "Run semantic tests"
CACHE_KEY = "fastembed-paraphrase-multilingual-MiniLM-L12-v2"


def gh(*args: str) -> str:
    log.debug("gh %s", " ".join(args))
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def seconds(step: dict) -> float | None:
    if not step.get("started_at") or not step.get("completed_at"):
        return None
    parse = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00"))  # noqa: E731
    return (parse(step["completed_at"]) - parse(step["started_at"])).total_seconds()


def cache_status(log_text: str) -> str:
    for line in log_text.splitlines():
        if CACHE_KEY not in line:
            continue
        if "Cache hit for" in line or "Cache restored from key" in line:
            return "hit"
        if "Cache not found" in line:
            return "miss"
    return "unknown"


def attempts(sha: str, event: str | None) -> list[dict]:
    repo = gh("repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner").strip()
    query = f"repos/{repo}/actions/workflows/ci.yml/runs?head_sha={sha}&per_page=50"
    runs = json.loads(gh("api", query))["workflow_runs"]
    rows = []
    for run in runs:
        if event and run["event"] != event:
            continue
        for n in range(1, run["run_attempt"] + 1):
            jobs = json.loads(gh("api", f"repos/{repo}/actions/runs/{run['id']}/attempts/{n}/jobs"))
            steps = [s for j in jobs["jobs"] for s in j["steps"]]
            step = next((s for s in steps if s["name"] == SEMANTIC_STEP), None)
            text = gh("run", "view", str(run["id"]), "--attempt", str(n), "--log")
            rows.append(
                {
                    "run": run["id"],
                    "attempt": n,
                    "event": run["event"],
                    "conclusion": jobs["jobs"][0]["conclusion"] if jobs["jobs"] else None,
                    "semantic_step": step and step["conclusion"],
                    "semantic_seconds": step and seconds(step),
                    "cache": cache_status(text),
                }
            )
    rows.sort(key=lambda r: (r["run"], r["attempt"]))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sha", required=True)
    ap.add_argument("--event")
    ap.add_argument("--require-miss-and-hit", action="store_true")
    ap.add_argument("--require-success", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(message)s")

    rows = attempts(a.sha, a.event)
    for r in rows:
        log.info(
            "run %s attempt %s (%s): %s, semantic step %s in %ss, fastembed cache %s",
            r["run"], r["attempt"], r["event"], r["conclusion"],
            r["semantic_step"], r["semantic_seconds"], r["cache"],
        )
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
        if not (miss and hit and min(h["run"] * 100 + h["attempt"] for h in hit)
                > min(m["run"] * 100 + m["attempt"] for m in miss)):
            log.error("need a successful cache miss followed by a successful cache hit")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
