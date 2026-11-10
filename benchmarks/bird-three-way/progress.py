#!/usr/bin/env python3
"""Print a one-line status per transcript: model, MCP servers, tool calls so far, finished or not."""

import collections
import json
import sys
from pathlib import Path

for arg in sys.argv[1:]:
    p = Path(arg)
    tools: collections.Counter = collections.Counter()
    init, result = {}, {}
    for line in p.read_text(errors="replace").splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "system" and ev.get("subtype") == "init":
            init = ev
        elif ev.get("type") == "assistant":
            for b in ev.get("message", {}).get("content", []):
                if b.get("type") == "tool_use":
                    tools[b["name"].replace("mcp__auto-ontology__", "")] += 1
        elif ev.get("type") == "result":
            result = ev
    servers = [(s["name"], s["status"]) for s in init.get("mcp_servers", [])]
    state = f"DONE {result.get('subtype')} ${result.get('total_cost_usd', 0):.2f}" if result else "running"
    print(f"{p.parent.name if p.name == 'transcript.jsonl' else p.stem}: {init.get('model')} {servers} {state} {dict(tools.most_common(8))}")
