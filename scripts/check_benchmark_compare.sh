#!/usr/bin/env bash
# Run the real benchmark against the baseline and confirm the step summary gets the comparison,
# including the hybrid search and vector sync latency rows. Latency fails only in CI (--fail-on-latency
# against a baseline from the same GitHub runners); on a local machine WARN still passes.
set -euo pipefail
dir=$(mktemp -d /tmp/pq-bench.XXXXXX)
trap 'rm -rf "$dir"' EXIT
uv run python benchmarks/run_benchmark.py --compare benchmarks/baseline.json --summary-file "$dir/summary.md" >/dev/null
grep -Eq '### Benchmark Comparison: `(OK|WARN)`' "$dir/summary.md"
grep -Eq '^\| hybrid_search \| [0-9.]+ \|' "$dir/summary.md"
grep -Eq '^\| vector_sync \| [0-9.]+ \|' "$dir/summary.md"
