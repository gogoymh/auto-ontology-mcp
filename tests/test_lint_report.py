"""scripts/lint_report.py must flag every 'before' example in the polish skill and pass every 'after' one."""
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SKILL = (ROOT / ".claude/skills/polish/SKILL.md").read_text(encoding="utf-8")
LINT = ROOT / "scripts/lint_report.py"
# section tag in the skill -> finding id the linter must report for its 'before' examples
SECTIONS = {
    "(translationese)": "translationese",
    "(crammed list)": "crammed",
    "(English report in a Korean channel)": "english",
    "(chained sentence)": "chained",
}


def _examples(label: str) -> list[tuple[str, str]]:
    out = []
    for tag, kind in SECTIONS.items():
        start = SKILL.index(tag)
        end = SKILL.find("\n## ", start)
        sec = SKILL[start : end if end > 0 else len(SKILL)]
        out += [(kind, ex) for ex in re.findall(label + r":\n((?:> .*\n?)+)", sec)]
    assert out, f"no '{label}' examples in the skill"
    return out


def _run(text: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(LINT)], input=text, capture_output=True, text=True, timeout=10)


@pytest.mark.parametrize(("kind", "text"), _examples("고치기 전"))
def test_before_examples_are_flagged(kind: str, text: str) -> None:
    res = _run(text)
    assert res.returncode == 1, res.stdout
    assert re.search(rf"^{kind}: ", res.stdout, re.M), res.stdout


@pytest.mark.parametrize(("kind", "text"), _examples("고친 뒤"))
def test_after_examples_pass(kind: str, text: str) -> None:
    res = _run(text)
    assert res.returncode == 0, res.stdout
    assert "ok: no polish patterns found" in res.stdout


def test_lint_reads_a_file(tmp_path: Path) -> None:
    draft = tmp_path / "draft.txt"
    draft.write_text("I finished reviewing AOM-11.\n", encoding="utf-8")
    res = subprocess.run([sys.executable, str(LINT), str(draft)], capture_output=True, text=True, timeout=10)
    assert res.returncode == 1 and "english:" in res.stdout
