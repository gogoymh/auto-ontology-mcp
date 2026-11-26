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
    "(noun stack)": "nounstack",
    "(self-rating)": "selfrate",
    "(empty report)": "empty",
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


@pytest.mark.parametrize(
    "text",
    ["검색 점수가 올랐습니다.", "실행 시간이 줄었습니다.", "미션 AOM-3을 맡습니다. 검색 점수를 고칩니다.", "AOM-17을 마쳤습니다. recall@3이 0.84로 올랐습니다."],
)
def test_plain_reports_pass(text: str) -> None:
    assert _run(text).returncode == 0, _run(text).stdout


def test_empty_outside_examples() -> None:
    flagged = [
        "방금 작업을 마쳤습니다.",
        "리뷰를 마쳤습니다.",
        "문서 정리를 끝냈습니다.",
        "병합과 푸시를 마쳤습니다.",
        "AOM-30 구현을 완료했습니다.",
    ]
    passed = [
        "리뷰를 마쳤습니다. #28은 병합해도 됩니다.",
        "AOM-17을 마쳤습니다. recall@3이 0.84로 올랐습니다.",
        "검색 점수가 올랐습니다.",
        "recall을 0.84로 올려 마쳤습니다.",
    ]
    for text in flagged:
        res = _run(text)
        assert res.returncode == 1, (text, res.stdout)
        assert re.search(r"^empty: ", res.stdout, re.M), (text, res.stdout)
    for text in passed:
        res = _run(text)
        assert res.returncode == 0, (text, res.stdout)


def test_nounstack_outside_examples() -> None:
    flagged = ["검토 시 기준선을 봅니다.", "기존으로 동작합니다.", "배포 시 로그를 남깁니다."]
    passed = ["실행 시간이 줄었습니다.", "3시에 배포합니다.", "다시 실행합니다.", "결과를 표시합니다.", "검토할 때 기준선을 봅니다."]
    for text in flagged:
        res = _run(text)
        assert res.returncode == 1, (text, res.stdout)
        assert re.search(r"^nounstack: ", res.stdout, re.M), (text, res.stdout)
    for text in passed:
        res = _run(text)
        assert res.returncode == 0, (text, res.stdout)


def test_selfrate_outside_examples() -> None:
    bad = _run("명확한 작업입니다.")
    assert bad.returncode == 1, bad.stdout
    assert re.search(r"^selfrate: ", bad.stdout, re.M), bad.stdout
    good = _run("기준이 불명확한 문장입니다.")
    assert good.returncode == 0, good.stdout


def test_pattern_count_is_five_to_seven() -> None:
    nums = [int(n) for n in re.findall(r"^## 패턴 (\d+)\. ", SKILL, re.M)]
    assert nums == list(range(1, len(nums) + 1))
    assert 5 <= len(nums) <= 7


def test_crammed_and_passive_outside_examples() -> None:
    crammed = "76개 과제를 benchmarks/tasks.json과 benchmarks/run_benchmark.py와 benchmarks/baseline.json에 저장했습니다."
    res = _run(crammed)
    assert res.returncode == 1, res.stdout
    assert re.search(r"^crammed: ", res.stdout, re.M), res.stdout
    for text in ("병합되어 있고 설치되어 있으면 검색은 그대로입니다.", "tasks.json과 baseline.json을 읽었습니다."):
        ok = _run(text)
        assert ok.returncode == 0, (text, ok.stdout)
