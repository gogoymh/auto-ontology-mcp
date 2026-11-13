"""Oracle support.

The unit tests at the top need no database. The integration test runs only when
AO_TEST_ORACLE_URL points at a disposable user (its demo tables are dropped and recreated), e.g.

    docker run -d --rm --name aomcp-ora -e ORACLE_PASSWORD=pw -e APP_USER=ao -e APP_USER_PASSWORD=pw \
        -p 1521:1521 gvenzl/oracle-free
    AO_TEST_ORACLE_URL="oracle+oracledb://ao:pw@127.0.0.1:1521/?service_name=FREEPDB1" \
        uv run --extra oracle pytest tests/test_oracle.py
"""

from __future__ import annotations

import os
import sqlite3

import pytest
import sqlalchemy as sa
import sqlglot
from sqlalchemy.dialects.oracle.base import OracleDialect
from sqlalchemy.dialects.postgresql.base import PGDialect
from sqlglot import exp

from auto_ontology_mcp.datasources import (
    builtin_columns,
    builtin_sources,
    is_oracle,
    native_name,
    normalize_url,
    ping_sql,
    scans_default_schema_only,
)
from auto_ontology_mcp.service import OntologyError, OntologyService
from auto_ontology_mcp.sqlcheck import SqlGuardError, guard_readonly, guard_sources
from auto_ontology_mcp.validation import Validator, limited

from conftest import DEMO_TERMS

ORA_URL = os.environ.get("AO_TEST_ORACLE_URL")
ORDER = ["regions", "states", "cities", "employees", "customers", "product_categories", "products", "tags", "product_tags", "orders", "order_lines"]


# ------------------------------------------------------------------ no database


def test_bare_oracle_url_uses_oracledb():
    assert normalize_url("oracle://u:p@h:1521/?service_name=x").startswith("oracle+oracledb://")
    assert normalize_url("oracle+cx_oracle://u:p@h:1521/?service_name=x").startswith("oracle+cx_oracle://")


def test_backend_helpers():
    assert is_oracle("oracle") and is_oracle("oracle+oracledb") and is_oracle("ORACLE") and not is_oracle(None)
    assert ping_sql("oracle") == "SELECT 1 FROM DUAL"
    assert ping_sql("postgresql") == "SELECT 1"
    assert scans_default_schema_only("oracle") and not scans_default_schema_only("postgresql")
    assert ("", "DUAL") in builtin_sources("oracle") and builtin_sources("sqlite") == []


def test_native_name_restores_oracle_spelling():
    d = OracleDialect()
    assert native_name(d, d.normalize_name("CUSTOMERS")) == "CUSTOMERS"
    assert native_name(d, d.normalize_name("MixedCase")) == "MixedCase"
    assert native_name(d, d.normalize_name("lower_quoted")) == "lower_quoted"
    assert native_name(d, "hr") == "HR"  # a schema typed by the user
    assert native_name(d, None) is None
    assert native_name(PGDialect(), "customers") == "customers"


def test_native_names_are_oracle_only():
    pg = PGDialect()
    pg.requires_name_normalize = True  # Snowflake and DB2 normalize names too; they must stay as before
    assert native_name(pg, "customers") == "customers"


def test_rescan_on_a_name_normalizing_dialect_keeps_reviewed_work(svc):
    """Review of #14: a lowercase catalog on a dialect that normalizes names (Snowflake, DB2) was renamed
    to upper case on rescan, which deleted the old tables and, by cascade, every column mapping."""
    for table in ("customers", "orders"):
        svc.save_table_terms(table, DEMO_TERMS[table])
    ds = svc.ds()
    mappings = len(svc.store.attributes_for_datasource(ds["id"]))
    linked = {t["name"]: t["primary_table_id"] for t in svc.store.terms(ds["id"])}
    assert mappings and all(linked.values())

    svc.connections.engine(ds).dialect.requires_name_normalize = True
    out = svc.scan_datasource()
    assert not out["warnings"]
    assert {"customers", "orders"} <= {t["name"] for t in svc.store.tables(ds["id"])}
    assert len(svc.store.attributes_for_datasource(ds["id"])) == mappings
    assert {t["name"]: t["primary_table_id"] for t in svc.store.terms(ds["id"])} == linked


