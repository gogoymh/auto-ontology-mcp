"""The target index must return what comparing every column with every key returned.

These tests keep the old shape of the algorithm as a reference: one loop over the primary keys
for each column, no index. The indexed path has to produce the same candidates, in the same
order, with the same scores and reasons.
"""

from __future__ import annotations

import random

from auto_ontology_mcp import inference
from auto_ontology_mcp.inference import _prepare, _score_prepared


def _reference(tables, columns_by_table, declared):
    """The pre-index algorithm: every column against every single-column primary key."""
    declared_sources = {source for source, _ in declared}
    targets = []
    for table in tables:
        pk = table.get("pk") or []
        if len(pk) != 1:
            continue
        for col in columns_by_table.get(table["id"], []):
            if col["name"] == pk[0]:
                targets.append((table, col))

    candidates, unlinked = [], []
    for table in tables:
        pk = set(table.get("pk") or [])
        for col in columns_by_table.get(table["id"], []):
            if col["id"] in declared_sources:
                continue
            is_own_pk = col["name"] in pk and len(pk) == 1
            id_like = inference.id_base(col["name"]) is not None
            best = []
            for target_table, target_col in targets:
                if target_col["id"] == col["id"]:
                    continue
                if is_own_pk and target_table["id"] == table["id"]:
                    continue
                if inference._looks_like_measure(col["name"]) and not id_like:
                    continue
                source_family = inference._type_family(col.get("data_type"))
                target_family = inference._type_family(target_col.get("data_type"))
                if not inference._families_compatible(source_family, target_family):
                    continue
                hit = _score_prepared(_prepare(col, table), _prepare(target_col, target_table))
                if hit is None:
                    continue
                score, role, reason = hit
                if is_own_pk:
                    score -= 0.2
                best.append((score, role, reason, target_col))
            if best:
                best.sort(key=lambda item: item[0], reverse=True)
                top = best[0][0]
                for score, role, reason, target_col in best:
                    if score >= top - 0.1:
                        candidates.append((col["id"], target_col["id"], score, role, reason))
            elif (
                id_like
                and not is_own_pk
                and col["name"] not in pk
                and not inference.names_own_table(col["name"], table["name"])
            ):
                unlinked.append((table["id"], col["id"]))
    return candidates, unlinked


def _indexed(tables, columns_by_table, declared):
    candidates, unlinked = inference.candidate_pairs(tables, columns_by_table, declared)
    return (
        [(c.source["id"], c.target["id"], c.name_score, c.role, c.reasons[0]) for c in candidates],
        [(item["table"]["id"], item["column"]["id"]) for item in unlinked],
    )


def _schema(rows):
    """rows: (table_name, pk_column, [(column_name, data_type), ...])"""
    tables, columns = [], {}
    for name, pk, cols in rows:
        table_id = f"tbl_{name}"
        tables.append({"id": table_id, "name": name, "pk": [pk], "schema_name": ""})
        columns[table_id] = [
            {"id": f"col_{name}_{col}", "name": col, "data_type": dtype, "table_id": table_id}
            for col, dtype in cols
        ]
    return tables, columns


def test_the_index_agrees_with_the_reference_on_a_handcrafted_schema():
    tables, columns = _schema(
        [
            ("customers", "customer_id", [("customer_id", "INTEGER"), ("city_id", "INTEGER")]),
            ("cities", "id", [("id", "INTEGER"), ("state_code", "TEXT")]),
            ("states", "state_code", [("state_code", "TEXT")]),
            ("orders", "order_id", [
                ("order_id", "INTEGER"), ("customer_id", "INTEGER"), ("parent_id", "INTEGER"),
                ("billing_city_id", "INTEGER"), ("total_amount", "NUMERIC"), ("status", "TEXT"),
                ("order_no", "TEXT"), ("custmer_id", "INTEGER"),
            ]),
            ("line_items", "line_id", [("line_id", "INTEGER"), ("order_id", "INTEGER")]),
            ("audit_log", "log_id", [("log_id", "INTEGER"), ("entity_id", "INTEGER")]),
        ]
    )
    assert _indexed(tables, columns, set()) == _reference(tables, columns, set())


def _random_schema(seed: int, tables: int = 22, columns: int = 8):
    rng = random.Random(seed)
    words = ["customer", "city", "state", "order", "product", "employee", "invoice", "tag",
             "custmer", "custome", "ordr", "prodct", "citie", "regions"]
    rows = []
    for i in range(tables):
        word = rng.choice(words)
        pk = rng.choice(["id", "uuid", f"{word}_id", f"{word}_key"])
        cols = [(pk, rng.choice(["INTEGER", "VARCHAR(36)"]))]
        seen = {pk}
        for j in range(columns - 1):
            other = rng.choice(words)
            style = rng.random()
            if style < 0.45:
                name = f"{other}_id"
            elif style < 0.65:
                name = f"parent_{other}_id"
            elif style < 0.8:
                name = f"{other}_code"
            else:
                name = f"{other}_{j}"
            while name in seen:  # a repeated column name would be a duplicate catalog row
                name = f"{name}{j}"
            seen.add(name)
            cols.append((name, rng.choice(["INTEGER", "TEXT", "NUMERIC", "DATE"])))
        rows.append((f"{word}_{i}", pk, cols))
    return _schema(rows)


