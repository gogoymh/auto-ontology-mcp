"""scripts/polish_blocks.py must count the polish hook's blocks per pattern, most frequent first."""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts/polish_blocks.py"
HOOK = ROOT / "scripts/polish_hook.py"
sys.path.insert(0, str(ROOT / "scripts"))
import polish_blocks  # noqa: E402

ROWS = [
    {"ts": "t1", "tool": "mission_finish", "field": "summary", "pattern": "translationese"},
    {"ts": "t1", "tool": "mission_finish", "field": "summary", "pattern": "translationese"},
    {"ts": "t1", "tool": "mission_finish", "field": "next", "pattern": "nominal"},
    {"ts": "t2", "tool": "message_post", "field": "text", "pattern": "nounstack"},
    {"ts": "t3", "tool": "issue_create", "field": "title", "pattern": "translationese"},
    {"ts": "t4", "tool": "message_post", "field": "text", "pattern": "nounstack"},
    {"ts": "t5", "tool": "doc_write", "field": "text", "pattern": "empty"},
]


def _write(path: Path, rows: list, extra: str = "") -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows) + extra, encoding="utf-8")
    return path


def _cli(*args: str, env: dict = None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, timeout=10, env=env)


def test_count_orders_by_findings_then_blocks() -> None:
    assert polish_blocks.count(ROWS) == [("translationese", 2, 3), ("nounstack", 2, 2), ("empty", 1, 1), ("nominal", 1, 1)]
    assert polish_blocks.count(ROWS, "tool")[0] == ("mission_finish", 1, 3)
    assert ("text", 3, 3) in polish_blocks.count(ROWS, "field")


def test_read_skips_broken_lines(tmp_path: Path) -> None:
    path = _write(tmp_path / "log.jsonl", ROWS[:2], 'not json\n{"tool": "x"}\n[1]\n\n')
    assert len(polish_blocks.read(path)) == 2
    assert polish_blocks.read(tmp_path / "none.jsonl") == []


def test_cli_prints_patterns_most_frequent_first(tmp_path: Path) -> None:
    res = _cli("--log", str(_write(tmp_path / "log.jsonl", ROWS)))
    assert res.returncode == 0, res.stderr
    lines = res.stdout.splitlines()
    assert lines[0].split() == ["pattern", "blocks", "findings"]
    assert [ln.split() for ln in lines[1:]] == [["translationese", "2", "3"], ["nounstack", "2", "2"], ["empty", "1", "1"], ["nominal", "1", "1"]]


def test_cli_by_tool_and_env_log(tmp_path: Path) -> None:
    path = _write(tmp_path / "log.jsonl", ROWS)
    res = _cli("--by", "tool", env={**os.environ, "POLISH_BLOCK_LOG": str(path)})
    assert res.returncode == 0 and res.stdout.splitlines()[1].split() == ["mission_finish", "1", "3"], res.stdout


def test_cli_without_records_exits_zero(tmp_path: Path) -> None:
    res = _cli("--log", str(tmp_path / "none.jsonl"))
    assert res.returncode == 0 and "no blocks recorded" in res.stdout


def test_hook_blocks_show_up_in_counts(tmp_path: Path) -> None:
    env = {**os.environ, "POLISH_BLOCK_LOG": str(tmp_path / "log.jsonl")}
    for text in ("검토 시 기준선을 봅니다.", "향후 배포 시 기준선을 봅니다.", "3시에 배포합니다."):
        payload = json.dumps({"tool_name": "mcp__cua__message_post", "tool_input": {"text": text}}, ensure_ascii=False)
        subprocess.run([sys.executable, str(HOOK)], input=payload, capture_output=True, text=True, timeout=10, env=env)
    res = _cli(env=env)
    assert res.stdout.splitlines()[1].split() == ["nounstack", "2", "3"], res.stdout
