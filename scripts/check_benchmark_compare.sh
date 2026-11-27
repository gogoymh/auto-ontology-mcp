#!/usr/bin/env bash
# Run the real benchmark against the baseline and confirm the step summary gets the comparison.
set -euo pipefail
dir=$(mktemp -d /tmp/pq-bench.XXXXXX)
trap 'rm -rf "$dir"' EXIT
uv run python benchmarks/run_benchmark.py --compare benchmarks/baseline.json --summary-file "$dir/summary.md" >/dev/null
grep -q '### Benchmark Comparison: `OK`' "$dir/summary.md"
