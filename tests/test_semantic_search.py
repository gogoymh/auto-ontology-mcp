"""Tests for optional local embedding (fastembed + sqlite-vec) hybrid search."""

from __future__ import annotations

import sqlite3
import tomllib
from dataclasses import replace
from pathlib import Path
from typing import Sequence

import pytest

from auto_ontology_mcp import semantic
from auto_ontology_mcp.config import Settings
from auto_ontology_mcp.search import NAME_SUFFIX, Doc, name_text, search as ontology_search
from auto_ontology_mcp.semantic import (
    VectorIndex,
    blend_ontology_score,
    normalize_vector,
    rrf_fuse,
    sqlite_vec_available,
)
from auto_ontology_mcp.service import OntologyService

from strict_skip import REQUIRE_SEMANTIC_ENV, skip_unless_required

ROOT = Path(__file__).resolve().parent.parent


class ConceptEmbedder:
    """Deterministic local embedder mapping multilingual concepts to orthogonal axes.

    Dimensions:
      0: memory / forgetting (기억, 망각, 에빙하우스, memory, decay, forget)
      1: spaced repetition / review (간격, 반복, 복습, spaced, repetition, flashcard, review)
      2: revenue / sales (매출, 수익, 판매액, revenue, sales, line_total, turnover)
      3: customer / buyer (고객, 구매자, 회원, customer, buyer, client, account)
      4: shipping / delivery delay (배송, 지연, 물류, delivery, fulfilment, logistics, shipment, late)
      5: advertising / marketing spend (광고, 마케팅, ad spend, roas, campaign)
    """

    _AXES: tuple[tuple[str, ...], ...] = (
        ("기억", "망각", "에빙하우스", "사라진", "memory", "forget", "forgetting", "decay", "retention"),
        ("간격", "반복", "복습", "학습", "spaced", "repetition", "flashcard", "review", "leitner", "practice"),
        ("매출", "수익", "판매액", "revenue", "line_total", "turnover", "income"),
        ("고객", "구매자", "회원", "customer", "buyer", "client", "account"),
        ("배송", "지연", "물류", "출고", "delivery", "fulfilment", "logistics", "shipment", "delay", "late"),
        ("광고", "마케팅", "ad spend", "roas", "advertising", "campaign"),
    )

    def __init__(self, model_name: str = "fake-multilingual-v1", dim: int | None = None) -> None:
        self._model_name = model_name
        self._dim = dim or len(self._AXES)
        self.doc_calls: list[list[str]] = []
        self.query_calls: list[str] = []

    @property
    def model_name(self) -> str:
        return self._model_name

    def _encode(self, text: str) -> list[float]:
        low = (text or "").casefold()
        vec = [0.0] * self._dim
        for idx, keywords in enumerate(self._AXES):
            if idx >= self._dim:
                break
            hits = sum(1 for kw in keywords if kw in low)
            if hits:
                vec[idx] = float(hits)
        # Mild semantic overlap between ad spend (axis 5) and revenue (axis 2): ~0.55 cosine
        # so check_answerable must still reject "ad spend" as not_covered.
        if self._dim >= 6 and vec[5] > 0 and vec[2] == 0:
            vec[2] = 0.65 * vec[5]
        return normalize_vector(vec)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        items = list(texts)
        self.doc_calls.append(items)
        return [self._encode(t) for t in items]

    def embed_query(self, text: str) -> list[float]:
        self.query_calls.append(text)
        return self._encode(text)


class FailingEmbedder:
    model_name = "broken-embedder"

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        raise RuntimeError("model download unavailable offline")

    def embed_query(self, text: str) -> list[float]:
        raise RuntimeError("model download unavailable offline")


def _attach_embedder(svc: OntologyService, embedder, *, use_sqlite_vec: bool | None = None) -> None:
    vec_path = svc.settings.store_path.with_name(f"{svc.settings.store_path.stem}.vectors.sqlite")
    vectors = VectorIndex(vec_path, enabled=True, embedder=embedder, use_sqlite_vec=use_sqlite_vec)
    svc.vectors = vectors
    svc.knowledge.index.vectors = vectors
    svc._synced_ontology_digest.clear()


# =========================================================== extra & fallback


