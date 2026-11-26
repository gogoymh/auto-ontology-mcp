"""scripts/polish_hook.py must block mission_finish reports with polish patterns and show the skill's fix."""
import json
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


def test_passes_other_tools_and_bad_input() -> None:
    assert _run("mcp__cua__message_post", {"text": DIRTY}).returncode == 0
    res = subprocess.run([sys.executable, str(HOOK)], input="not json", capture_output=True, text=True, timeout=10)
    assert res.returncode == 0, res.stderr


@pytest.mark.parametrize("kind", sorted(lint_report.HINTS))
def test_every_kind_has_skill_example(kind: str) -> None:
    skill = polish_hook.SKILL.read_text(encoding="utf-8")
    assert polish_hook.example(kind, skill), f"no fix example in the skill for {kind}"


def test_settings_registers_hook() -> None:
    cfg = json.loads((ROOT / ".claude/settings.json").read_text(encoding="utf-8"))
    entries = [e for e in cfg["hooks"]["PreToolUse"] if e["matcher"] == TOOL]
    assert entries, "no PreToolUse entry for mission_finish"
    assert any("scripts/polish_hook.py" in h["command"] for e in entries for h in e["hooks"])
