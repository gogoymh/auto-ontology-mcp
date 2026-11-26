#!/usr/bin/env python3
"""Claude Code PreToolUse hook: run the polish lint on a completion report before mission_finish.

Reads the hook payload (JSON with tool_name and tool_input) on stdin. When the report's
summary or next has a pattern from .claude/skills/polish/SKILL.md, it exits 2 so the call is
blocked and prints, on stderr, each finding with the skill's before/after example, asking the
agent to rewrite the text without changing its meaning. Exits 0 otherwise (fails open on bad input).
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / ".claude/skills/polish/SKILL.md"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from lint_report import HINTS, lint  # noqa: E402

TOOLS = ("mission_finish",)  # matched by suffix: mcp__cua__mission_finish
FIELDS = ("summary", "next")  # body and commit are English by rule
# finding id -> section tag in the skill whose first example the hook shows
SECTIONS = {
    "translationese": "(translationese)",
    "crammed": "(crammed list)",
    "english": "(English report in a Korean channel)",
    "chained": "(chained sentence)",
}
# finding id -> phrase that picks the rule line under "함께 지킬 것"
RULES = {"nominal": "명사형 동사", "middot": "가운뎃점"}


def log(msg: str) -> None:
    print(f"[polish_hook] {msg}", file=sys.stderr)


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


def report(findings: dict[str, list[tuple[str, str]]], skill: str) -> str:
    lines = [
        "완료 보고에 윤문 패턴이 남아 있어 mission_finish를 막았습니다.",
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
    if not tool.endswith(TOOLS):
        return 0
    findings = {f: found for f in FIELDS if isinstance(args.get(f), str) and (found := lint(args[f]))}
    count = sum(len(v) for v in findings.values())
    log(f"tool={tool} fields={','.join(f for f in FIELDS if f in args)} findings={count}")
    if not findings:
        return 0
    skill = SKILL.read_text(encoding="utf-8") if SKILL.exists() else ""
    print(report(findings, skill), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
