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


def test_state_records_synced_commit_per_ported_path():
    state = json.loads((ROOT / "scripts" / "upstream_state.json").read_text())
    ported = state["ported"]
    assert scout.PORTED == [(p, e["local"]) for p, e in ported.items()]
    assert {"guidance.py", "profiling.py"} <= set(scout.local_files(state))
    for path, entry in ported.items():
        assert len(entry["synced"]) == 40 and int(entry["synced"], 16) >= 0, path
        assert entry["local"] and all(f.endswith(".py") for f in entry["local"]), path
        for f in entry["local"]:
            assert (ROOT / "src" / "auto_ontology_mcp" / f).exists(), f


@pytest.fixture
def ported(upstream):
    """Upstream with one adapted file, synced at its second commit."""
    repo, state, _ = upstream
    _commit(repo, "auto_ontology/semantic/term_extractor.py", "old rule already ported")
    synced = _commit(repo, "auto_ontology/semantic/visit_enter.py", "probe already ported")
    s = json.loads(state.read_text())
    s["ported"] = {
        "auto_ontology/semantic/term_extractor.py": {"local": ["guidance.py"], "synced": synced},
        "auto_ontology/semantic/visit_enter.py": {"local": ["profiling.py"], "synced": synced},
    }
    state.write_text(json.dumps(s))
    return repo, state, synced


def test_diff_shows_only_changes_after_synced(ported, capsys):
    repo, state, synced = ported
    assert scout.main(["--state", str(state), "--diff", "src/auto_ontology_mcp/guidance.py"]) == 0
    out = capsys.readouterr().out
    assert "0 new commits" in out and "Up to date." in out and "old rule" not in out

    _commit(repo, "auto_ontology/semantic/term_extractor.py", "new naming rule")
    _commit(repo, "auto_ontology/semantic/visit_enter.py", "profiling change")
    assert scout.main(["--state", str(state), "--diff", "guidance.py"]) == 0
    out = capsys.readouterr().out
    assert "## auto_ontology/semantic/term_extractor.py" in out
    assert f"Synced {synced[:12]}" in out and "1 new commit." in out
    assert "+new naming rule" in out and "new naming rule" in out.split("```diff")[0]
    assert "+old rule" not in out
    assert "visit_enter" not in out and "profiling change" not in out


def test_diff_rejects_unported_file(ported, caplog):
    _, state, _ = ported
    assert scout.main(["--state", str(state), "--diff", "server.py"]) == 2
    assert "server.py has no ported upstream code" in caplog.text
    assert "guidance.py, profiling.py" in caplog.text


def test_mark_synced_moves_only_that_file(ported, capsys):
    repo, state, synced = ported
    _commit(repo, "auto_ontology/semantic/term_extractor.py", "new naming rule")
    head = _commit(repo, "auto_ontology/semantic/visit_enter.py", "profiling change")
    assert scout.main(["--state", str(state), "--mark-synced", "guidance.py"]) == 0
    saved = json.loads(state.read_text())["ported"]
    assert saved["auto_ontology/semantic/term_extractor.py"]["synced"] == head
    assert saved["auto_ontology/semantic/visit_enter.py"]["synced"] == synced
    capsys.readouterr()
    assert scout.main(["--state", str(state), "--diff", "guidance.py"]) == 0
    assert "Up to date." in capsys.readouterr().out
    assert scout.main(["--state", str(state), "--diff", "profiling.py"]) == 0
    assert "+profiling change" in capsys.readouterr().out


def _load_copy(tmp_path: Path):
    """A copy of the script outside the project, as a routine on a main without it makes one."""
    copy = tmp_path / "tmp" / "upstream_scout.py"
    copy.parent.mkdir()
    copy.write_text((ROOT / "scripts" / "upstream_scout.py").read_text())
    spec = importlib.util.spec_from_file_location("upstream_scout_copy", copy)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_temporary_copy_reads_the_working_folders_state(upstream, tmp_path, monkeypatch, capsys):
    repo, state, base = upstream
    work = tmp_path / "work"
    (work / "scripts").mkdir(parents=True)
    local = work / "scripts" / "upstream_state.json"
    local.write_text(state.read_text())
    monkeypatch.chdir(work)
    copy = _load_copy(tmp_path)
    assert copy.STATE_FILE == local
    head = _commit(repo, "frontend/app.tsx", "New page")
    assert copy.main([]) == 0
    assert "New page" in capsys.readouterr().out
    assert copy.main(["--mark-reviewed"]) == 0
    assert json.loads(local.read_text())["reviewed"] == head != base


def test_temporary_copy_without_state_exits_2(tmp_path, monkeypatch, caplog):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)
    copy = _load_copy(tmp_path)
    assert copy.PORTED == []
    assert copy.main([]) == 2
    assert "no state file" in caplog.text
