#!/usr/bin/env python3
"""Claude Code PreToolUse hook: run the polish lint on agent reports before they are posted.

Checks mission_finish (summary, next), issue_update (comment, title, body), message_post (text),
issue_create (title, body), doc_write (text) and doc_comment (text). Title fields get the title checks as well.
Reads the hook payload (JSON with tool_name and tool_input) on stdin. When one of those
fields has a pattern from .claude/skills/polish/SKILL.md, it exits 2 so the call is
blocked and prints, on stderr, each finding with the skill's before/after example, asking the
agent to rewrite the text without changing its meaning. Exits 0 otherwise (fails open on bad input).

Every block appends one JSON line per finding ({"ts", "tool", "field", "pattern"}) to polish_blocks.jsonl
in the repository's shared .git folder, so every worktree writes to the same file. POLISH_BLOCK_LOG
overrides the path. scripts/polish_blocks.py counts the lines per pattern.
"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / ".claude/skills/polish/SKILL.md"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from lint_report import HINTS, lint, lint_title  # noqa: E402

# tool name suffix (mcp__cua__<name> or mcp__preconquer__<name>) -> (fields to lint, what the text is, in the block message)
TOOLS = {
    "mission_finish": (("summary", "next"), "완료 보고"),  # body and commit are English by rule
    "issue_update": (("comment", "title", "body"), "이슈 댓글과 본문"),
    "message_post": (("text",), "채널 메시지"),
    "issue_create": (("title", "body"), "이슈 제목과 본문"),
    "doc_write": (("text",), "문서"),
    "doc_comment": (("text",), "문서 댓글"),  # quote is the document's own words, not the agent's
}
# finding id -> section tag in the skill whose first example the hook shows
SECTIONS = {
    "translationese": "(translationese)",
    "crammed": "(crammed list)",
    "english": "(English report in a Korean channel)",
    "chained": "(chained sentence)",
    "nounstack": "(noun stack)",
    "selfrate": "(self-rating)",
    "empty": "(empty report)",
    "title": "(title)",
}
# finding id -> phrase that picks the rule line under "함께 지킬 것"
RULES = {"nominal": "명사형 동사", "middot": "가운뎃점"}


def log(msg: str) -> None:
    print(f"[polish_hook] {msg}", file=sys.stderr)


def block_log(root: Path = ROOT) -> Path:
    """Where blocks are recorded: $POLISH_BLOCK_LOG, else the shared .git folder (one file for all worktrees)."""
    env = os.environ.get("POLISH_BLOCK_LOG")
    if env:
        return Path(env)
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=root, capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip()
        return (Path(root) / out).resolve() / "polish_blocks.jsonl"  # out may be relative to root
    except (OSError, subprocess.SubprocessError) as exc:
        log(f"no git dir ({exc}); recording in {root}/.git")
        return root / ".git" / "polish_blocks.jsonl"


def record(tool: str, findings: dict, path: Path = None) -> None:
    """Append one line per finding. A write failure is logged and never changes the hook's verdict."""
    path = path or block_log()
    ts = datetime.now().astimezone().isoformat(timespec="microseconds")  # one ts per blocked call
    lines = [
        json.dumps({"ts": ts, "tool": tool, "field": field, "pattern": kind}, ensure_ascii=False)
        for field, found in findings.items()
        for kind, _ in found
    ]
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        log(f"recorded {len(lines)} findings in {path}")
    except OSError as exc:
        log(f"could not record blocks in {path} ({exc})")


def example(kind: str, skill: str) -> str:
    """Return the skill's fix example for a finding id, or '' when the skill has none."""
    if kind in RULES:
        m = re.search(r"^- .*" + RULES[kind] + r".*$", skill, re.M)
        return m.group(0)[2:] if m else ""
    start = skill.find(SECTIONS.get(kind, "\0"))
    if start < 0:
        return ""
    end = skill.find("\n## ", start)
    sec = skill[start : end if end > 0 else len(skill)]
    m = re.search(r"고치기 전:\n((?:> .*\n?)+)\s*고친 뒤:\n((?:> .*\n?)+)", sec)
    if not m:
        return ""
    return f"고치기 전:\n{m.group(1).rstrip()}\n고친 뒤:\n{m.group(2).rstrip()}"


def report(findings: dict[str, list[tuple[str, str]]], skill: str, what: str = "완료 보고", tool: str = "mission_finish") -> str:
    lines = [
        f"{what}에 윤문 패턴이 남아 있어 {tool}를 막았습니다.",
        "아래 예처럼 문장만 고쳐 다시 부르세요. 뜻, 수치, 파일 이름, 티켓 번호, 명령은 그대로 둡니다.",
    ]
    for field, found in findings.items():
        lines.append(f"\n[{field}]")
        lines += [f"- {kind}: '{excerpt}' -> {HINTS[kind]}" for kind, excerpt in found]
    for kind in dict.fromkeys(k for found in findings.values() for k, _ in found):
        ex = example(kind, skill)
        if ex:
            lines += [f"\n{kind} 고친 예:", ex]
    return "\n".join(lines)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        tool = str(payload.get("tool_name", ""))
        args = payload.get("tool_input") or {}
    except (ValueError, AttributeError) as exc:
        log(f"skip: unreadable payload ({exc})")
        return 0
    name = next((t for t in TOOLS if tool.endswith(t)), None)
    if name is None or not isinstance(args, dict):
        return 0
    fields, what = TOOLS[name]
    findings = {
        f: found
        for f in fields
        if isinstance(args.get(f), str) and (found := (lint_title if f == "title" else lint)(args[f]))
    }
    count = sum(len(v) for v in findings.values())
    log(f"tool={tool} fields={','.join(f for f in fields if f in args)} findings={count}")
    if not findings:
        return 0
    record(name, findings)
    skill = SKILL.read_text(encoding="utf-8") if SKILL.exists() else ""
    print(report(findings, skill, what, name), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
