"""scripts/upstream_scout.py against a local git repository standing in for NVIDIA Auto Ontology."""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("upstream_scout", ROOT / "scripts" / "upstream_scout.py")
scout = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scout)


def _git(cwd: Path, *args: str) -> str:
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"}
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout.strip()


def _commit(repo: Path, path: str, msg: str) -> str:
    f = repo / path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(f.read_text() + msg + "\n" if f.exists() else msg + "\n")
    _git(repo, "add", path)
    _git(repo, "commit", "-q", "-m", msg)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def upstream(tmp_path, monkeypatch):
    repo = tmp_path / "auto-ontology"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    base = _commit(repo, "README.md", "initial")
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"remote": str(repo), "reviewed": base}))
    monkeypatch.setenv("UPSTREAM_SCOUT_CACHE", str(tmp_path / "cache"))
    return repo, state, base


def test_up_to_date_gate(upstream, tmp_path):
    repo, state, _ = upstream
    assert scout.main(["--state", str(state), "--up-to-date"]) == 0
    _commit(repo, "frontend/app.tsx", "new page")
    assert scout.main(["--state", str(state), "--up-to-date"]) == 1
    # Offline: report up to date so the scheduled run starts no agent for nothing.
    assert scout.main(["--state", str(state), "--remote", str(tmp_path / "missing"), "--up-to-date"]) == 0


def test_digest_ranks_ported_then_related_then_other(upstream, capsys):
    repo, state, _ = upstream
    _commit(repo, "frontend/app.tsx", "Dark mode chart labels")
    _commit(repo, "auto_ontology/connectors/bigquery.py", "BigQuery connector")
    # A feature branch merged as one PR: counted once, with the files of the whole PR.
    _git(repo, "checkout", "-q", "-b", "feature")
    _commit(repo, "auto_ontology/semantic/tests/test_visit_enter.py", "Probe test")
    _commit(repo, "auto_ontology/semantic/visit_enter.py", "Key probe on empty column")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "feature", "-m", "Merge pull request #599 key probe")

    assert scout.main(["--state", str(state), "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert [(i["subject"], i["level"]) for i in d["items"]] == [
        ("Merge pull request #599 key probe", "ported"),
        ("BigQuery connector", "related"),
        ("Dark mode chart labels", "other"),
    ]
    assert d["items"][0]["local"] == ["profiling.py"]
    assert d["items"][1]["local"] == ["datasources.py"]
    assert d["counts"] == {"ported": 1, "related": 1, "other": 1}

    assert scout.main(["--state", str(state)]) == 0
    text = capsys.readouterr().out
    assert "3 new PRs (1 ported, 1 related, 1 other)" in text
    assert text.index("key probe") < text.index("BigQuery") < text.index("Dark mode")
    assert "-> profiling.py" in text


def test_mark_reviewed_moves_state_forward(upstream, capsys):
    repo, state, base = upstream
    head = _commit(repo, "auto_ontology/semantic/term_extractor.py", "Term prompt tweak")
    assert scout.main(["--state", str(state), "--up-to-date"]) == 1
    assert scout.main(["--state", str(state), "--mark-reviewed"]) == 0
    saved = json.loads(state.read_text())
    assert saved["reviewed"] == head != base
    assert saved["reviewed_at"]
    assert scout.main(["--state", str(state), "--up-to-date"]) == 0
    capsys.readouterr()
    assert scout.main(["--state", str(state)]) == 0
    assert "Nothing new upstream." in capsys.readouterr().out


def test_classify_follows_notice():
    assert scout.classify("auto_ontology/semantic/date_format.py") == ("ported", ["profiling.py"])
    assert scout.classify("auto_ontology/semantic/tests/test_fk_suggester.py")[0] == "ported"
    assert scout.classify("skills/nvidia-ontology-query/references/query-validation.md")[0] == "ported"
    assert scout.classify("auto_ontology/semantic/compile.py")[0] == "related"
    assert scout.classify("helm/values.yaml") == ("other", [])
    state = json.loads((ROOT / "scripts" / "upstream_state.json").read_text())
    assert state["remote"] == "https://github.com/NVIDIA/auto-ontology"
    assert len(state["reviewed"]) == 40
