"""scripts/polish_hook.py must block agent text people read (reports, comments, messages, issue titles and bodies, docs) with polish patterns."""
import json
import os
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


@pytest.fixture(autouse=True)
def block_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every test records blocks in its own file, never in the repository's shared log."""
    path = tmp_path / "polish_blocks.jsonl"
    monkeypatch.setenv("POLISH_BLOCK_LOG", str(path))
    return path


def _run(tool: str, args: dict) -> subprocess.CompletedProcess:
    payload = json.dumps({"tool_name": tool, "tool_input": args}, ensure_ascii=False)
    return subprocess.run([sys.executable, str(HOOK)], input=payload, capture_output=True, text=True, timeout=10, env=os.environ.copy())


def _records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def test_block_records_one_line_per_finding(block_file: Path) -> None:
    res = _run("mcp__cua__issue_update", {"id": 3, "status": "done", "body": DIRTY, "title": "해당 스크립트 연동"})
    assert res.returncode == 2, res.stderr
    rows = _records(block_file)
    assert {(r["tool"], r["field"]) for r in rows} == {("issue_update", "title"), ("issue_update", "body")}
    pairs = {(r["field"], r["pattern"]) for r in rows}
    assert {("body", "translationese"), ("body", "middot"), ("title", "translationese"), ("title", "nominal")} <= pairs
    assert len({r["ts"] for r in rows}) == 1 and all(set(r) == {"ts", "tool", "field", "pattern"} for r in rows)
    assert "recorded" in res.stderr


def test_block_records_append_and_short_tool_name(block_file: Path) -> None:
    assert _run("mcp__preconquer__doc_comment", {"name": "정리", "id": "c1", "text": "반영을 마쳤습니다."}).returncode == 2
    assert _run("mcp__cua__message_post", {"text": "검토 시 기준선을 봅니다."}).returncode == 2
    rows = _records(block_file)
    assert [(r["tool"], r["field"]) for r in rows][-1] == ("message_post", "text")
    assert ("doc_comment", "text", "empty") in {(r["tool"], r["field"], r["pattern"]) for r in rows}
    assert rows[-1]["pattern"] == "nounstack" and len({r["ts"] for r in rows}) == 2


def test_pass_records_nothing(block_file: Path) -> None:
    assert _run(TOOL, {"id": 1, "summary": CLEAN, "next": CLEAN}).returncode == 0
    assert _run("mcp__cua__issue_get", {"text": DIRTY}).returncode == 0
    assert not block_file.exists()


def test_unwritable_log_still_blocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLISH_BLOCK_LOG", str(tmp_path / "missing-dir" / "polish_blocks.jsonl"))
    res = _run(TOOL, {"id": 1, "summary": DIRTY})
    assert res.returncode == 2 and "translationese: " in res.stderr, res.stderr
    assert "could not record blocks" in res.stderr


