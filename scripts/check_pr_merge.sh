#!/usr/bin/env bash
# Merge the given PRs onto the current origin/main in a throwaway clone and run the given tests.
# Usage: scripts/check_pr_merge.sh "28 31" "tests/test_inference_pairs.py tests/test_search_cache.py"
set -euo pipefail
prs=$1; tests=$2
repo=$(git rev-parse --show-toplevel)
tmp=$(mktemp -d /tmp/pq-prcheck.XXXXXX)
trap 'rm -rf "$tmp"' EXIT
git -C "$repo" fetch -q origin main
git clone -q --no-checkout "$repo" "$tmp/r"
cd "$tmp/r"
git fetch -q "$(git -C "$repo" remote get-url origin)" main
git checkout -q --detach FETCH_HEAD
for n in $prs; do
  git fetch -q "$(git -C "$repo" remote get-url origin)" "pull/$n/head"
  git -c user.name=check -c user.email=check@local merge -q --no-edit FETCH_HEAD \
    || { echo "PR #$n does not merge cleanly onto main"; exit 1; }
  echo "merged PR #$n"
done
UV_PROJECT_ENVIRONMENT="$tmp/venv" uv run -q --frozen pytest -q -p no:cacheprovider $tests
