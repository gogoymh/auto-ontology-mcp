"""Trigrams are memoized: one search compares the same field tokens thousands of times."""

from __future__ import annotations

from auto_ontology_mcp.naming import trigram_similarity, trigrams


def test_the_same_text_returns_the_same_frozen_set():
    first = trigrams("customer")

    assert first is trigrams("customer")
    assert isinstance(first, frozenset)
    assert {"  c", "cus", "er "} <= first


def test_similarity_is_the_jaccard_index_of_the_trigrams():
    for a, b in (("customer", "custmer"), ("order", "orders"), ("주문", "주문서"), ("", "order"), ("x", "")):
        ta, tb = trigrams(a), trigrams(b)
        expected = len(ta & tb) / len(ta | tb) if ta and tb else 0.0
        assert trigram_similarity(a, b) == expected


def test_a_later_call_does_not_change_an_earlier_result():
    before = trigrams("order")
    snapshot = set(before)

    trigrams("customer")
    trigrams("order no")

    assert set(trigrams("order")) == snapshot


def test_the_cache_is_bounded():
    trigrams.cache_clear()
    for i in range(5000):
        trigrams(f"token{i}")

    info = trigrams.cache_info()
    assert info.maxsize == 4096
    assert info.currsize <= 4096