def test_default_log_is_shared_by_worktrees(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POLISH_BLOCK_LOG")
    repo, tree = tmp_path / "repo", tmp_path / "tree"
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"]
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run([*git, "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "init"], check=True)
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", str(tree)], check=True)
    main, other = polish_hook.block_log(repo), polish_hook.block_log(tree)
    assert main == other == (repo / ".git" / "polish_blocks.jsonl").resolve()
    polish_hook.record("message_post", {"text": [("nounstack", "검토 시")]}, other)
    assert _records(main)[0]["pattern"] == "nounstack"


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


def test_passes_issue_update_without_text_fields() -> None:
    assert _run("mcp__cua__issue_update", {"id": 3, "status": "done"}).returncode == 0


def test_blocks_issue_update_title_and_body() -> None:
    res = _run("mcp__cua__issue_update", {"id": 3, "status": "done", "body": DIRTY, "title": "해당 스크립트 연동"})
    assert res.returncode == 2, res.stderr
    assert "[title]" in res.stderr and "[body]" in res.stderr


CUT_TITLE = "작업: .github/workflows/ci.yml에 benchmarks/run_benchmark.py --comp…"
GOOD_TITLE = "이슈 제목과 본문, 문서에도 윤문 점검 훅 걸기"


def test_blocks_issue_create_cut_path_title_with_skill_example() -> None:
    res = _run("mcp__cua__issue_create", {"title": CUT_TITLE, "body": CLEAN})
    assert res.returncode == 2, res.stderr
    assert "이슈 제목과 본문" in res.stderr and "issue_create" in res.stderr
    assert "[title]" in res.stderr and "[body]" not in res.stderr
    for excerpt in ("작업:", ".github/workflows/ci.yml", "--comp", "…"):
        assert f"title: '{excerpt}" in res.stderr, res.stderr
    assert "> CI에서 벤치마크를 기준선과 비교하기" in res.stderr


@pytest.mark.parametrize(
    "title",
    [
        "scripts/polish_hook.py 고치기",
        "벤치마크에 --semantic 옵션 더하기",
        "할 일: 벤치마크 기준선 다시 재기",
        "벤치마크 기준선을 다시 재고 결과를 채널에 올리고 README 표까지 한꺼번에 고쳐서 바로 공유하기",
        "CI에서 벤치마크를 비교하기...",
    ],
)
def test_blocks_each_title_pattern(title: str) -> None:
    res = _run("mcp__cua__issue_create", {"title": title})
    assert res.returncode == 2 and "title: " in res.stderr, res.stderr


def test_blocks_issue_create_body() -> None:
    res = _run("mcp__cua__issue_create", {"title": GOOD_TITLE, "body": DIRTY})
    assert res.returncode == 2, res.stderr
    assert "[body]" in res.stderr and "[title]" not in res.stderr and "translationese: " in res.stderr


def test_passes_clean_issue_create() -> None:
    for title in (GOOD_TITLE, "CI에서 벤치마크를 기준선과 비교하기", "'--semantic' 결과를 README에 싣기"):
        res = _run("mcp__cua__issue_create", {"title": title, "body": CLEAN})
        assert res.returncode == 0, res.stderr


def test_blocks_doc_write_text() -> None:
    res = _run("mcp__cua__doc_write", {"name": "정리", "text": DIRTY, "keep": True})
    assert res.returncode == 2, res.stderr
    assert "문서" in res.stderr and "doc_write" in res.stderr and "[text]" in res.stderr
    assert "[name]" not in res.stderr


def test_passes_clean_doc_write() -> None:
    text = "# 정리\n\n" + CLEAN + "\n\n```\npython3 benchmarks/run_benchmark.py --compare\n```\n"
    assert _run("mcp__cua__doc_write", {"name": "정리", "text": text}).returncode == 0


def test_blocks_doc_comment_text() -> None:
    for tool in ("mcp__cua__doc_comment", "mcp__preconquer__doc_comment"):
        res = _run(tool, {"name": "doc:3f9a2c", "id": "c1", "text": DIRTY})
        assert res.returncode == 2, res.stderr
        assert "문서 댓글" in res.stderr and "doc_comment" in res.stderr and "[text]" in res.stderr
        assert "translationese: " in res.stderr and "고친 뒤:" in res.stderr


def test_blocks_doc_comment_bare_done() -> None:
    res = _run("mcp__cua__doc_comment", {"name": "정리", "id": "c1", "text": "반영을 마쳤습니다."})
    assert res.returncode == 2 and "empty: " in res.stderr, res.stderr


def test_passes_doc_comment_clean_resolve_and_quote() -> None:
    assert _run("mcp__cua__doc_comment", {"name": "정리", "id": "c1", "text": CLEAN}).returncode == 0
    assert _run("mcp__preconquer__doc_comment", {"name": "정리", "id": "c1", "resolve": True}).returncode == 0
    res = _run("mcp__cua__doc_comment", {"name": "정리", "quote": DIRTY, "text": "이 문장은 숫자를 붙여 다시 씁니다."})
    assert res.returncode == 0, res.stderr


def test_passes_other_tools_and_bad_input() -> None:
    assert _run("mcp__cua__issue_get", {"text": DIRTY}).returncode == 0
    res = subprocess.run([sys.executable, str(HOOK)], input="not json", capture_output=True, text=True, timeout=10)
    assert res.returncode == 0, res.stderr


@pytest.mark.parametrize("kind", sorted(lint_report.HINTS))
def test_every_kind_has_skill_example(kind: str) -> None:
    skill = polish_hook.SKILL.read_text(encoding="utf-8")
    assert polish_hook.example(kind, skill), f"no fix example in the skill for {kind}"


@pytest.mark.parametrize(
    "tool",
    ["mcp__cua__mission_finish", "mcp__cua__issue_update", "mcp__cua__message_post", "mcp__cua__issue_create", "mcp__cua__doc_write", "mcp__cua__doc_comment",
     "mcp__preconquer__doc_comment", "mcp__preconquer__mission_finish"],
)
def test_settings_registers_hook(tool: str) -> None:
    cfg = json.loads((ROOT / ".claude/settings.json").read_text(encoding="utf-8"))
    entries = [e for e in cfg["hooks"]["PreToolUse"] if re.fullmatch(e["matcher"], tool)]
    assert entries, f"no PreToolUse entry matches {tool}"
    assert any("scripts/polish_hook.py" in h["command"] for e in entries for h in e["hooks"])
    assert not re.fullmatch(entries[0]["matcher"], "mcp__cua__issue_get")
    assert not re.fullmatch(entries[0]["matcher"], "mcp__preconquer__doc_read")
