"""End-to-end semantic benchmark with the real local model (needs the `semantic` extra)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from auto_ontology_mcp.config import Settings
from auto_ontology_mcp.semantic import FastEmbedder, resolve_model_name, semantic_dependencies_available

from strict_skip import REQUIRE_SEMANTIC_ENV, skip_unless_required

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "benchmarks" / "run_benchmark.py"


def require_semantic_extra() -> None:
    if not semantic_dependencies_available():
        skip_unless_required("semantic extra not installed", REQUIRE_SEMANTIC_ENV)


def _load_runner():
    spec = importlib.util.spec_from_file_location("run_benchmark_semantic_model", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _share_session_model(semantic_model, monkeypatch):
    import fastembed

    monkeypatch.setattr(
        fastembed.TextEmbedding,
        "__init__",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("TextEmbedding reloaded instead of reusing session model")),
    )
    return semantic_model


def test_cli_semantic_flag_measures_recall_gain_and_latency(tmp_path: Path, capsys, semantic_model):
    out_file = tmp_path / "semantic.json"
    runner = _load_runner()
    rc = runner.main(
        ["--semantic", "--iterations", "1", "--warmup", "1", "--repeats", "1", "--output", str(out_file), "--format", "json"]
    )
    captured = capsys.readouterr()
    assert rc == 0, captured.err

    cfg = Settings.from_env()
    assert FastEmbedder(resolve_model_name(cfg.embedding_model), cache_dir=cfg.embedding_cache_dir)._model() is semantic_model

    sem = json.loads(out_file.read_text(encoding="utf-8"))["semantic_search"]
    lex, hyb = sem["modes"]["lexical"], sem["modes"]["hybrid"]
    assert hyb["recall_at_3"] > lex["recall_at_3"]
    assert hyb["recall_by_group"]["cross_lingual"] >= 0.5 > lex["recall_by_group"]["cross_lingual"]
    assert hyb["negative_precision"] == 1.0 and not sem["delta"]["lost"]
    assert hyb["latency_ms"]["p50"] > 0 and lex["latency_ms"]["p50"] > 0 and sem["delta"]["p50_ratio"] > 0


def test_default_model_saves_missed_korean_compound_from_the_translated_search(modeled, semantic_model):
    # MiniLM puts "주문 상태" at 0.31 from "Order Status": the hybrid search misses it, the
    # translated search offers the update_term call, and after it the phrase finds the column.
    assert modeled.vectors.min_similarity == 0.50
    miss = modeled.search_ontology("주문 상태")
    assert modeled.vectors._get_embedder()._model() is semantic_model
    assert miss["search_mode"] == "hybrid"
    assert "Order › Order Status" not in [h["name"] for h in miss["hits"][:3]]
    learn = modeled.search_ontology("order status")["learn"]
    assert learn["phrase"] == "주문 상태"
    call = next(c["update_term"] for c in learn["candidates"] if c["name"] == "Order › Order Status")
    modeled.update_term(**call)
    assert modeled.search_ontology("주문 상태")["hits"][0]["name"] == "Order › Order Status"
    # An unrelated miss is not offered as a synonym of the next hit.
    modeled.search_ontology("astrophysics telescope")
    assert "learn" not in modeled.search_ontology("order date")
