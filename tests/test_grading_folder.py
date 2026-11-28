"""scripts/check_grading_folder.py filed/judge must catch grading notes left at the top or missing from the '채점' list (AOM-46)."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts/check_grading_folder.py"
INDEX = "# 채점\n\n- [[AOM-28 채점]]: AOM-28\n"


def _docs(tmp: Path, parents: dict, index: str) -> Path:
    (tmp / "채점.md").write_text(index, encoding="utf-8")
    for name in ("AOM-28 채점", "AOM-46 채점"):
        (tmp / f"{name}.md").write_text(f"# {name}\n", encoding="utf-8")
    (tmp / ".tree.json").write_text(json.dumps({"parents": parents}, ensure_ascii=False), encoding="utf-8")
    return tmp


def _run(cmd: str, docs: Path = None, bots: Path = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if docs:
        env["PQ_DOCS"] = str(docs)
    if bots:
        env["PQ_BOTS"] = str(bots)
    return subprocess.run([sys.executable, str(SCRIPT), cmd], capture_output=True, text=True, env=env, timeout=10)


@pytest.mark.parametrize(
    "parents,index,why",
    [
        ({"AOM-28 채점": "채점"}, INDEX + "- [[AOM-46 채점]]: AOM-46\n", "not under"),
        ({"AOM-28 채점": "채점", "AOM-46 채점": "채점"}, INDEX, "does not list"),
        ({"AOM-28 채점": "채점", "AOM-46 채점": "채점"}, INDEX + "- [[AOM-46 채점]]\n", "names no ticket"),
    ],
    ids=["top-level", "unlisted", "no-ticket"],
)
def test_filed_detects_misfiled_note(tmp_path, parents, index, why) -> None:
    res = _run("filed", docs=_docs(tmp_path, parents, index))
    assert res.returncode == 1 and why in res.stdout, res.stdout


def test_judge_detects_role_without_folder(tmp_path) -> None:
    (tmp_path / "judge.json").write_text(json.dumps({"role": "미션을 0~100점으로 채점합니다."}), encoding="utf-8")
    res = _run("judge", bots=tmp_path)
    assert res.returncode == 1 and "lacks" in res.stdout, res.stdout


def test_filed_accepts_new_note_under_folder(tmp_path) -> None:
    parents = {"AOM-28 채점": "채점", "AOM-46 채점": "채점"}
    res = _run("filed", docs=_docs(tmp_path, parents, INDEX + "- [[AOM-46 채점]]: AOM-46, AOM-45\n"))
    assert res.returncode == 0 and "2 grading notes" in res.stdout, res.stdout


def test_judge_accepts_role_with_folder(tmp_path) -> None:
    role = "채점 기록은 doc_write에 parent '채점'을 넘겨 쓰고, '채점' 목록에 새 문서와 티켓 줄을 더합니다."
    (tmp_path / "judge.json").write_text(json.dumps({"role": role}, ensure_ascii=False), encoding="utf-8")
    res = _run("judge", bots=tmp_path)
    assert res.returncode == 0, res.stdout
