"""CI must fail, not skip, when release tags or the semantic extra are missing."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import test_benchmark_semantic_model as bench_semantic
import test_glama_quality as glama
import test_semantic_search as semantic_tests

ROOT = Path(__file__).resolve().parent.parent
Failed = pytest.fail.Exception
Skipped = pytest.skip.Exception


@pytest.fixture
def no_tags(monkeypatch):
    monkeypatch.setattr(glama, "_read_release_tags", lambda root: (None, "git checkout has no fetched tags"))


@pytest.fixture
def no_sqlite_vec(monkeypatch):
    monkeypatch.setattr(semantic_tests, "sqlite_vec_available", lambda: False)
    monkeypatch.setattr(bench_semantic, "semantic_dependencies_available", lambda: False)


def test_release_tags_fail_without_tags_on_ci(no_tags, monkeypatch):
    monkeypatch.setenv("CI", "true")
    with pytest.raises(Failed, match="no fetched tags"):
        glama.test_release_tags()


def test_release_tags_skip_without_tags_locally(no_tags, monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    with pytest.raises(Skipped):
        glama.test_release_tags()


def test_semantic_tests_fail_when_required(no_sqlite_vec, monkeypatch, tmp_path):
    monkeypatch.setenv("AO_REQUIRE_SEMANTIC", "1")
    with pytest.raises(Failed, match="sqlite-vec"):
        semantic_tests.test_vector_index_sqlite_vec_real_extension_when_installed(tmp_path)
    with pytest.raises(Failed, match="semantic extra"):
        bench_semantic.require_semantic_extra()


def test_semantic_tests_skip_when_not_required(no_sqlite_vec, monkeypatch, tmp_path):
    monkeypatch.delenv("AO_REQUIRE_SEMANTIC", raising=False)
    with pytest.raises(Skipped):
        semantic_tests.test_vector_index_sqlite_vec_real_extension_when_installed(tmp_path)
    with pytest.raises(Skipped):
        bench_semantic.require_semantic_extra()


def _ci_steps() -> list[dict]:
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    return workflow["jobs"]["test"]["steps"]


def test_ci_semantic_step_requires_semantic():
    step = next(s for s in _ci_steps() if "test_semantic_search.py" in s.get("run", ""))
    assert str(step["env"]["AO_REQUIRE_SEMANTIC"]) == "1"


def test_ci_model_cache_key_names_only_the_model():
    step = next(s for s in _ci_steps() if "actions/cache" in s.get("uses", ""))
    key = step["with"]["key"]
    assert "hashFiles" not in key and "uv.lock" not in key
    assert key.endswith("fastembed-paraphrase-multilingual-MiniLM-L12-v2")
    assert "restore-keys" not in step["with"]