@pytest.mark.parametrize("dialect", ["sqlite", "postgres"])
def test_currval_and_nextval_are_ordinary_columns_elsewhere(dialect):
    sql = "SELECT t.currval, t.nextval FROM customers t"
    guard_sources(guard_readonly(sql, dialect), [("", "customers")])
    with pytest.raises(SqlGuardError, match="Sequence"):
        guard_readonly(sql, "oracle")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT dbms_xmlgen.getxml('select * from all_users') FROM orders",
        "SELECT sys.dbms_xmlgen.getxml('select 1 from dual') FROM orders",
        "SELECT ctxsys.drithsx.sn(1, 'x') FROM orders",
        "SELECT utl_http.request('http://example.com') FROM orders",
        "SELECT httpuritype('http://example.com').getclob() FROM orders",
        "SELECT xmltype(bfilename('DIR', 'f.xml')) FROM orders",
        "SELECT order_seq.nextval FROM orders",
        "SELECT utl_inaddr.get_host_name FROM dual",  # package function without parentheses
        "SELECT sys.dbms_xmlgen.getxml FROM dual",
        "SELECT o.status, dbms_session.unique_session_id FROM orders o",
        "SELECT * FROM orders FOR UPDATE",
        "DELETE FROM orders",
        "BEGIN NULL; END;",
    ],
)
def test_oracle_guard_rejects(sql):
    with pytest.raises(SqlGuardError):
        guard_sources(guard_readonly(sql, "oracle"), [("", "ORDERS"), *builtin_sources("oracle")])


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT SYSDATE FROM DUAL",
        "SELECT o.status, COUNT(*) FROM orders o GROUP BY o.status FETCH FIRST 5 ROWS ONLY",
        "SELECT * FROM orders WHERE ROWNUM <= 5",
        "SELECT NVL(o.status, 'x'), TO_CHAR(o.order_date) FROM orders o",
        "SELECT utl_x.status FROM orders utl_x",  # an alias, not a package
        "WITH dbms_x AS (SELECT status FROM orders) SELECT dbms_x.status FROM dbms_x",
    ],
)
def test_oracle_guard_accepts_reads(sql):
    guard_sources(guard_readonly(sql, "oracle"), [("", "ORDERS"), *builtin_sources("oracle")])


@pytest.mark.parametrize(
    "dialect,sql",
    [
        ("mysql", "SELECT sys.format_bytes(1) FROM orders"),
        ("tsql", "SELECT sys.fn_varbintohexstr(o.status) FROM orders o"),
        ("postgres", "SELECT xdb.helper(1) FROM orders"),
    ],
)
def test_oracle_owner_list_applies_to_oracle_only(dialect, sql):
    """Review of #14: sys.* is a harmless helper schema on MySQL and SQL Server."""
    guard_sources(guard_readonly(sql, dialect), [("", "orders")])
    with pytest.raises(SqlGuardError, match="Calls into"):
        guard_readonly(sql, "oracle")


def _oracle_validator(seen: list[str]) -> Validator:
    columns = [
        {"id": f"c{i}", "table_id": f"t{t}", "schema_name": "", "table_name": tn, "name": cn, "data_type": "NUMBER"}
        for i, (t, tn, cn) in enumerate(
            [(1, "ORDERS", "ORDER_ID"), (1, "ORDERS", "CUSTOMER_ID"), (1, "ORDERS", "STATUS"), (2, "CUSTOMERS", "CUSTOMER_ID")]
        )
    ] + builtin_columns("oracle")

    def run(sql: str) -> list[list]:
        seen.append(sql)
        return [[0, 0]]

    return Validator(dialect="oracle", catalog_columns=columns, relationships=[], attributes=[], sql_attributes=[], run=run)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT o.customer_id, c.customer_id FROM orders o JOIN customers c ON c.customer_id = o.customer_id",
        "SELECT o.*, c.* FROM orders o JOIN customers c ON c.customer_id = o.customer_id",
        "SELECT order_id FROM orders UNION SELECT customer_id FROM customers ORDER BY 1",
    ],
)
def test_oracle_dry_run_does_not_wrap_the_query(sql):
    """Review of #14: SELECT * FROM (<query>) aoq raises ORA-00918 when two output columns share a name."""
    seen: list[str] = []
    _oracle_validator(seen).validate(sql)
    assert seen, "no dry run"
    dry = seen[-1]
    assert "aoq" not in dry and dry.endswith("FETCH FIRST 0 ROWS ONLY")
    assert limited(sqlglot.parse_one(sql, read="oracle"), 1, "oracle").sql(dialect="oracle").endswith("FETCH FIRST 1 ROWS ONLY")
    assert "aoq" in limited(sqlglot.parse_one(sql), 1, "postgres").sql(dialect="postgres")  # other dialects as before


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT USER FROM dual",
        "SELECT dummy FROM dual",
        "SELECT SYSDATE, USER, UID FROM DUAL",
        "SELECT * FROM orders WHERE ROWNUM <= 5",
        "SELECT o.status, LEVEL FROM orders o",
    ],
)
def test_dual_and_pseudo_columns_validate(sql):
    """Review of #14: DUAL had no columns, so SELECT USER FROM dual came back valid=false."""
    out = _oracle_validator([]).validate(sql)
    assert out["valid"], out["errors"]


