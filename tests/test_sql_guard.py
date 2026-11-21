from __future__ import annotations

import pytest

from auto_ontology_mcp.datasources import _MAX_VALUE_CHARS
from auto_ontology_mcp.service import OntologyError
from auto_ontology_mcp.sqlcheck import SqlGuardError, extract_expressions, guard_readonly, guard_sources


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM orders",
        "UPDATE orders SET status = 'x'",
        "INSERT INTO tags VALUES (99, 'x')",
        "DROP TABLE orders",
        "SELECT 1; DELETE FROM orders",
        "SELECT 1; ; DELETE FROM orders",
        "PRAGMA table_info(orders)",
        "ATTACH DATABASE '/tmp/x.db' AS x",
        "CREATE TABLE t AS SELECT 1",
        "SELECT * INTO new_orders FROM orders",
        "SELECT load_extension('evil')",
        "WITH d AS (DELETE FROM orders RETURNING *) SELECT * FROM d",
        "SELECT * FROM orders FOR UPDATE",
    ],
)
def test_guard_rejects_writes(sql):
    dialect = "postgres" if "RETURNING" in sql or "INTO new" in sql or "FOR UPDATE" in sql else "sqlite"
    with pytest.raises(SqlGuardError):
        guard_readonly(sql, dialect)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "SELECT o.status, COUNT(*) FROM orders o GROUP BY 1",
        "WITH x AS (SELECT * FROM orders) SELECT COUNT(*) FROM x",
        "SELECT 1 UNION ALL SELECT 2",
        "SELECT 1; /* done */",
    ],
)
def test_guard_accepts_reads(sql):
    guard_readonly(sql, "sqlite")


@pytest.mark.parametrize(
    "dialect,sql",
    [
        ("mysql", "SELECT LOAD_FILE('/etc/passwd')"),
        ("postgres", "SELECT pg_write_file('/tmp/x', 'y')"),
        ("postgres", "SELECT lo_unlink(1)"),
        ("postgres", "SELECT dblink_send_query('c', 'x')"),
        ("duckdb", "SELECT * FROM query('select 1')"),
        ("postgres", "COPY (SELECT 1) TO PROGRAM 'id'"),
        ("postgres", "EXPLAIN ANALYZE DELETE FROM orders"),
        ("sqlite", "SELECT zeroblob(5000000000) FROM orders"),
        ("sqlite", "SELECT randomblob(2000000000) FROM orders"),
    ],
)
def test_guard_rejects_dangerous_functions(dialect, sql):
    with pytest.raises(SqlGuardError):
        guard_readonly(sql, dialect)


def test_run_sql_truncates_oversized_values(svc):
    marker = f"... [{200000:,} chars truncated]"
    cell = svc.run_sql("SELECT printf('%.*c', 200000, 'x') AS v FROM orders LIMIT 1")["rows"][0][0]
    assert cell[:_MAX_VALUE_CHARS] == "x" * _MAX_VALUE_CHARS
    assert cell.endswith(marker)
    assert len(cell) == _MAX_VALUE_CHARS + len(marker)
    status = svc.run_sql("SELECT status FROM orders LIMIT 1")["rows"][0][0]
    assert status in {"Delivered", "Shipped", "Placed", "Cancelled", "Returned"}


@pytest.mark.parametrize(
    "dialect,sql",
    [
        ("duckdb", "SELECT * FROM '/etc/passwd'"),
        ("duckdb", "SELECT * FROM sqlite_scan('/x.db', 't')"),
        ("tsql", "SELECT * FROM OPENQUERY(srv, 'select 1')"),
        ("postgres", "SELECT * FROM generate_series(1, 3) g"),
        ("sqlite", "SELECT * FROM sqlite_master"),
        ("sqlite", "SELECT * FROM orders o JOIN secrets s ON s.id = o.order_id"),
    ],
)
def test_sources_must_be_cataloged_tables(dialect, sql):
    # Blocked by either layer: the function deny-list or the catalog allow-list.
    with pytest.raises(SqlGuardError):
        guard_sources(guard_readonly(sql, dialect), [("", "orders"), ("", "customers")])


def test_ctes_subqueries_and_values_are_allowed():
    sql = (
        "WITH x AS (SELECT customer_id FROM orders) SELECT * FROM x "
        "JOIN (SELECT * FROM customers) c ON c.customer_id = x.customer_id"
    )
    guard_sources(guard_readonly(sql, "sqlite"), [("", "orders"), ("", "customers")])
    guard_sources(guard_readonly("SELECT * FROM (VALUES (1), (2)) AS v(x)", "postgres"), [])