def test_extra_and_fallback_pyproject_declares_semantic_extra():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    core_deps = data["project"]["dependencies"]
    assert not any(d.startswith(("fastembed", "sqlite-vec")) for d in core_deps)

    opt = data["project"]["optional-dependencies"]
    assert "semantic" in opt
    semantic_deps = opt["semantic"]
    assert any(d.startswith("fastembed") for d in semantic_deps)
    assert any(d.startswith("sqlite-vec") for d in semantic_deps)


def test_extra_and_fallback_without_dependencies_stays_lexical(modeled, monkeypatch):
    monkeypatch.setattr(semantic, "fastembed_available", lambda: False)
    monkeypatch.setattr(semantic, "sqlite_vec_available", lambda: False)
    svc = OntologyService(modeled.settings)

    ready = svc.check_readiness()
    assert ready["semantic_search"]["enabled"] is False
    assert "auto-ontology-mcp[semantic]" in ready["semantic_search"]["reason"]

    ont = svc.search_ontology("revenue")
    assert ont["search_mode"] == "lexical"
    assert ont["hits"][0]["name"] == "Order Line › Revenue"

    svc.knowledge.save_passages(
        [{"text": "에빙하우스의 망각 곡선은 기억이 빠르게 사라짐을 보여 준다.", "heading": "망각 곡선"}],
        source={"title": "기억의 과학", "kind": "web"},
    )
    txt = svc.knowledge.search_text("기억")
    assert txt["search_mode"] == "lexical"
    assert txt["hits"][0]["heading"] == "망각 곡선"
    assert not svc.vectors.path.exists()


