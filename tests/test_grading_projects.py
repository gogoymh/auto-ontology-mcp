"""check_grading_folder.py reads every project's values from grading_projects.json (AOM-64)."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
SCRIPT = SCRIPTS / "check_grading_folder.py"
CONFIG = SCRIPTS / "grading_projects.json"
NOTES = {"NP-1 채점": ["NP-1"], "NP-2와 NP-3 채점": ["NP-2", "NP-3"]}
INDEX = "# 채점\n\n- [[NP-1 채점]]: NP-1\n- [[NP-2와 NP-3 채점]]: NP-2, NP-3\n"
TREE = {"parents": {"NP-1 채점": "채점", "NP-2와 NP-3 채점": "채점"}}


def test_one_copy_of_check_code() -> None:
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    assert {"auto-ontology-mcp", "d-means", "git-ontology-agent"} <= set(cfg)
    for name in ("check_grading_folder.py", "check_dmeans_grading.py", "check_goa_grading.py"):
        src = (SCRIPTS / name).read_text(encoding="utf-8")
        assert not re.search(r"[0-9a-f]{64}", src), f"{name} still holds a hash"
        assert "/hub/" not in src and "채점 결과" not in src, f"{name} still holds a hub path or note name"
    for name in ("check_dmeans_grading.py", "check_goa_grading.py"):
        src = (SCRIPTS / name).read_text(encoding="utf-8")
        assert "def " not in src and len(src.splitlines()) < 25, f"{name} is not a thin wrapper"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def hub(tmp_path: Path):
    docs = tmp_path / "hub" / "docs"
    docs.mkdir(parents=True)
    for name in NOTES:
        (docs / f"{name}.md").write_text(f"# {name}\n본문\n", encoding="utf-8")
    (docs / ".tree.json").write_text(json.dumps({"parents": {}}, ensure_ascii=False), encoding="utf-8")
    backup = tmp_path / "hub" / "docs-backup"
    shutil.copytree(docs, backup)
    (docs / "채점.md").write_text(INDEX, encoding="utf-8")
    (docs / ".tree.json").write_text(json.dumps(TREE, ensure_ascii=False), encoding="utf-8")
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    cfg["new-project"] = {
        "hub": str(tmp_path / "hub"),
        "backup": "docs-backup",
        "notes": NOTES,
        "before": {n: _sha(backup / f"{n}.md") for n in NOTES},
        "tree_before": _sha(backup / ".tree.json"),
    }
    conf = tmp_path / "grading_projects.json"
    conf.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    return tmp_path / "hub", conf


def _run(cmd: str, conf: Path) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in ("PQ_HUB", "PQ_DOCS")}
    env["PQ_GRADING_CONFIG"] = str(conf)
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--project", "new-project", cmd],
        capture_output=True, text=True, env=env, timeout=10,
    )


@pytest.mark.parametrize("cmd", ["nested", "backup", "intact", "index", "count", "filed"])
def test_new_project_passes_from_config_alone(hub, cmd) -> None:
    res = _run(cmd, hub[1])
    assert res.returncode == 0 and f"{cmd} ok" in res.stdout, res.stdout


def _top_level(h: Path) -> None:
    (h / "docs" / ".tree.json").write_text(json.dumps({"parents": {"NP-1 채점": "채점"}}, ensure_ascii=False))


def _edit(h: Path) -> None:
    (h / "docs" / "NP-1 채점.md").write_text("# NP-1 채점\n바뀐 본문\n", encoding="utf-8")


def _drop_backup(h: Path) -> None:
    (h / "docs-backup" / "NP-1 채점.md").unlink()


def _unlink(h: Path) -> None:
    (h / "docs" / "채점.md").write_text("# 채점\n\n- [[NP-1 채점]]: NP-1\n", encoding="utf-8")


def _wrong_ticket(h: Path) -> None:
    (h / "docs" / "채점.md").write_text(INDEX.replace("NP-2, NP-3", "NP-2"), encoding="utf-8")


@pytest.mark.parametrize(
    "cmd,breaks,why",
    [
        ("nested", _top_level, "not under"),
        ("intact", _edit, "changed"),
        ("backup", _drop_backup, "missing or different"),
        ("index", _unlink, "does not link"),
        ("index", _wrong_ticket, "lists"),
    ],
    ids=["top-level", "edited", "backup-missing", "unlinked", "wrong-ticket"],
)
def test_new_project_fails_on_misfiled_docs(hub, cmd, breaks, why) -> None:
    breaks(hub[0])
    res = _run(cmd, hub[1])
    assert res.returncode == 1 and why in res.stdout, res.stdout


def test_new_project_unknown_name_fails(hub) -> None:
    env = dict(os.environ, PQ_GRADING_CONFIG=str(hub[1]))
    res = subprocess.run([sys.executable, str(SCRIPT), "--project", "nope", "nested"],
                         capture_output=True, text=True, env=env, timeout=10)
    assert res.returncode == 1 and "unknown project" in res.stdout, res.stdout