def test_generated_probes_are_valid_oracle():
    wrapped = exp.select("*").from_(sqlglot.parse_one("SELECT a FROM t").subquery("aoq")).limit(0).sql(dialect="oracle")
    assert "LIMIT" not in wrapped and "FETCH FIRST 0 ROWS ONLY" in wrapped
    assert '"_' not in wrapped and " _" not in wrapped
    probe = sqlglot.transpile('SELECT * FROM "ORDERS" LIMIT 10', read="postgres", write="oracle")[0]
    assert probe == 'SELECT * FROM "ORDERS" FETCH FIRST 10 ROWS ONLY'


# ------------------------------------------------------------------- live Oracle

_TYPES = {"INTEGER": "NUMBER(19)", "REAL": "NUMBER(14,4)", "TEXT": "VARCHAR2(400)"}


@pytest.fixture()
def ora_svc(settings, demo_path):
    if not ORA_URL:
        pytest.skip("set AO_TEST_ORACLE_URL to run Oracle tests")
    pytest.importorskip("oracledb")
    src = sqlite3.connect(demo_path)
    engine = sa.create_engine(normalize_url(ORA_URL))
    with engine.begin() as con:
        for name in reversed(ORDER):
            con.exec_driver_sql(
                f"BEGIN EXECUTE IMMEDIATE 'DROP TABLE {name} CASCADE CONSTRAINTS'; "
                "EXCEPTION WHEN OTHERS THEN IF SQLCODE != -942 THEN RAISE; END IF; END;"
            )
        con.exec_driver_sql(
            "BEGIN EXECUTE IMMEDIATE 'DROP SEQUENCE ao_test_seq'; EXCEPTION WHEN OTHERS THEN NULL; END;"
        )
        con.exec_driver_sql("CREATE SEQUENCE ao_test_seq")
        for name in ORDER:
            info = src.execute(f"PRAGMA table_info({name})").fetchall()
            cols = [f"{c[1]} {_TYPES.get((c[2] or 'TEXT').upper(), 'VARCHAR2(400)')}" for c in info]
            pk = [c[1] for c in sorted(info, key=lambda c: c[5]) if c[5]]
            if pk:
                cols.append(f"PRIMARY KEY ({', '.join(pk)})")
            for fk in src.execute(f"PRAGMA foreign_key_list({name})").fetchall():
                cols.append(f"FOREIGN KEY ({fk[3]}) REFERENCES {fk[2]}({fk[4]})")
            con.exec_driver_sql(f"CREATE TABLE {name} ({', '.join(cols)})")
            rows = src.execute(f"SELECT * FROM {name}").fetchall()
            if rows:
                binds = ", ".join(f":{i + 1}" for i in range(len(rows[0])))
                con.exec_driver_sql(f"INSERT INTO {name} VALUES ({binds})", [tuple(r) for r in rows])
    engine.dispose()
    svc = OntologyService(settings)
    svc.add_datasource("shop", ORA_URL)
    return svc


def test_oracle_scan_and_query(ora_svc):
    ds = ora_svc.ds()
    assert ds["dialect"] == "oracle"
    tables = {t["name"] for t in ora_svc.store.tables(ds["id"])}
    assert {"ORDERS", "CUSTOMERS", "ORDER_LINES"} <= tables
    cols = ora_svc.store.columns_for_datasource(ds["id"])
    assert any(c["name"] == "STATUS" and c.get("sample_values") for c in cols), "profiling did not run"

    rels = ora_svc.list_relationships(status="all")["relationships"]
    assert any(
        r["source"].casefold().endswith("orders.customer_id") and r["target"].casefold().endswith("customers.customer_id")
        and r["status"] == "accepted"
        for r in rels
    )

    sql = "SELECT o.status, COUNT(*) AS n FROM orders o WHERE o.status = 'delivered' GROUP BY o.status"
    warnings = " ".join(ora_svc.validate_sql(sql)["warnings"])
    assert "'Delivered'" in warnings
    out = ora_svc.run_sql(sql.replace("'delivered'", "'Delivered'"))
    assert out["row_count"] == 1 and out["columns"] == ["STATUS", "N"]
    assert ora_svc.run_sql("SELECT SYSDATE FROM DUAL")["row_count"] == 1


def test_oracle_is_read_only(ora_svc):
    for bad in (
        "DELETE FROM orders",
        "SELECT dbms_xmlgen.getxml('select 1 from dual') FROM orders",
        "SELECT ao_test_seq.nextval FROM orders",
        "SELECT utl_inaddr.get_host_name FROM dual",
        "SELECT httpuritype('http://example.com').getclob() FROM DUAL",
    ):
        with pytest.raises(OntologyError):
            ora_svc.run_sql(bad)
    with pytest.raises(Exception, match="ORA-01456"):
        ora_svc.connections.run_readonly(ora_svc.ds(), "UPDATE orders SET status = status")