def test_run_sql_refuses_uncataloged_sources(svc):
    with pytest.raises(OntologyError) as exc:
        svc.run_sql("SELECT name, sql FROM sqlite_master")
    assert "not in the catalog" in str(exc.value)


def test_run_sql_sends_the_first_statement_to_the_driver(svc, monkeypatch):
    real = svc.connections.run_readonly
    seen: list[str] = []

    def capture(ds, sql, *, max_rows=None, params=None):
        seen.append(sql)
        return real(ds, sql, max_rows=max_rows)

    monkeypatch.setattr(svc.connections, "run_readonly", capture)
    result = svc.run_sql("SELECT COUNT(*) AS n FROM orders; /* done */")
    assert seen == ["SELECT COUNT(*) AS n FROM orders"]
    assert result["rows"] == [[3200]]


def test_run_sql_refuses_writes_and_data_is_untouched(svc):
    with pytest.raises(OntologyError):
        svc.run_sql("DELETE FROM orders")
    assert svc.run_sql("SELECT COUNT(*) AS n FROM orders")["rows"] == [[3200]]


def test_run_sql_handles_colons_inside_string_literals(svc):
    assert svc.run_sql("SELECT 'a :bar b' AS v FROM orders LIMIT 1")["rows"] == [["a :bar b"]]
    assert svc.run_sql("SELECT '{\"a\":1}' AS v FROM orders LIMIT 1")["rows"] == [['{"a":1}']]


def test_run_sql_handles_percent_signs(svc):
    rows = svc.run_sql("SELECT COUNT(*) AS n FROM orders WHERE status LIKE '%live%'")["rows"]
    assert isinstance(rows[0][0], int) and rows[0][0] > 0
    assert svc.run_sql("SELECT 7 % 3 AS r FROM orders LIMIT 1")["rows"] == [[1]]


def test_connection_itself_is_read_only(svc):
    ds = svc.ds()
    with pytest.raises(Exception) as exc:
        svc.connections.run_readonly(ds, "DELETE FROM orders")
    assert "readonly" in str(exc.value).lower() or "read-only" in str(exc.value).lower()


def test_extract_expressions_mines_business_logic():
    exprs = extract_expressions(
        "SELECT SUM(l.line_total) FROM order_lines l JOIN orders o ON o.order_id = l.order_id "
        "WHERE o.status = 'Delivered' AND l.discount_pct > 0.1",
        "sqlite",
    )
    assert "o.status = 'Delivered'" in exprs
    assert "SUM(l.line_total)" in exprs
    assert not any(e == "o.order_id = l.order_id" for e in exprs)


def test_validate_sql_checks_literals_through_a_union(svc):
    plain = svc.validate_sql("SELECT COUNT(*) AS n FROM orders WHERE status = 'Delivered '")
    assert plain["warnings"] == ["No row has orders.status = 'Delivered '. Close real values: 'Delivered'."]
    cte = svc.validate_sql(
        "WITH u AS (SELECT status FROM orders UNION ALL SELECT status FROM orders) "
        "SELECT COUNT(*) AS n FROM u WHERE status = 'Delivered '"
    )
    derived = svc.validate_sql(
        "SELECT COUNT(*) AS n FROM (SELECT status FROM orders UNION ALL SELECT status FROM orders) u "
        "WHERE status = 'Delivered '"
    )
    assert cte["warnings"] == derived["warnings"] == plain["warnings"]
    # Branches that resolve to different physical columns stay unchecked rather than guessed.
    mixed = svc.validate_sql(
        "SELECT COUNT(*) AS n FROM (SELECT customer_name AS d FROM customers UNION ALL SELECT segment AS d "
        "FROM customers) u WHERE d = 'Zzzz nothing'"
    )
    assert not mixed["warnings"] and not mixed["errors"]


def test_validate_sql_traces_nested_unions(svc):
    plain = svc.validate_sql("SELECT COUNT(*) AS n FROM orders WHERE status = 'Delivered '")
    three = svc.validate_sql(
        "WITH u AS (SELECT status FROM orders UNION ALL SELECT status FROM orders "
        "UNION ALL SELECT status FROM orders) SELECT COUNT(*) AS n FROM u WHERE status = 'Delivered '"
    )
    assert three["warnings"] == plain["warnings"]


def test_validate_sql_matches_union_branches_by_position(svc):
    swapped = svc.validate_sql(
        "SELECT s, c FROM (SELECT status AS s, channel AS c FROM orders "
        "UNION ALL SELECT channel AS c, status AS s FROM orders) u WHERE s = 'zzz-no-match'"
    )
    assert not swapped["warnings"] and not swapped["errors"]
