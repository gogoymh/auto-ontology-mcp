"""Table filters for scanning part of a very large schema."""

from __future__ import annotations

import sqlite3
from dataclasses import replace

import pytest

from auto_ontology_mcp.catalog import TableFilter
from auto_ontology_mcp.config import Settings, _patterns
from auto_ontology_mcp.service import OntologyService

from conftest import DEMO_TERMS


def names(svc: OntologyService, ds: str = "demo") -> set[str]:
    return {t["name"] for t in svc.store.tables(svc.ds(ds)["id"])}


# ---------------------------------------------------------------- the filter


def test_empty_or_star_means_everything():
    assert TableFilter.parse(None) is None
    assert TableFilter.parse([]) is None
    assert TableFilter.parse(" , ") is None
    assert TableFilter.parse("*") is None
    assert TableFilter.parse(["*", "orders"]) is None


@pytest.mark.parametrize(
    "patterns,schema,table,expected",
    [
        ("STJ_KTP_*", "", "STJ_KTP_KITAP", True),
        ("stj_ktp_*", "", "STJ_KTP_KITAP", True),          # case-insensitive
        ("STJ_KTP%", "", "STJ_KTP_KITAP", True),           # % works like *
        ("STJ_KTP_*", "", "STJXKTP_KITAP", False),         # _ is literal
        ("ADR_IL?", "", "ADR_ILCE", False),
        ("ADR_IL??", "", "ADR_ILCE", True),
        ("HR.EMP*", "HR", "EMPLOYEES", True),              # schema-qualified pattern
        ("HR.EMP*", "SALES", "EMPLOYEES", False),
        ("EMP*", "SALES", "EMPLOYEES", True),              # no dot: table name only
        ("!*_H,!*_LOG", "", "ACB_YOSROL", True),           # only exclusions: everything else
        ("!*_H,!*_LOG", "", "ACB_MUSTERIRIZA_H", False),
        ("ACB_*,!*_H", "", "ACB_MUSTERIRIZA_H", False),    # exclusion wins
        ("ACB_*,!*_H", "", "ACB_YOSROL", True),
        ("ACB_*,ADR_*", "", "ADR_ILCE", True),
        ("ACB_*,ADR_*", "", "AAA", False),
    ],
)
def test_filter_matching(patterns, schema, table, expected):
    assert TableFilter.parse(patterns).matches(schema, table) is expected


def test_reports_keep_the_patterns_as_written():
    assert TableFilter.parse(["STJ_KTP_*", "!*_H"]).patterns == ["STJ_KTP_*", "!*_H"]


def test_env_var_is_split_on_commas(monkeypatch):
    assert _patterns(" SALES_* , HR.EMP*,,!*_LOG ") == ("SALES_*", "HR.EMP*", "!*_LOG")
    monkeypatch.setenv("AUTO_ONTOLOGY_SCAN_TABLES", "orders,order_*")
    assert Settings.from_env().scan_tables == ("orders", "order_*")
    monkeypatch.delenv("AUTO_ONTOLOGY_SCAN_TABLES")
    assert Settings.from_env().scan_tables == ()


# ---------------------------------------------------------------- scanning


def test_default_scans_every_table(svc):
    assert {"orders", "order_lines", "customers", "regions"} <= names(svc)


def test_env_filter_limits_the_scan(settings, demo_path):
    svc = OntologyService(replace(settings, scan_tables=("orders", "order_*")))
    out = svc.add_datasource("demo", f"sqlite:///{demo_path}")
    assert names(svc) == {"orders", "order_lines"}
    assert out["scan"]["table_filter_source"] == "env"
    assert out["scan"]["table_filter"] == ["orders", "order_*"]
    assert out["scan"]["skipped_by_filter"] > 0


def test_rescan_with_another_filter_keeps_earlier_tables(settings, demo_path):
    svc = OntologyService(replace(settings, scan_tables=("orders", "order_*")))
    svc.add_datasource("demo", f"sqlite:///{demo_path}")
    svc.save_table_terms("orders", DEMO_TERMS["orders"])
    attrs_before = len(svc.store.attributes_for_datasource(svc.ds()["id"]))
    assert attrs_before

    out = svc.scan_datasource(tables=["customers"])
    assert out["table_filter_source"] == "argument"
    assert names(svc) == {"orders", "order_lines", "customers"}
    assert out["profiled_tables"] == 1  # earlier areas are not re-profiled
    assert len(svc.store.attributes_for_datasource(svc.ds()["id"])) == attrs_before
    rels = svc.list_relationships(status="all")["relationships"]
    assert any(r["source"] == "orders.customer_id" and r["target"] == "customers.customer_id" for r in rels)


def test_datasource_filter_beats_env_and_star_reads_all(settings, demo_path):
    svc = OntologyService(replace(settings, scan_tables=("orders",)))
    out = svc.add_datasource("demo", f"sqlite:///{demo_path}", tables=["regions", "states"])
    assert out["scan"]["table_filter_source"] == "datasource"
    assert names(svc) == {"regions", "states"}
    assert svc.scan_datasource()["table_filter_source"] == "datasource"
    assert names(svc) == {"regions", "states"}

    out = svc.scan_datasource(tables=["*"])
    assert "table_filter" not in out
    assert {"orders", "customers", "regions"} <= names(svc)


def test_dropped_table_is_removed_only_when_the_filter_covers_it(settings, demo_path):
    svc = OntologyService(settings)
    svc.add_datasource("demo", f"sqlite:///{demo_path}")
    con = sqlite3.connect(demo_path)
    con.execute("DROP TABLE product_tags")
    con.commit()
    con.close()

    svc.scan_datasource(tables=["orders"])
    assert "product_tags" in names(svc)  # outside the filter: not looked at, kept
    svc.scan_datasource(tables=["product_*"])
    assert "product_tags" not in names(svc)
    assert "product_categories" in names(svc)


def test_filter_matching_nothing_warns(svc):
    out = svc.scan_datasource(tables=["NO_SUCH_*"])
    assert any("matched no table" in w for w in out["warnings"])
    assert "orders" in names(svc)


def test_knowledge_store_ignores_the_env_filter(settings):
    svc = OntologyService(replace(settings, scan_tables=("NO_SUCH_*",)))
    svc.knowledge.define_entity("Book", [{"name": "isbn", "type": "text"}, {"name": "title", "type": "text"}], keys=["isbn"])
    assert any(t["name"] == "book" for t in svc.store.tables(svc.ds("knowledge")["id"]))
    assert svc._table_filter(svc.ds("knowledge"), None) == (None, None)


def test_re_adding_a_datasource_keeps_its_saved_filter(settings, demo_path):
    """Review of #14: re-adding without tables (e.g. to change the password) reset the filter to every table."""
    svc = OntologyService(settings)
    url = f"sqlite:///{demo_path}"
    svc.add_datasource("demo", url, tables=["regions", "states"])
    out = svc.add_datasource("demo", url, description="new password")
    assert svc.ds()["table_filter"] == ["regions", "states"]
    assert out["scan"]["table_filter_source"] == "datasource"
    assert names(svc) == {"regions", "states"}

    out = svc.add_datasource("demo", url, tables=["*"])  # the explicit way to read everything again
    assert "table_filter" not in out["scan"]
    assert {"orders", "customers", "regions"} <= names(svc)
