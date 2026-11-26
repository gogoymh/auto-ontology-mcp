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
# 시 after a noun of two or more syllables (검토 시, 배포 시). Not 3시, 시간, 다시, 표시.
NOUNSTACK = [
    r"향후",
    r"(?<![가-힣])미(할당|설치|적용|반영|처리|확인)",
    r"비활성화",
    r"[가-힣]{2,} 시(?![가-힣])",
    r"(?<![가-힣])기존",
]
# 불명확한 names the subject, so 명확한 must not match inside it
SELFRATE = [r"(?<![가-힣])명확한", r"완결할 수 있", r"바로 마무리"]
# a sentence that only announces completion; a result in that same sentence is not an empty report
_DONE = re.compile(r"(?:마쳤|끝났|끝냈|완료했)습니다\.?$")
_RESULT = re.compile(r"\d+\.\d+|\d+점|올리|올라|올랐|올렸|내리|내려|줄었|줄어|늘었|늘어|고쳤|고쳐|통과")
# three or more paths plus a number and one of these verbs, even with no parentheses
_CRAM_ACT = re.compile(r"추가|구현|저장|수행|넣었|고쳤")
CONNECTORS = r"(상태에서|되었고|되었으며|했으며|반면|하므로|이므로)"  # 되어/하고/이고 stay allowed
# \b misses a path glued to a Korean particle (tasks.json과). Stop at an ASCII word char instead.
FILE = r"[\w./-]+\.(py|json|toml|md|ts|tsx|rs|yml|yaml|sql)(?![A-Za-z0-9_])"
HINTS = {
    "translationese": "번역투: 짧은 동사로 고친다 (패턴 1)",
    "nominal": "명사형 동사: '만들다', '잇다', '올라왔다'로 고친다 (함께 지킬 것)",
    "crammed": "나열: 동작을 먼저 쓰고 수치와 파일 이름은 뒤 문장으로 뺀다 (패턴 2)",
    "english": "영어 보고: 문장을 한국어로 쓰고 식별자만 원문으로 둔다 (패턴 3)",
    "chained": "긴 문장: 사실 하나에 한 문장으로 나눈다 (패턴 4)",
    "middot": "가운뎃점: '구현하고 검증해'처럼 동사로 푼다 (함께 지킬 것)",
    "nounstack": "한자어 명사: '앞으로', '설치하지 않으면', '맡은 사람이 없다'처럼 풀어 쓴다 (패턴 5)",
    "selfrate": "스스로 매긴 평가: '명확한', '완결할 수 있는'을 지우고 근거 사실을 쓴다 (패턴 6)",
    "empty": "빈 보고: 무엇이 달라졌는지 한 문장을 붙인다 (패턴 7)",
}


def log(msg: str) -> None:
    print(f"[lint_report] {msg}", file=sys.stderr)


def sentences(text: str) -> list[str]:
    text = re.sub(r"^\s*>\s?", "", text, flags=re.M)
    parts = re.split(r"(?<=[.!?다요])\s+|\n+", text)
    return [p.strip() for p in parts if p.strip()]


def empty_report(text: str) -> bool:
    """True when every sentence only says that something finished."""
    parts = sentences(text)
    return bool(parts) and all(_DONE.search(part) and not _RESULT.search(part) for part in parts)


def lint(text: str) -> list[tuple[str, str]]:
    """Return (pattern id, excerpt) for every finding in text."""
    found: list[tuple[str, str]] = []
    parts = sentences(text)
    if empty_report(text):
        found.append(("empty", parts[0][:60]))
    for s in parts:
        plain = re.sub(r"`[^`]*`", "", s)
        for rx in TRANSLATIONESE:
            m = re.search(rx, plain)
            if m:
                found.append(("translationese", m.group(0)))
        for kind, rxs in (("nominal", NOMINAL), ("nounstack", NOUNSTACK), ("selfrate", SELFRATE)):
            for rx in rxs:
                m = re.search(rx, plain)
                if m:
                    found.append((kind, m.group(0)))
        if "·" in plain:
            found.append(("middot", plain[max(0, plain.find("·") - 6) : plain.find("·") + 6]))
        parens = re.findall(r"\(([^)]*)\)", s)
        paren_hit = len(parens) >= 3 or (len(parens) >= 2 and any(re.search(FILE, p) for p in parens))
        file_hit = len(re.findall(FILE, s)) >= 3 and re.search(r"\d", s) and _CRAM_ACT.search(s)
        if paren_hit or file_hit:
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
