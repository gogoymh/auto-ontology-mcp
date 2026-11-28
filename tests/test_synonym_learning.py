"""Saving the user's own words as Term and column synonyms, and per-model similarity floors.

These tests run without the embedding model: lexical mode, or a tiny deterministic embedder.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Sequence

import pytest

from auto_ontology_mcp.config import Settings
from auto_ontology_mcp.semantic import (
    DEFAULT_EMBEDDING_MODEL,
    LARGER_EMBEDDING_MODEL,
    VectorIndex,
    model_min_similarity,
    normalize_vector,
    resolve_model_name,
)
from auto_ontology_mcp.service import OntologyService

from conftest import DEMO_TERMS


@pytest.fixture()
def lexical(settings: Settings, demo_path) -> OntologyService:
    svc = OntologyService(replace(settings, semantic_search=False))
    svc.add_datasource("demo", f"sqlite:///{demo_path}", description="demo")
    for table, terms in DEMO_TERMS.items():
        svc.save_table_terms(table, terms)
    return svc


def _names(out: dict) -> list[str]:
    return [h["name"] for h in out["hits"]]


# ------------------------------------------------------------ column synonyms


def test_column_synonym_makes_korean_compound_findable(lexical):
    assert "Order › Order Status" not in _names(lexical.search_ontology("주문 상태"))[:3]
    lexical.update_term("Order", column_updates=[{"column": "status", "add_synonyms": ["주문 상태", "주문 상태"]}])
    assert _names(lexical.search_ontology("주문 상태"))[0] == "Order › Order Status"
    attr = next(a for a in lexical.get_term("Order")["attributes"] if a["name"] == "Order Status")
    assert attr["synonyms"] == ["주문 상태"]


def test_column_synonyms_survive_remodel_and_export_import(lexical, settings, demo_path, tmp_path):
    lexical.update_term("Order", column_updates=[{"column": "status", "add_synonyms": ["주문 상태"]}])
    lexical.save_table_terms("orders", DEMO_TERMS["orders"])
    assert _names(lexical.search_ontology("주문 상태"))[0] == "Order › Order Status"

    exported = lexical.export_ontology()["document"]
    assert "주문 상태" in exported
    other = OntologyService(replace(settings, home=tmp_path / "b", store_path=tmp_path / "b" / "o.db", semantic_search=False))
    other.add_datasource("demo", f"sqlite:///{demo_path}")
    other.import_ontology(exported)
    assert _names(other.search_ontology("주문 상태"))[0] == "Order › Order Status"


# ------------------------------------------------------ suggestion on a miss


def test_missed_phrase_is_offered_as_synonym_on_the_next_hit(lexical):
    miss = lexical.search_ontology("주문 상태")
    assert miss["learn"]["phrase"] == "주문 상태" and "candidates" not in miss["learn"]

    found = lexical.search_ontology("order status")
    assert found["hits"][0]["name"] == "Order › Order Status"
    learn = found["learn"]
    assert learn["phrase"] == "주문 상태"
    first = learn["candidates"][0]
    assert first["name"] == "Order › Order Status"
    assert first["update_term"] == {"term": "Order", "column_updates": [{"column": "status", "add_synonyms": ["주문 상태"]}]}

    lexical.update_term(**first["update_term"])
    again = lexical.search_ontology("주문 상태")
    assert again["hits"][0]["name"] == "Order › Order Status" and "learn" not in again
    # The suggestion is made once.
    assert "learn" not in lexical.search_ontology("order status")


def test_no_suggestion_without_a_miss_or_for_another_datasource(lexical):
    assert "learn" not in lexical.search_ontology("order status")
    lexical.search_ontology("주문 상태")
    lexical.add_datasource("other", lexical.ds("demo")["url"])
    assert "learn" not in lexical.search_ontology("order status", datasource="other")


class PairEmbedder:
    """'주문 상태' and 'Order Status' share an axis; 'telescope' sits on its own."""

    model_name = "pair-test-v1"
    _AXES = (("주문 상태", "order status"), ("order",), ("telescope", "astrophysics"))

    def _encode(self, text: str) -> list[float]:
        low = text.casefold()
        vec = [1.0 if any(k in low for k in keys) else 0.0 for keys in self._AXES] + [0.01]
        return normalize_vector(vec)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._encode(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._encode(text)


def test_unrelated_miss_is_not_paired_when_embeddings_are_on(lexical, tmp_path):
    lexical.vectors = VectorIndex(tmp_path / "pair.sqlite", enabled=True, embedder=PairEmbedder(), use_sqlite_vec=False)
    lexical.search_ontology("astrophysics telescope")
    assert "learn" not in lexical.search_ontology("order status")

    lexical.search_ontology("주문 상태")
    learn = lexical.search_ontology("order status")["learn"]
    assert [c["name"] for c in learn["candidates"]] == ["Order › Order Status"]


# ------------------------------------------------------- per-model floors


def test_model_floor_profiles_aliases_and_override(monkeypatch, tmp_path):
    assert resolve_model_name("mpnet") == LARGER_EMBEDDING_MODEL
    assert resolve_model_name("MiniLM") == DEFAULT_EMBEDDING_MODEL
    assert resolve_model_name(None) == DEFAULT_EMBEDDING_MODEL
    assert resolve_model_name("BAAI/bge-small-en-v1.5") == "BAAI/bge-small-en-v1.5"
    assert model_min_similarity(DEFAULT_EMBEDDING_MODEL) == 0.50
    assert model_min_similarity(LARGER_EMBEDDING_MODEL) == 0.40
    assert model_min_similarity("unknown/model") == 0.50
    assert model_min_similarity(LARGER_EMBEDDING_MODEL, 0.3) == 0.3

    index = VectorIndex(tmp_path / "v.sqlite", enabled=True, model_name="mpnet")
    assert index.model_name == LARGER_EMBEDDING_MODEL and index.min_similarity == 0.40

    monkeypatch.setenv("AUTO_ONTOLOGY_MIN_SIMILARITY", "0.45")
    monkeypatch.setenv("AUTO_ONTOLOGY_EMBEDDING_MODEL", "mpnet")
    cfg = Settings.from_env()
    assert cfg.min_similarity == 0.45
    svc = OntologyService(replace(cfg, home=tmp_path, store_path=tmp_path / "o.db"))
    assert svc.vectors.model_name == LARGER_EMBEDDING_MODEL and svc.vectors.min_similarity == 0.45
