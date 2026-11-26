#!/usr/bin/env python3
"""Flag the polish skill's patterns in a draft before it is posted.

Usage: python3 scripts/lint_report.py [draft.txt]   (reads stdin without a file)
Prints one line per finding and exits 1 when any pattern is found, 0 when the draft is clean.
The patterns mirror .claude/skills/polish/SKILL.md; see its "완료 보고 전 점검" section.
"""
import re
import sys
from pathlib import Path

# pattern id -> (regexes for phrases, hint)
TRANSLATIONESE = [
    r"부합합니다", r"정확히 맞", r"을\s?대상으로|를\s?대상으로", r"수행(합|했|하)", r"하도록 구현했",
    r"통과(를|함을) 확인했", r"이를 통해", r"해당 ", r"가 아니라 .*입니다",
]
NOMINAL = [r"구축", r"연동", r"등록되었"]
CONNECTORS = r"(상태에서|되었고|되었으며|했으며|반면|하므로|이므로|되어)"  # heavy joins; plain 하고/이고 stay allowed
FILE = r"[\w./-]+\.(py|json|toml|md|ts|tsx|rs|yml|yaml|sql)\b"
HINTS = {
    "translationese": "번역투: 짧은 동사로 고친다 (패턴 1)",
    "nominal": "명사형 동사: '만들다', '잇다', '올라왔다'로 고친다 (함께 지킬 것)",
    "crammed": "나열: 동작을 먼저 쓰고 수치와 파일 이름은 뒤 문장으로 뺀다 (패턴 2)",
    "english": "영어 보고: 문장을 한국어로 쓰고 식별자만 원문으로 둔다 (패턴 3)",
    "chained": "긴 문장: 사실 하나에 한 문장으로 나눈다 (패턴 4)",
    "middot": "가운뎃점: '구현하고 검증해'처럼 동사로 푼다 (함께 지킬 것)",
}


def log(msg: str) -> None:
    print(f"[lint_report] {msg}", file=sys.stderr)


def sentences(text: str) -> list[str]:
    text = re.sub(r"^\s*>\s?", "", text, flags=re.M)
    parts = re.split(r"(?<=[.!?다요])\s+|\n+", text)
    return [p.strip() for p in parts if p.strip()]


def lint(text: str) -> list[tuple[str, str]]:
    """Return (pattern id, excerpt) for every finding in text."""
    found: list[tuple[str, str]] = []
    for s in sentences(text):
        plain = re.sub(r"`[^`]*`", "", s)
        for rx in TRANSLATIONESE:
            m = re.search(rx, plain)
            if m:
                found.append(("translationese", m.group(0)))
        for rx in NOMINAL:
            m = re.search(rx, plain)
            if m:
                found.append(("nominal", m.group(0)))
        if "·" in plain:
            found.append(("middot", plain[max(0, plain.find("·") - 6) : plain.find("·") + 6]))
        parens = re.findall(r"\(([^)]*)\)", s)
        if len(parens) >= 3 or (len(parens) >= 2 and any(re.search(FILE, p) for p in parens)):
            found.append(("crammed", s[:60]))
        if not re.search(r"[가-힣]", s) and len(re.findall(r"\b[A-Za-z]{2,}\b", plain)) >= 3:
            found.append(("english", s[:60]))
        joins = re.findall(CONNECTORS + r"[,\s]", plain)
        if len(joins) >= 2 or (re.search(r"상태에서|반면", plain) and len(plain) > 50):
            found.append(("chained", s[:60]))
    return found


def main() -> int:
    if len(sys.argv) > 2:
        print(__doc__)
        return 2
    src = sys.argv[1] if len(sys.argv) == 2 else "-"
    text = sys.stdin.read() if src == "-" else Path(src).read_text(encoding="utf-8")
    found = lint(text)
    for kind, excerpt in found:
        print(f"{kind}: '{excerpt}' -> {HINTS[kind]}")
    log(f"source={src} chars={len(text)} findings={len(found)}")
    if not found:
        print("ok: no polish patterns found")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