def test_the_index_agrees_with_the_reference_on_random_schemas():
    for seed in range(6):
        tables, columns = _random_schema(seed)
        assert _indexed(tables, columns, set()) == _reference(tables, columns, set()), f"seed {seed}"


def test_declared_targets_are_skipped_by_both():
    tables, columns = _schema(
        [
            ("customers", "customer_id", [("customer_id", "INTEGER")]),
            ("orders", "order_id", [("order_id", "INTEGER"), ("customer_id", "INTEGER")]),
        ]
    )
    declared = {("col_orders_customer_id", "col_customers_customer_id")}
    assert _indexed(tables, columns, declared) == _reference(tables, columns, declared)
    assert all(c[0] != "col_orders_customer_id" for c in _indexed(tables, columns, declared)[0])


def test_a_full_catalog_is_not_scored_pair_by_pair(monkeypatch):
    """The index is the point: a column is scored against a handful of keys, not all of them."""
    tables, columns = _random_schema(11, tables=120, columns=9)
    calls = 0
    original = inference._score_prepared

    def counting(source, target):
        nonlocal calls
        calls += 1
        return original(source, target)

    monkeypatch.setattr(inference, "_score_prepared", counting)
    inference.candidate_pairs(tables, columns, set())

    pairs = sum(len(cols) for cols in columns.values()) * len(tables)
    assert calls < pairs * 0.15, f"{calls} scores for {pairs} pairs"

def test_a_strong_indexed_match_skips_the_fuzzy_scan(monkeypatch):
    """A fuzzy candidate scores 0.6 and stays within 0.1 of the best, so above 0.7 it cannot survive."""
    tables, columns = _schema(
        [
            ("customers", "id", [("id", "INTEGER")]),
            ("orders", "order_id", [("order_id", "INTEGER"), ("customer_id", "INTEGER")]),
            ("custmer", "id", [("id", "INTEGER")]),
        ]
    )
    seen: list[str] = []
    original = inference._similar

    def counting(base, table_name):
        seen.append(base)
        return original(base, table_name)

    monkeypatch.setattr(inference, "_similar", counting)
    candidates, _ = inference.candidate_pairs(tables, columns, set())
    # An empty stem ("id") is compared with the bare-"id" targets it was indexed for, and the
    # ratio is zero; the point here is that the strong column's stem never reaches the fuzzy rule.
    assert "customer" not in seen
    assert [(c.name_score, c.target["table"]["id"]) for c in candidates if c.source["name"] == "customer_id"] == [
        (0.9, "tbl_customers")
    ]

    # With the skip removed the same schema does reach the fuzzy comparison, so the miss above is
    # the skip and not a schema that could never fuzzy-match.
    monkeypatch.setattr(inference, "_FUZZY_FLOOR", 1.0)  # every indexed score is at or below it
    inference.candidate_pairs(tables, columns, set())
    assert "customer" in seen


def test_the_fuzzy_window_still_runs_when_nothing_else_matched():
    tables, columns = _schema(
        [
            ("customers", "id", [("id", "INTEGER")]),
            ("orders", "order_id", [("order_id", "INTEGER"), ("custmer_id", "INTEGER")]),
        ]
    )
    candidates, _ = inference.candidate_pairs(tables, columns, set())
    typo = [c for c in candidates if c.source["name"] == "custmer_id"]
    assert [(c.name_score, c.target["table"]["id"]) for c in typo] == [(0.6, "tbl_customers")]


def test_a_seventy_score_still_keeps_a_fuzzy_candidate():
    """0.7 - 0.1 is exactly 0.6: a fuzzy candidate is kept, so the window must still be opened."""
    tables, columns = _schema(
        [
            ("orders", "order_id", [("order_id", "INTEGER"), ("parent_id", "INTEGER")]),
            ("parrent", "id", [("id", "INTEGER")]),
        ]
    )
    candidates, _ = inference.candidate_pairs(tables, columns, set())
    parent = sorted(((c.name_score, c.target["table"]["id"]) for c in candidates if c.source["name"] == "parent_id"), reverse=True)
    assert parent == [(0.7, "tbl_orders"), (0.6, "tbl_parrent")]
