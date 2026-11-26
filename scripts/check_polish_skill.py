#!/usr/bin/env python3
"""Check that .claude/skills/polish/SKILL.md covers one pattern with a before/after example.

Usage: python3 scripts/check_polish_skill.py <frontmatter|translationese|crammed|english|chained|checklist|guide>
Exits 0 only when the requested part is present and its rewrite drops the flagged phrase.
"checklist" checks the pre-report procedure and checklist; "guide" checks that CLAUDE.md points report writers to it.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / ".claude/skills/polish/SKILL.md"
GUIDE = ROOT / "CLAUDE.md"
LINT = ROOT / "scripts/lint_report.py"
CHECKLIST_TAG = "## 완료 보고 전 점검"

# section heading tag -> regex the "before" example must match and the "after" example must not
PATTERNS = {
    "translationese": ("(translationese)", r"부합합니다"),
    "crammed": ("(crammed list)", r"(\([^)]*\.(json|py|toml)[^)]*\).*){2,}"),
    "english": ("(English report in a Korean channel)", r"^[^가-힣]*[A-Za-z]{3,}[^가-힣]*$"),
    "chained": ("(chained sentence)", r"상태에서.*되었고"),
}


def log(msg: str) -> None:
    print(f"[check_polish_skill] {msg}")


def section(text: str, tag: str) -> str:
    start = text.find(tag)
    if start < 0:
        return ""
    nxt = text.find("\n## ", start)
    return text[start : nxt if nxt > 0 else len(text)]


def examples(sec: str, label: str) -> list[str]:
    return re.findall(label + r":\n((?:> .*\n?)+)", sec)


def check(name: str) -> bool:
    if not SKILL.exists():
        log(f"missing {SKILL}")
        return False
    text = SKILL.read_text(encoding="utf-8")
    if name == "frontmatter":
        m = re.match(r"---\n(.*?)\n---\n", text, re.S)
        ok = bool(m and re.search(r"^name: polish$", m.group(1), re.M) and re.search(r"^description: .{40,}", m.group(1), re.M))
        log(f"frontmatter ok={ok}")
        return ok
    if name == "checklist":
        sec = section(text, CHECKLIST_TAG)
        steps = re.findall(r"^\d+\. .+", sec, re.M)
        items = re.findall(r"^- \[ \] .+", sec, re.M)
        covered = {n for n in "1234" if any(f"패턴 {n}" in i for i in items)}
        ok = bool(sec) and len(steps) >= 3 and "scripts/lint_report.py" in sec and LINT.exists() and covered == set("1234")
        log(f"checklist ok={ok} steps={len(steps)} items={len(items)} patterns={sorted(covered)}")
        return ok
    if name == "guide":
        guide = GUIDE.read_text(encoding="utf-8") if GUIDE.exists() else ""
        need = [".claude/skills/polish/SKILL.md", CHECKLIST_TAG.lstrip("# "), "scripts/lint_report.py", "mission_finish"]
        missing = [n for n in need if n not in guide]
        ok = not missing and CHECKLIST_TAG in text
        log(f"guide ok={ok} missing={missing}")
        return ok
    tag, rx = PATTERNS[name]
    sec = section(text, tag)
    before, after = examples(sec, "고치기 전"), examples(sec, "고친 뒤")
    if not before or len(before) != len(after):
        log(f"{name}: need paired examples, got before={len(before)} after={len(after)}")
        return False
    flags = re.M if name == "english" else 0
    hit = [b for b in before if re.search(rx, b.replace("> ", ""), flags)]
    if not hit:
        log(f"{name}: no 'before' example shows the pattern")
        return False
    for b, a in zip(before, after):
        if re.search(rx, b.replace("> ", ""), flags) and re.search(rx, a.replace("> ", ""), flags):
            log(f"{name}: rewrite still has the pattern: {a.strip()[:80]}")
            return False
        if name == "english" and not re.search(r"[가-힣]", a):
            log(f"{name}: rewrite is not Korean")
            return False
    log(f"{name}: ok ({len(before)} examples)")
    return True


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in {"frontmatter", "checklist", "guide", *PATTERNS}:
        print(__doc__)
        sys.exit(2)
    sys.exit(0 if check(sys.argv[1]) else 1)
