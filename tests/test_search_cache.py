"""The document set is cached per datasource and rebuilt only when the store changes."""

from __future__ import annotations

from auto_ontology_mcp.search import Doc, score


def test_the_revision_moves_with_a_write_and_not_with_a_read(svc):
    before = svc.store.revision()
    svc.store.all("SELECT id FROM term")
    assert svc.store.revision() == before

    svc.save_glossary([{"name": "Revenue", "definition": "money received for goods"}])
    assert svc.store.revision() > before


def test_the_documents_are_built_once_and_reused(svc):
    ds = svc.ds()
    first = svc._docs(ds)
    assert svc._docs(ds) is first


def test_a_write_rebuilds_the_documents(svc):
    ds = svc.ds()
    first = svc._docs(ds)
    svc.save_glossary([{"name": "Revenue", "definition": "money received for goods"}])

    second = svc._docs(ds)
    assert second is not first
    assert any(doc.kind == "glossary" and doc.title == "Revenue" for doc in second)


def test_the_knowledge_datasource_is_not_cached(svc):
    """Its documents also carry the knowledge store's sources, which the ontology revision misses."""
    ds = svc.knowledge.ds()
    assert svc._docs(ds) is not svc._docs(ds)


def test_a_field_without_tokens_scores_like_no_field_at_all():
    plain = Doc("term", "a", "Order", [("Order", 1.0)])
    padded = Doc("term", "b", "Order", [("Order", 1.0), ("", 0.5), ("   ", 0.2)])

    assert [field.weight for field in plain.prepared] == [1.0]
    assert [field.weight for field in padded.prepared] == [1.0]
    assert score("order", padded) == score("order", plain) == 1.0


def test_a_field_is_tokenized_once_when_its_document_is_built():
    doc = Doc("term", "a", "Order", [("Order No", 1.0), ("Customer", 0.5)])

    assert [sorted(field.tokens) for field in doc.prepared] == [["no", "order"], ["customer"]]
    assert [field.norm for field in doc.prepared] == ["order no", "customer"]

def test_a_document_set_above_the_memory_bound_is_not_kept(svc, monkeypatch):
    """The prepared documents cost about 0.9 KB per field; past the bound the server stays flat."""
    monkeypatch.setattr("auto_ontology_mcp.service.DOCS_CACHE_MAX_FIELDS", 1)
    ds = svc.ds()
    assert svc._docs(ds) is not svc._docs(ds)
    assert ds["id"] not in svc._docs_cache
