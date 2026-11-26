"""scripts/polish_hook.py must block reports (mission_finish, issue_update comment, message_post) with polish patterns."""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "scripts/polish_hook.py"
sys.path.insert(0, str(ROOT / "scripts"))
import lint_report  # noqa: E402
import polish_hook  # noqa: E402

TOOL = "mcp__cua__mission_finish"
CLEAN = "완료 보고 직전에 윤문 점검을 자동으로 돌립니다. 패턴이 걸리면 고친 예를 보여 주고 다시 쓰게 합니다."
DIRTY = "벤치마크 자동 실행·기준선 비교 연동은 @benchmark의 역할에 정확히 부합합니다."


def _run(tool: str, args: dict) -> subprocess.CompletedProcess:
    payload = json.dumps({"tool_name": tool, "tool_input": args}, ensure_ascii=False)
    return subprocess.run([sys.executable, str(HOOK)], input=payload, capture_output=True, text=True, timeout=10)


def test_blocks_summary_with_skill_example() -> None:
    res = _run(TOOL, {"id": 1, "summary": DIRTY, "next": CLEAN})
    assert res.returncode == 2, res.stderr
    assert "[summary]" in res.stderr and "[next]" not in res.stderr
    assert "translationese: " in res.stderr
    assert "고치기 전:" in res.stderr and "고친 뒤:" in res.stderr
    assert "> 벤치마크를 자동으로 돌려 기준선과 비교하는 일은 제 역할입니다." in res.stderr
    assert "뜻, 수치, 파일 이름, 티켓 번호, 명령은 그대로" in res.stderr


def test_blocks_next_and_names_field() -> None:
    res = _run(TOOL, {"id": 1, "summary": CLEAN, "next": "후속 티켓\n해당 스크립트를 CI에 연동합니다."})
    assert res.returncode == 2, res.stderr
    assert "[next]" in res.stderr and "[summary]" not in res.stderr
    assert "nominal: " in res.stderr and "명사형 동사" in res.stderr


def test_passes_clean_report() -> None:
    res = _run(TOOL, {"id": 1, "summary": CLEAN, "next": "후속 티켓\n다른 도구에도 같은 점검을 붙입니다.", "commit": "feat: add hook"})
    assert res.returncode == 0, res.stderr


def test_blocks_bare_done_and_nounstack_outside_examples() -> None:
    done = _run("mcp__cua__issue_update", {"id": 3, "status": "review", "comment": "리뷰를 마쳤습니다."})
    assert done.returncode == 2 and "empty: " in done.stderr, done.stderr
    noun = _run("mcp__cua__message_post", {"text": "검토 시 기준선을 봅니다."})
    assert noun.returncode == 2 and "nounstack: " in noun.stderr, noun.stderr
    assert _run("mcp__cua__message_post", {"text": "3시에 배포합니다."}).returncode == 0
    unclear = _run("mcp__cua__issue_update", {"id": 3, "status": "review", "comment": "기준이 불명확한 문장입니다."})
    assert unclear.returncode == 0, unclear.stderr


def test_blocks_review_comment() -> None:
    res = _run("mcp__cua__issue_update", {"id": 3, "status": "review", "comment": "AOM-19 채점을 마쳤습니다."})
    assert res.returncode == 2, res.stderr
    assert "이슈 댓글" in res.stderr and "issue_update" in res.stderr and "[comment]" in res.stderr
    assert "empty: " in res.stderr and "> AOM-17을 마쳤습니다. `run_benchmark.py --semantic`" in res.stderr


def test_blocks_channel_message() -> None:
    res = _run("mcp__cua__message_post", {"channel": "general", "text": "미설치 또는 비활성화 환경에서는 기존 검색이 동작합니다."})
    assert res.returncode == 2, res.stderr
    assert "채널 메시지" in res.stderr and "message_post" in res.stderr and "[text]" in res.stderr
    assert "nounstack: '미설치'" in res.stderr and "고친 뒤:" in res.stderr


def test_blocks_english_message() -> None:
    res = _run("mcp__cua__message_post", {"text": "I reviewed both new PRs. #27 needs changes before it can merge."})
    assert res.returncode == 2 and "english: " in res.stderr, res.stderr


def test_passes_clean_comment_and_message() -> None:
    assert _run("mcp__cua__issue_update", {"id": 3, "status": "review", "comment": CLEAN}).returncode == 0
    assert _run("mcp__cua__message_post", {"text": CLEAN}).returncode == 0


def test_passes_issue_update_without_comment() -> None:
    res = _run("mcp__cua__issue_update", {"id": 3, "status": "done", "body": DIRTY, "title": "해당 스크립트 연동"})
    assert res.returncode == 0, res.stderr


def test_passes_other_tools_and_bad_input() -> None:
    assert _run("mcp__cua__doc_write", {"text": DIRTY}).returncode == 0
    res = subprocess.run([sys.executable, str(HOOK)], input="not json", capture_output=True, text=True, timeout=10)
    assert res.returncode == 0, res.stderr


@pytest.mark.parametrize("kind", sorted(lint_report.HINTS))
def test_every_kind_has_skill_example(kind: str) -> None:
    skill = polish_hook.SKILL.read_text(encoding="utf-8")
    assert polish_hook.example(kind, skill), f"no fix example in the skill for {kind}"


@pytest.mark.parametrize("tool", ["mcp__cua__mission_finish", "mcp__cua__issue_update", "mcp__cua__message_post"])
def test_settings_registers_hook(tool: str) -> None:
    cfg = json.loads((ROOT / ".claude/settings.json").read_text(encoding="utf-8"))
    entries = [e for e in cfg["hooks"]["PreToolUse"] if re.fullmatch(e["matcher"], tool)]
    assert entries, f"no PreToolUse entry matches {tool}"
    assert any("scripts/polish_hook.py" in h["command"] for e in entries for h in e["hooks"])
    assert not re.fullmatch(entries[0]["matcher"], "mcp__cua__doc_write")
