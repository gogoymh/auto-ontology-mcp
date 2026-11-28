"""scripts/ci_semantic_timing.py reports semantic test time and cache step time separately."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import ci_semantic_timing as timing  # noqa: E402


def _step(name: str, start_sec: int, end_sec: int, conclusion: str = "success") -> dict:
    return {
        "name": name,
        "conclusion": conclusion,
        "started_at": f"2026-11-28T07:41:{start_sec:02d}Z",
        "completed_at": f"2026-11-28T07:41:{end_sec:02d}Z",
    }


def _mock_gh(monkeypatch, *, runs: list[dict], jobs_by_attempt: dict[tuple[int, int], dict], logs_by_attempt: dict[tuple[int, int], str]):
    def fake_gh(*args: str) -> str:
        if args[:2] == ("repo", "view"):
            return "gogoymh/auto-ontology-mcp\n"
        if args[0] == "api" and "/actions/workflows/ci.yml/runs" in args[1]:
            return json.dumps({"workflow_runs": runs})
        if args[0] == "api" and "/attempts/" in args[1]:
            parts = args[1].split("/")
            run_id = int(parts[parts.index("runs") + 1])
            attempt = int(parts[parts.index("attempts") + 1])
            return json.dumps(jobs_by_attempt[(run_id, attempt)])
        if args[:2] == ("run", "view"):
            run_id = int(args[2])
            attempt = int(args[args.index("--attempt") + 1])
            return logs_by_attempt[(run_id, attempt)]
        raise AssertionError(f"unexpected gh call: {args}")

    monkeypatch.setattr(timing, "gh", fake_gh)


def test_attempts_and_report_separate_semantic_and_cache_step_times_on_miss_and_hit(monkeypatch, caplog):
    runs = [{"id": 100, "event": "pull_request", "run_attempt": 3}]
    jobs = {
        # Attempt 1: failed cache miss must be excluded from successful miss/hit summary.
        (100, 1): {
            "jobs": [
                {
                    "conclusion": "failure",
                    "steps": [
                        _step("Cache fastembed model", 0, 0),
                        _step("Run semantic tests", 0, 5, conclusion="failure"),
                        _step("Post Cache fastembed model", 5, 5, conclusion="skipped"),
                    ],
                }
            ]
        },
        # Attempt 2: successful cache miss (22s semantic, 0s restore + 2s save = 2s cache step).
        (100, 2): {
            "jobs": [
                {
                    "conclusion": "success",
                    "steps": [
                        _step("Cache fastembed model", 10, 10),
                        _step("Run semantic tests", 10, 32),
                        _step("Post Cache fastembed model", 32, 34),
                    ],
                }
            ]
        },
        # Attempt 3: successful cache hit (21s semantic, 2s restore + 0s save = 2s cache step).
        (100, 3): {
            "jobs": [
                {
                    "conclusion": "success",
                    "steps": [
                        _step("Cache fastembed model", 10, 12),
                        _step("Run semantic tests", 13, 34),
                        _step("Post Cache fastembed model", 34, 34),
                    ],
                }
            ]
        },
    }
    logs = {
        (100, 1): f"Cache not found for input keys: Linux-{timing.CACHE_KEY}\n",
        (100, 2): f"Cache not found for input keys: Linux-{timing.CACHE_KEY}\n",
        (100, 3): f"Cache restored from key: Linux-{timing.CACHE_KEY}\n",
    }
    _mock_gh(monkeypatch, runs=runs, jobs_by_attempt=jobs, logs_by_attempt=logs)

    with caplog.at_level(logging.INFO, logger="ci_semantic_timing"):
        rc = timing.main(["--sha", "abc123", "--require-miss-and-hit", "--require-success"])
    assert rc == 0

    rows = timing.attempts("abc123", None)
    assert [(r["attempt"], r["cache"], r["semantic_seconds"], r["cache_seconds"]) for r in rows] == [
        (1, "miss", 5.0, 0.0),
        (2, "miss", 22.0, 2.0),
        (3, "hit", 21.0, 2.0),
    ]

    summary = timing.summarize(rows)
    assert summary["miss"] == {
        "count": 1,
        "semantic_seconds": 22.0,
        "cache_seconds": 2.0,
        "restore_seconds": 0.0,
        "save_seconds": 2.0,
        "total_seconds": 24.0,
    }
    assert summary["hit"] == {
        "count": 1,
        "semantic_seconds": 21.0,
        "cache_seconds": 2.0,
        "restore_seconds": 2.0,
        "save_seconds": 0.0,
        "total_seconds": 23.0,
    }
    assert summary["delta"] == {
        "semantic_saved_seconds": 1.0,
        "hit_cache_seconds": 2.0,
        "net_saved_seconds": -1.0,
    }

    text = "\n".join(caplog.messages)
    assert "successful cache miss (n=1): semantic 22.0s, cache step 2.0s (restore 0.0s, save 2.0s, total 24.0s)" in text
    assert "successful cache hit (n=1): semantic 21.0s, cache step 2.0s (restore 2.0s, save 0.0s, total 23.0s)" in text
    assert "cache net vs uncached semantic: -1.0s (semantic saved +1.0s, hit cache step 2.0s)" in text


def test_runs_without_cache_step_report_none_and_require_flags_enforce_order(monkeypatch):
    runs = [{"id": 200, "event": "push", "run_attempt": 1}]
    jobs = {
        (200, 1): {
            "jobs": [
                {
                    "conclusion": "success",
                    "steps": [_step("Run semantic tests", 0, 14)],
                }
            ]
        }
    }
    _mock_gh(monkeypatch, runs=runs, jobs_by_attempt=jobs, logs_by_attempt={})

    rows = timing.attempts("def456", "push")
    assert rows == [
        {
            "run": 200,
            "attempt": 1,
            "event": "push",
            "conclusion": "success",
            "semantic_step": "success",
            "semantic_seconds": 14.0,
            "cache": "none",
            "cache_seconds": None,
            "cache_restore_seconds": None,
            "cache_save_seconds": None,
        }
    ]
    assert timing.main(["--sha", "def456", "--require-success"]) == 0
    assert timing.main(["--sha", "def456", "--require-miss-and-hit"]) == 1
