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
        # the lint skips blockquotes, so lint the example's text without its '> ' markers
        out += [(kind, re.sub(r"^> ?", "", ex, flags=re.M)) for ex in re.findall(label + r":\n((?:> .*\n?)+)", sec)]
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


def _kinds(text: str) -> tuple[int, set[str]]:
    res = _run(text)
    return res.returncode, set(re.findall(r"^(\w+): ", res.stdout, re.M))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("필요 없이 작업을 마쳤습니다.", ["필요 없이 작업을 마쳤습니다."]),
        ("중요 지표를 고쳤습니다. 리뷰를 마쳤습니다.", ["중요 지표를 고쳤습니다.", "리뷰를 마쳤습니다."]),
        ("다 끝났습니다.", ["다 끝났습니다."]),
        ("예를 들어 e.g. 이렇게 씁니다. 다음 문장입니다.", ["예를 들어 e.g. 이렇게 씁니다.", "다음 문장입니다."]),
        ("1. 작업을 마쳤습니다.\n- 리뷰를 마쳤어요 다음은 내일입니다", ["작업을 마쳤습니다.", "리뷰를 마쳤어요", "다음은 내일입니다"]),
        ("점수는 0.84. 다음입니다.", ["점수는 0.84.", "다음입니다."]),
    ],
)
def test_sentence_split(text: str, expected: list[str]) -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    from lint_report import sentences

    assert sentences(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "다 끝났습니다.",
        "필요 없이 작업을 마쳤습니다.",
        "작업을 마쳤어요.",
        "작업이 완료되었습니다.",
        "AOM-30 작업을 마무리했습니다.",
        "작업을 마쳤습니다!",
        "리뷰를 마쳤습니다. 확인 부탁드립니다.",
        "작업을 마쳤습니다. 감사합니다.",
        "1. 작업을 마쳤습니다.\n2. 리뷰를 마쳤습니다.",
    ],
)
def test_empty_variants_flagged(text: str) -> None:
    code, kinds = _kinds(text)
    assert code == 1 and "empty" in kinds, (text, kinds)


@pytest.mark.parametrize(
    "text",
    [
        "작업을 마쳤어요. recall@3이 0.84로 올랐어요.",
        "리뷰를 마쳤습니다. #28은 병합해도 됩니다. 감사합니다.",
        "중요 지표를 고쳤습니다. 리뷰를 마쳤습니다.",
        "감사합니다.",
    ],
)
def test_empty_variants_with_result_pass(text: str) -> None:
    code, kinds = _kinds(text)
    assert "empty" not in kinds, (text, kinds)


@pytest.mark.parametrize(
    "text",
    [
        "scripts/a.py와 scripts/b.py와 tests/c.py에 테스트 5개를 더했습니다.",
        "a.py, b.py, c.py로 검사 3개를 만들었습니다.",
        "AOM-33에서 a.py, b.py, c.py에 패턴 2개를 넣었습니다.",
    ],
)
def test_crammed_without_parens_flagged(text: str) -> None:
    code, kinds = _kinds(text)
    assert code == 1 and "crammed" in kinds, (text, kinds)


@pytest.mark.parametrize(
    "text",
    [
        "test_v2.py와 a.py와 b.py를 고쳤습니다.",
        "AOM-33에서 a.py, b.py, c.py를 고쳤습니다.",
        "#28에서 a.py, b.py, c.py를 바꿨습니다.",
        "a.py, b.py, c.py 3개를 읽었습니다.",
    ],
)
def test_crammed_without_parens_pass(text: str) -> None:
    code, kinds = _kinds(text)
    assert "crammed" not in kinds, (text, kinds)


# AOM-29: wording the writer quotes (quotes, code blocks, blockquotes) is not their own, so it is not checked.
@pytest.mark.parametrize(
    "text",
    [
        "'향후'를 '앞으로'로 고칩니다.",
        '"정확히 부합합니다"를 지웠습니다. 이제 린트가 이 말을 잡습니다.',
        "“미설치 환경”을 풀어 썼습니다.",
        "‘명확한 작업’ 같은 평가어를 지웠습니다.",
        "「해당 스크립트」를 '이 스크립트'로 바꿉니다.",
        "사용자가 쓴 말은 이렇습니다.\n> 향후 배포 시 해당 기능을 연동합니다.\n이 문장을 고칩니다.",
        "로그는 이렇습니다.\n```\nI reviewed both new PRs and found nothing.\n미설치 상태에서 실행되었고 실패했으며 종료\n```\n다시 실행해 고쳤습니다.",
        "출력입니다.\n~~~text\n해당 파일을 수행합니다.\n~~~\n이제 통과합니다.",
    ],
)
def test_quoted_text_is_skipped(text: str) -> None:
    code, kinds = _kinds(text)
    assert code == 0, (text, kinds)


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("'향후'를 고쳤고 향후 배포도 고칩니다.", "nounstack"),
        ("인용 밖의 해당 스크립트를 고칩니다.\n> 향후 고칩니다.", "translationese"),
        ("```\n코드\n```\n해당 스크립트를 고칩니다.", "translationese"),
        ("I don't think the 'fix' works well here.", "english"),
        ("리뷰를 마쳤습니다.\n> #28은 병합해도 됩니다.", "empty"),
        ("`a.py`, `b.py`, `c.py`에 테스트 5개를 더했습니다.", "crammed"),
    ],
)
def test_text_outside_quotes_is_still_checked(text: str, kind: str) -> None:
    code, kinds = _kinds(text)
    assert code == 1 and kind in kinds, (text, kinds)


def test_skill_documents_quote_exclusion() -> None:
    start = SKILL.index("### 점검하지 않는 곳")
    sec = SKILL[start : SKILL.find("\n## ", start) if SKILL.find("\n## ", start) > 0 else len(SKILL)]
    for word in ("따옴표", "코드 블록", "인용문"):
        assert word in sec, word
    before = re.findall(r"고치기 전:\n((?:> .*\n?)+)", sec)
    after = re.findall(r"고친 뒤:\n((?:> .*\n?)+)", sec)
    assert before and len(before) == len(after)
    for b, a in zip(before, after):
        assert _run(re.sub(r"^> ?", "", b, flags=re.M)).returncode == 1, b
        assert _run(re.sub(r"^> ?", "", a, flags=re.M)).returncode == 0, a


# AOM-35: past handoff and merge posts were flagged as crammed only for particle hints like 을(를).
@pytest.mark.parametrize(
    "text",
    [
        "Antigravity이(가) 사용 한도에 닿아 미션 AOM-5을(를) Claude(으)로 이어서 합니다.",
        "미션 AOM-25을(를) 마쳐 병합했습니다: 이제 리뷰 댓글(issue_update의 comment)과 채널 메시지(message_post)도 훅이 막습니다.",
    ],
)
def test_particle_parens_are_not_crammed(text: str) -> None:
    code, kinds = _kinds(text)
    assert "crammed" not in kinds, (text, kinds)


@pytest.mark.parametrize(
    "text",
    [
        "AOM-13을(를) 마쳐 과제 셋(benchmarks/tasks.json)과 스크립트(benchmarks/run_benchmark.py)를 추가했습니다.",
        "검색(어휘)과 점수(결합)와 색인(증분)을 바꿨습니다.",
    ],
)
def test_particle_parens_keep_real_crammed(text: str) -> None:
    code, kinds = _kinds(text)
    assert code == 1 and "crammed" in kinds, (text, kinds)