def test_extra_and_fallback_disabled_via_env_var(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTO_ONTOLOGY_HOME", str(tmp_path))
    monkeypatch.setenv("AUTO_ONTOLOGY_SEMANTIC", "0")
    monkeypatch.setenv("AUTO_ONTOLOGY_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")

    cfg = Settings.from_env()
    assert cfg.semantic_search is False
    assert cfg.embedding_model == "BAAI/bge-small-en-v1.5"

    vectors = VectorIndex(tmp_path / "vec.sqlite", enabled=cfg.semantic_search, embedder=ConceptEmbedder())
    assert vectors.is_available() is False
    assert vectors.status() == {"enabled": False, "reason": "disabled by AUTO_ONTOLOGY_SEMANTIC"}


def test_extra_and_fallback_runtime_error_degrades_gracefully(modeled):
    _attach_embedder(modeled, FailingEmbedder())

    ont = modeled.search_ontology("revenue")
    assert ont["hits"][0]["name"] == "Order Line › Revenue"

    modeled.knowledge.save_passages(
        [{"text": "Spaced repetition strengthens long-term memory.", "heading": "Review"}],
        source={"title": "Memory Notes", "kind": "note"},
    )
    txt = modeled.knowledge.search_text("spaced repetition")
    assert txt["hits"][0]["heading"] == "Review"


# =============================================================== vector index


def test_vector_index_incremental_sync_and_deletion(tmp_path):
    embedder = ConceptEmbedder()
    idx = VectorIndex(tmp_path / "vectors.sqlite", enabled=True, embedder=embedder)

    first = idx.sync_items(
        "knowledge",
        "passage",
        {
            "1": "에빙하우스의 망각 곡선은 기억이 빠르게 사라진다는 것을 보여 준다.",
            "2": "간격 반복은 복습 간격을 점점 늘리는 학습 기법이다.",
            "3": "물류 센터 이전으로 배송 지연이 발생했다.",
        },
    )
    assert first == {"embedded": 3, "deleted": 0, "unchanged": 0}
    assert len(embedder.doc_calls) == 1

    # Repeating with identical content embeds nothing.
    second = idx.sync_items(
        "knowledge",
        "passage",
        {
            "1": "에빙하우스의 망각 곡선은 기억이 빠르게 사라진다는 것을 보여 준다.",
            "2": "간격 반복은 복습 간격을 점점 늘리는 학습 기법이다.",
            "3": "물류 센터 이전으로 배송 지연이 발생했다.",
        },
    )
    assert second == {"embedded": 0, "deleted": 0, "unchanged": 3}
    assert len(embedder.doc_calls) == 1

    # Updating item 2 and deleting item 3 embeds only item 2 and removes item 3.
    third = idx.sync_items(
        "knowledge",
        "passage",
        {
            "1": "에빙하우스의 망각 곡선은 기억이 빠르게 사라진다는 것을 보여 준다.",
            "2": "매출 수익이 크게 증가했다.",
        },
    )
    assert third == {"embedded": 1, "deleted": 1, "unchanged": 1}
    assert len(embedder.doc_calls) == 2 and len(embedder.doc_calls[1]) == 1

    # Deleted delivery passage is gone; updated item 2 now matches revenue queries.
    assert idx.search("knowledge", ["passage"], "delivery delay") == []
    rev_hits = idx.search("knowledge", ["passage"], "revenue turnover")
    assert [h.item_id for h in rev_hits] == ["2"]
    assert rev_hits[0].similarity >= 0.99


def test_vector_index_rebuilds_when_model_changes(tmp_path):
    db_path = tmp_path / "vectors.sqlite"
    emb_v1 = ConceptEmbedder(model_name="model-v1", dim=6)
    idx_v1 = VectorIndex(db_path, enabled=True, embedder=emb_v1)
    idx_v1.sync_items("knowledge", "term", {"t1": "Forgetting Curve memory decay"})
    assert idx_v1.search("knowledge", ["term"], "기억 망각")[0].item_id == "t1"

    # Switching to a model with different name & dimension resets and re-indexes cleanly.
    emb_v2 = ConceptEmbedder(model_name="model-v2", dim=8)
    idx_v2 = VectorIndex(db_path, enabled=True, embedder=emb_v2)
    stats = idx_v2.sync_items("knowledge", "term", {"t1": "Forgetting Curve memory decay"})
    assert stats == {"embedded": 1, "deleted": 0, "unchanged": 0}
    hits = idx_v2.search("knowledge", ["term"], "기억 망각")
    assert [h.item_id for h in hits] == ["t1"]


def test_vector_index_respects_scope_and_kind_isolation(tmp_path):
    embedder = ConceptEmbedder()
    idx = VectorIndex(tmp_path / "vectors.sqlite", enabled=True, embedder=embedder)
    idx.sync_items("ontology:ds1", "term", {"t_rev": "Order Line Revenue"})
    idx.sync_items("ontology:ds2", "term", {"t_other": "Order Line Revenue"})
    idx.sync_items("knowledge", "passage", {"p1": "Sales revenue grew"})

    ds1_hits = idx.search("ontology:ds1", ["term"], "매출")
    assert [(h.kind, h.item_id) for h in ds1_hits] == [("term", "t_rev")]

    kb_hits = idx.search("knowledge", ["passage"], "매출")
    assert [(h.kind, h.item_id) for h in kb_hits] == [("passage", "p1")]


def test_vector_index_sqlite_vec_real_extension_when_installed(tmp_path):
    if not sqlite_vec_available():
        skip_unless_required("sqlite-vec is not installed in this environment", REQUIRE_SEMANTIC_ENV)
    embedder = ConceptEmbedder()
    idx = VectorIndex(tmp_path / "vec0.sqlite", enabled=True, embedder=embedder, use_sqlite_vec=True)
    assert idx.is_available() is True
    assert idx.status()["backend"] == "sqlite-vec"

    idx.sync_items("knowledge", "passage", {"1": "memory decay", "2": "shipping logistics delay"})
    hits = idx.search("knowledge", ["passage"], "기억 망각")
    assert [h.item_id for h in hits] == ["1"]
    assert hits[0].similarity >= 0.99

    idx.sync_items("knowledge", "passage", {"2": "shipping logistics delay"})
    assert idx.search("knowledge", ["passage"], "기억 망각") == []


# =========================================================== textindex hybrid


def _seed_hybrid_kb(settings: Settings) -> OntologyService:
    svc = OntologyService(settings)
    _attach_embedder(svc, ConceptEmbedder())
    k = svc.knowledge
    k.save_concept("Forgetting Curve", "Memory decays quickly without review.", synonyms=["망각 곡선"])
    k.save_concept("Spaced Repetition", "Reviewing flashcards at increasing intervals.")
    k.save_passages(
        [
            {
                "text": "에빙하우스의 망각 곡선은 시간이 지나면 기억이 빠르게 사라진다는 것을 보여 준다.",
                "heading": "망각 곡선",
                "terms": ["Forgetting Curve"],
            },
            {
                "text": "복습 간격을 점점 늘리는 것이 핵심이다.",
                "heading": "간격 반복",
                "terms": ["Spaced Repetition"],
            },
        ],
        source={"title": "기억의 과학", "kind": "web", "uri": "https://example.com/memory"},
    )
    k.save_passages(
        [
            {
                "text": "Fulfilment moved to Lakeshore Logistics and deliveries took eight days instead of three.",
                "heading": "January cutover",
                "terms": ["Fulfilment Cutover"],
            },
        ],
        source={"title": "Midwest sales review", "kind": "note", "uri": "https://example.com/midwest"},
    )
    return svc


def test_textindex_hybrid_rrf_and_multilingual_paraphrase_recall(settings):
    svc = _seed_hybrid_kb(settings)
    k = svc.knowledge

    # Cross-lingual / paraphrase query with zero lexical overlap against Korean passage.
    sem_only = k.search_text("memory retention decay", kinds=["passage"])
    assert sem_only["search_mode"] == "hybrid"
    assert sem_only["matched"] == "semantic"
    assert sem_only["hits"][0]["heading"] == "망각 곡선"
    assert sem_only["hits"][0]["match"] == "semantic"
    assert sem_only["hits"][0]["semantic_score"] >= 0.9

    # Korean query recalls English passage about delivery delay.
    ko_to_en = k.search_text("배송 지연 물류", kinds=["passage"])
    assert ko_to_en["hits"][0]["heading"] == "January cutover"
    assert ko_to_en["hits"][0]["match"] == "semantic"

    # Dual-signal query keeps FTS5 match='words' + snippet highlight and adds semantic_score via RRF.
    hybrid = k.search_text("기억", kinds=["passage"])
    assert hybrid["search_mode"] == "hybrid"
    assert hybrid["hits"][0]["heading"] == "망각 곡선"
    assert hybrid["hits"][0]["match"] == "words"
    assert "[기억이]" in hybrid["hits"][0]["snippet"]
    assert hybrid["hits"][0]["semantic_score"] >= 0.9

    # Unrelated query still yields empty hits (min_similarity filters noise).
    empty = k.search_text("quantum banana telescope")
    assert empty["hits"] == [] and empty["hint"]

    # knowledge.sqlite stays pure SQLite (no vec0 tables polluting schema or catalog).
    con = sqlite3.connect(k.path)
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    con.close()
    assert "vec_embeddings" not in tables and "vec_blobs" not in tables


def test_textindex_hybrid_preserves_source_term_filters_and_deletes(settings):
    svc = _seed_hybrid_kb(settings)
    k = svc.knowledge

    # Add a second passage about memory decay in source 2 to verify source filter.
    added = k.save_passages(
        [{"text": "Memory fades exponentially after learning.", "heading": "Decay", "terms": ["Forgetting Curve"]}],
        source={"id": 2},
    )
    pid_s2 = added["inserted"][0]

    only_s1 = k.search_text("memory decay", kinds=["passage"], source=1)["hits"]
    assert only_s1 and all(h["source_id"] == 1 for h in only_s1)

    only_s2 = k.search_text("memory decay", kinds=["passage"], source=2)["hits"]
    assert [h["passage_id"] for h in only_s2] == [pid_s2]

    only_term = k.search_text("flashcards review", kinds=["passage"], term="Spaced Repetition")["hits"]
    assert only_term and all("Spaced Repetition" in h["terms"] for h in only_term)

    # Deleting the passage removes it from hybrid vector search immediately.
    k.save_passages(delete_ids=[pid_s2])
    after_del = k.search_text("memory decay", kinds=["passage"], source=2)["hits"]
    assert after_del == []


# ============================================================ ontology hybrid


def test_ontology_hybrid_recalls_synonyms_and_multilingual_terms(modeled):
    embedder = ConceptEmbedder()
    _attach_embedder(modeled, embedder)

    # Korean query with zero lexical overlap recalls "Order Line › Revenue".
    ko_rev = modeled.search_ontology("총 매출액")
    assert ko_rev["search_mode"] == "hybrid"
    names = [h["name"] for h in ko_rev["hits"]]
    assert "Order Line › Revenue" in names[:3]
    rev_hit = next(h for h in ko_rev["hits"] if h["name"] == "Order Line › Revenue")
    assert rev_hit["match"] == "semantic"
    assert rev_hit["semantic_score"] >= 0.9

    # Paraphrase query recalls "Customer".
    buyer = modeled.search_ontology("buyer account", kinds=["term"])
    assert buyer["hits"][0]["name"] == "Customer"

    # Exact lexical match stays ranked #1 with full score 1.0.
    exact = modeled.search_ontology("revenue")
    assert exact["hits"][0]["name"] == "Order Line › Revenue"
    assert exact["hits"][0]["score"] == 1.0

    # Repeated search without catalog changes hits in-memory sync cache (no new document embeddings).
    calls_before = len(embedder.doc_calls)
    modeled.search_ontology("총 매출액")
    assert len(embedder.doc_calls) == calls_before


def test_ontology_hybrid_preserves_check_answerable_precision(modeled):
    _attach_embedder(modeled, ConceptEmbedder())

    # "ad spend" has moderate (~0.55) semantic overlap with Revenue, below check_answerable's
    # floor + margin (0.50 + 0.40 for a model without a profile).
    missing = modeled.check_answerable(
        "Return on ad spend for flash drives",
        ["ad spend", "return on ad spend", "flash drive"],
    )
    assert missing["verdict"] in ("partially_answerable", "not_answerable")
    assert any(e["entity"] == "ad spend" and e["state"] == "not_covered" for e in missing["entities"])

    # True multilingual synonym ("매출" -> Revenue, similarity ~1.0 >= 0.90) IS covered.
    covered = modeled.check_answerable("고객별 매출", ["고객", "매출"])
    assert covered["verdict"] == "answerable"


def test_ontology_hybrid_fusion_math_invariants():
    # Exact lexical match never degraded even when semantic is None or low.
    assert blend_ontology_score(1.0, None) == 1.0
    assert blend_ontology_score(0.9, 0.2) == 0.9

    # Pure semantic recall above min_semantic surfaces above 0.2 min_score.
    assert blend_ontology_score(0.0, 0.8) == pytest.approx(0.68)
    assert blend_ontology_score(0.0, 0.3) == 0.0

    # Dual match boosts partial lexical score while staying <= 1.0 and monotonic in lex.
    sem_only = blend_ontology_score(0.0, 0.8)
    boosted_low = blend_ontology_score(0.15, 0.8)
    boosted_mid = blend_ontology_score(0.5, 0.8)
    assert sem_only < boosted_low < boosted_mid <= 1.0

    # RRF ranks dual-matched items above single-signal items.
    fused = rrf_fuse([("a", 5.0), ("b", 2.0)], [("b", 0.95), ("c", 0.90)])
    assert fused["b"] > fused["a"] > fused["c"]

    # search() direct call with semantic_scores blends cleanly.
    docs = [
        Doc("term", "t1", "Revenue", [("Revenue", 1.0)]),
        Doc("term", "t2", "Customer", [("Customer", 1.0)]),
    ]
    hits = ontology_search("매출", docs, semantic_scores={("term", "t1"): 0.88})
    assert [h["id"] for h in hits] == ["t1"]
    assert hits[0]["match"] == "semantic"
    assert hits[0]["semantic_score"] == 0.88

    # Settings replace compatibility stays intact.
    s = replace(Settings.from_env(), semantic_search=False)
    assert s.semantic_search is False


def test_vector_index_fastembed_wrapper_uses_lazy_cache(monkeypatch, tmp_path):
    created: list[dict] = []

    class FakeTextEmbedding:
        def __init__(self, model_name: str, cache_dir: str | None = None) -> None:
            created.append({"model_name": model_name, "cache_dir": cache_dir})

        def passage_embed(self, texts):
            for _ in texts:
                yield [3.0, 4.0]

        def query_embed(self, texts):
            for _ in texts:
                yield [3.0, 4.0]

    class FakeModule:
        TextEmbedding = FakeTextEmbedding

    monkeypatch.setattr(semantic, "_MODEL_CACHE", {})
    monkeypatch.setattr(semantic.importlib, "import_module", lambda name: FakeModule)

    emb = semantic.FastEmbedder("test/model", cache_dir=tmp_path)
    assert created == []  # lazy: not loaded until first embed call
    docs = emb.embed_documents(["hello"])
    query = emb.embed_query("hello")
    assert len(created) == 1
    assert created[0] == {"model_name": "test/model", "cache_dir": str(tmp_path)}
    assert docs[0] == pytest.approx([0.6, 0.8])
    assert query == pytest.approx([0.6, 0.8])
    # Second instance with same model_name/cache_dir reuses cached model.
    semantic.FastEmbedder("test/model", cache_dir=tmp_path).embed_query("again")
    assert len(created) == 1


def test_hybrid_search_reuses_cached_revision_docs(settings, tmp_path, monkeypatch):
    # The vector sync gets the per-revision cached document list; handed the same list again,
    # it returns without re-hashing every document.
    import auto_ontology_mcp.service as service_mod

    svc = OntologyService(settings)
    ds = {"id": "ds1", "name": "shop"}
    texts: list[str] = []
    real_doc_text = service_mod.doc_text
    monkeypatch.setattr(service_mod, "doc_text", lambda d: texts.append(d.id) or real_doc_text(d))

    class Vec:
        model_name = "m"
        path = tmp_path / "s.vectors.sqlite"

        def sync_items(self, scope, kind, items):
            pass

    Vec.path.write_text("")
    svc.vectors = Vec()
    docs = [Doc("term", "t1", "Customer", [("Customer", 1.0)])]
    svc._sync_ontology_vectors(ds, docs)
    assert texts == ["t1"]
    svc._sync_ontology_vectors(ds, docs)
    assert texts == ["t1"]
    svc._sync_ontology_vectors(ds, list(docs))
    assert texts == ["t1", "t1"]


def test_name_text_is_bare_name_and_term_synonyms():
    term = Doc("term", "t1", "Revenue", [("Revenue", 1.0), ("Turnover Sales", 0.95), ("order_lines", 0.6)])
    attr = Doc("attribute", "a1", "Order Line › Revenue", [("Revenue", 0.9), ("", 0.9), ("line_total", 0.75), ("237.0 79.0", 0.35)])
    status = Doc("attribute", "a2", "Order › Order Status", [("Order Status", 0.9), ("주문 상태", 0.9), ("status", 0.75)])
    example = Doc("example", "e1", "Revenue by month", [("Revenue by month", 0.9), ("monthly", 0.4)])
    bare = Doc("glossary", "g1", "GMV", [("GMV", 1.0)])
    assert name_text(term) == "Revenue\nTurnover Sales"
    assert name_text(attr) == "Revenue"
    # A column's saved synonyms join its name vector, the column name does not.
    assert name_text(status) == "Order Status\n주문 상태"
    # Long-text kinds get no name vector; a name that is the whole document needs no second one.
    assert name_text(example) == ""
    assert name_text(bare) == ""


class NameEmbedder:
    """Puts the bare word "Revenue" and the query "turnover" on one axis, everything else on another."""

    model_name = "fake-name-v1"

    def _encode(self, text: str) -> list[float]:
        return [1.0, 0.0] if text.strip().casefold() in ("revenue", "turnover") else [0.0, 1.0]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._encode(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._encode(text)


def test_hybrid_search_finds_metric_by_its_name_vector(settings, tmp_path):
    # The full text mixes the name with column names and samples, which the embedder
    # puts far from the query; the name-only vector still finds the attribute.
    svc = OntologyService(settings)
    svc.vectors = VectorIndex(tmp_path / "n.vectors.sqlite", enabled=True, embedder=NameEmbedder(), use_sqlite_vec=False)
    ds = {"id": "ds-name", "name": "shop"}
    docs = [
        Doc("attribute", "a1", "Order Line › Revenue", [("Revenue", 0.9), ("", 0.9), ("line_total", 0.75), ("237.0 79.0", 0.35)]),
        Doc("attribute", "a2", "Order › Status", [("Status", 0.9), ("", 0.9), ("status", 0.75), ("Delivered Shipped", 0.35)]),
    ]
    hits = svc._search(ds, "turnover", docs=docs)
    assert [h["id"] for h in hits] == ["a1"]
    assert hits[0]["match"] == "semantic"
    assert hits[0]["semantic_score"] == 1.0
    assert not any(h["id"].endswith(NAME_SUFFIX) for h in hits)
