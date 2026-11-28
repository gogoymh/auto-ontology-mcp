"""The lint's false-positive corpus holds grading documents, issue bodies and criteria (AOM-58) besides channel posts."""
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROWS = [
    json.loads(line)
    for line in (ROOT / "tests/data/lint_reports.jsonl").read_text(encoding="utf-8").splitlines()
    if line.strip()
]
sys.path.insert(0, str(ROOT / "scripts"))
from import_lint_corpus import collect, issue_body  # noqa: E402


def test_corpus_counts() -> None:
    sources = Counter(r.get("source", "post") for r in ROWS)
    assert sources == {"post": 121, "doc": 4, "issue": 71, "criterion": 251}, sources
    assert len({r["id"] for r in ROWS}) == len(ROWS) == 447
    for r in ROWS:
        assert r["text"].strip(), r["id"]
    assert all("You are @" not in r["text"] for r in ROWS if r.get("source") == "issue")


def test_corpus_counts_issue_body_strips_criteria_and_wrapper() -> None:
    body = (
        "할 일을 적습니다.\n\n(Nobody asked: @polish decided on its own to take this on, because: 제 몫입니다. "
        "You are @polish: 역할 설명. Preconquer posts the outcome to the channel general when the mission ends.)"
        "\n\n1. 첫 기준\n2. 둘째 기준"
    )
    crit = [{"id": 1, "text": "첫 기준"}, {"id": 2, "text": "둘째 기준"}]
    assert issue_body(body, crit) == "할 일을 적습니다.\n\n제 몫입니다."


def test_corpus_counts_import_adds_once(tmp_path: Path) -> None:
    hub = tmp_path / "hub"
    for d in ("docs", "issues", "missions"):
        (hub / d).mkdir(parents=True)
    (hub / "docs/AOM-9 채점.md").write_text("# AOM-9 채점\n\n90점입니다.", encoding="utf-8")
    (hub / "docs/채점.md").write_text("목록", encoding="utf-8")
    (hub / "issues/3.json").write_text(json.dumps({"id": 3, "mission": 7, "body": "본문입니다.\n\n1. 기준입니다."}), encoding="utf-8")
    (hub / "missions/7.json").write_text(json.dumps({"id": 7, "criteria": [{"id": 1, "text": "기준입니다."}]}), encoding="utf-8")
    assert [r["id"] for r in collect(hub, "AOM")] == ["doc-AOM-9", "AOM-3-body", "AOM-3-k1"]
    corpus = tmp_path / "c.jsonl"
    corpus.write_text('{"id": "general-1", "text": "글"}\n', encoding="utf-8")
    cmd = [sys.executable, str(ROOT / "scripts/import_lint_corpus.py"), str(hub), "--corpus", str(corpus)]
    first = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    second = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    assert "added=3 " in first.stdout and "added=0 " in second.stdout, (first.stdout, second.stdout)
    assert len(corpus.read_text(encoding="utf-8").splitlines()) == 4
