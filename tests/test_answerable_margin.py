"""check_answerable's per-model margin and the noise level of names close to everything.

A tiny deterministic embedder stands in for the model: "Tag" is a hub name, close to every word
it knows nothing about, as "Tag" is to "재고", "광고" and "주가" with MiniLM.
"""

from __future__ import annotations

import hashlib
from typing import Sequence

from auto_ontology_mcp import semantic
from auto_ontology_mcp.search import NAME_SUFFIX
from auto_ontology_mcp.semantic import (
    DEFAULT_ANSWERABLE_MARGIN,
    DEFAULT_EMBEDDING_MODEL,
    LARGER_EMBEDDING_MODEL,
    VectorIndex,
    answerable_margin,
    normalize_vector,
)

HASH_DIMS = 64
REVENUE, CUSTOMER, COMMON = 0, 1, 2


class HubEmbedder:
    """Revenue and customer words get their own axis; "tag" is pure shared axis (a hub); any other
    text gets a hashed axis of its own plus the shared axis, so it sits near "Tag" and far apart
    from other unknown texts."""

    def __init__(self, model_name: str = DEFAULT_EMBEDDING_MODEL) -> None:
        self.model_name = model_name

    def _encode(self, text: str) -> list[float]:
        low = (text or "").casefold()
        vec = [0.0] * (3 + HASH_DIMS)
        if any(k in low for k in ("revenue", "매출")):
            vec[REVENUE] = 1.0
        if any(k in low for k in ("customer", "고객")):
            vec[CUSTOMER] = 1.0
        if "tag" in low:
            vec[COMMON] = 1.0
        if not any(vec):
            vec[3 + int.from_bytes(hashlib.blake2b(low.encode(), digest_size=4).digest(), "big") % HASH_DIMS] = 1.0
            vec[COMMON] = 1.5
        return normalize_vector(vec)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._encode(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._encode(text)


def _attach(svc, embedder) -> None:
    vec_path = svc.settings.store_path.with_name(f"{svc.settings.store_path.stem}.hub.vectors.sqlite")
    svc.vectors = VectorIndex(vec_path, enabled=True, embedder=embedder, use_sqlite_vec=False)
    svc._synced_ontology_digest.clear()
    svc._synced_docs.clear()
    svc._noise.clear()


def _entity(svc, phrase: str) -> dict:
    return svc.check_answerable(phrase, [phrase])["entities"][0]


def test_margin_table_differs_per_model(tmp_path):
    assert answerable_margin(DEFAULT_EMBEDDING_MODEL) == 0.30
    assert answerable_margin(LARGER_EMBEDDING_MODEL) == 0.40
    assert answerable_margin("unknown/model") == DEFAULT_ANSWERABLE_MARGIN
    assert VectorIndex(tmp_path / "a.sqlite", enabled=True, model_name="mpnet").answerable_margin == 0.40
    assert VectorIndex(tmp_path / "b.sqlite", enabled=True, model_name="minilm").answerable_margin == 0.30


def test_hub_name_does_not_cover_an_unrelated_phrase(modeled):
    _attach(modeled, HubEmbedder())
    vectors = modeled.vectors
    threshold = vectors.min_similarity + vectors.answerable_margin
    # By cosine alone "재고" (stock) would be covered by "Tag".
    assert vectors.similarities("재고", ["Tag"])[0] >= threshold

    assert _entity(modeled, "재고")["state"] == "not_covered"
    ds = modeled.ds(None)
    noise = modeled._noise_levels(ds)
    by_name = {d.title: noise.get((d.kind, d.id)) for d in modeled._docs(ds)}
    assert by_name["Tag"] >= 0.8 and by_name["Order Line › Revenue"] < 0.1

    revenue = _entity(modeled, "매출")
    # Order Line's description ("revenue lives here") shares the revenue axis in this embedder.
    assert revenue["state"] == "covered" and revenue["matches"][0]["name"] in ("Order Line › Revenue", "Order Line")
    # The hub stays reachable by its own word.
    tag = _entity(modeled, "tag")
    assert tag["state"] == "covered" and tag["matches"][0]["name"] == "Tag"


def test_threshold_follows_the_models_margin(modeled, monkeypatch):
    _attach(modeled, HubEmbedder())
    assert _entity(modeled, "매출")["state"] == "covered"
    # "매출" sits at 1.0 from Revenue; a margin past 0.50 over the 0.50 floor is out of reach.
    monkeypatch.setitem(semantic.ANSWERABLE_MARGIN, DEFAULT_EMBEDDING_MODEL, 0.55)
    assert _entity(modeled, "매출")["state"] == "not_covered"


def test_noise_levels_merge_full_text_and_name_vectors(tmp_path):
    index = VectorIndex(tmp_path / "n.sqlite", enabled=True, embedder=HubEmbedder(), use_sqlite_vec=False)
    index.sync_items("s", "term", {"t1": "Revenue earned", "t1" + NAME_SUFFIX: "Tag", "t2": "Revenue"})
    levels = index.noise_levels("s", ["term"], key=lambda iid: iid.removesuffix(NAME_SUFFIX))
    assert set(levels) == {("term", "t1"), ("term", "t2")}
    assert levels[("term", "t1")] >= 0.8
    assert levels[("term", "t2")] < 0.1
