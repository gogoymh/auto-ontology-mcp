#!/usr/bin/env bash
# Block a call with the hook under the same python3 .claude/settings.json uses, then count the log:
# the blocked pattern must show up in the table with its tool.
set -euo pipefail
cd "$(dirname "$0")/.."
dir=$(mktemp -d /tmp/pq-polish.XXXXXX)
trap 'rm -rf "$dir"' EXIT
export POLISH_BLOCK_LOG="$dir/polish_blocks.jsonl"
set +e
echo '{"tool_name":"mcp__preconquer__message_post","tool_input":{"text":"검토 시 기준선을 봅니다."}}' \
  | python3 scripts/polish_hook.py 2>/dev/null
status=$?
set -e
[ "$status" -eq 2 ]
python3 scripts/polish_blocks.py | grep -Eq '^nounstack +1 +1$'
python3 scripts/polish_blocks.py --by tool | grep -Eq '^message_post +1 +1$'
