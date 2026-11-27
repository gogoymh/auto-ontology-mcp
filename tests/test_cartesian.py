"""Sources read side by side with nothing linking them: every row pairs with every row."""

from __future__ import annotations

import pytest

from auto_ontology_mcp.sqlcheck import guard_readonly
from auto_ontology_mcp.structure import cartesian_products


def warnings_for(sql: str) -> list[str]:
    return cartesian_products(guard_readonly(sql, "sqlite"))


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT COUNT(*) FROM orders, customers",
        "SELECT COUNT(*) FROM orders CROSS JOIN customers",
        "SELECT COUNT(*) FROM orders JOIN customers",  # a JOIN with no ON is a cross join
        "SELECT COUNT(*) FROM orders JOIN customers ON 1 = 1",
    ],
)
def test_a_query_that_pairs_every_row_is_reported(sql):
    warnings = warnings_for(sql)
    assert len(warnings) == 1
    assert "cartesian product" in warnings[0]
    assert "orders" in warnings[0] and "customers" in warnings[0]


@pytest.mark.parametrize(
    "sql",
    [
        # one table joined to itself twice pairs both halves
        "SELECT COUNT(*) FROM orders o1 JOIN orders o2",
        "SELECT COUNT(*) FROM orders o1, orders o2",
        "SELECT COUNT(*) FROM orders o1, orders o2, customers c WHERE o1.customer_id = c.customer_id",
    ],
)
def test_two_aliases_of_one_table_are_two_sources(sql):
    warnings = warnings_for(sql)
    assert len(warnings) == 1
    assert "orders AS o2" in warnings[0]


def test_a_self_join_with_a_predicate_is_linked():
    assert warnings_for("SELECT COUNT(*) FROM orders o1 JOIN orders o2 ON o1.customer_id = o2.customer_id") == []


def test_a_where_clause_joins_as_much_as_an_on():
    assert warnings_for("SELECT COUNT(*) FROM orders, customers WHERE orders.customer_id = customers.customer_id") == []


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM orders JOIN customers ON orders.customer_id = customers.customer_id",
        "SELECT * FROM order_lines JOIN orders USING (order_id)",
        "SELECT * FROM orders NATURAL JOIN customers",
        "SELECT * FROM orders o JOIN customers c ON o.customer_id = c.customer_id AND o.status = o.channel",
        # a range join ties the rows as much as an equality
        "SELECT * FROM products p JOIN order_lines l ON p.unit_price > l.unit_price",
        "SELECT * FROM orders o JOIN customers c ON o.order_date BETWEEN c.signup_date AND c.signup_date",
        # a join on an expression of one table's column against another's still links the tables
        "SELECT COUNT(*) FROM orders o JOIN customers c ON DATE(o.order_date) = DATE(c.signup_date)",
        "SELECT COUNT(*) FROM orders o JOIN customers c ON o.customer_id + 0 = c.customer_id",
        "SELECT COUNT(*) FROM orders o JOIN customers c ON o.order_date >= DATE(c.signup_date, '+7 days')",
        "SELECT COUNT(*) FROM orders o JOIN customers c ON o.customer_id IN (c.customer_id)",
    ],
)
def test_sources_tied_by_a_predicate_are_not_reported(sql):
    assert warnings_for(sql) == []


def test_a_table_function_is_not_a_source():
    sql = "SELECT COUNT(*) FROM orders o, json_each(o.tags) t WHERE t.value = 'x'"
    assert warnings_for(sql) == []


def test_a_table_outside_the_join_is_named():
    warnings = warnings_for("SELECT * FROM order_lines l JOIN orders o ON l.order_id = o.order_id, products p")
    assert len(warnings) == 1
    assert "Table products AS p appears" in warnings[0]
    assert "order_lines" not in warnings[0]


def test_the_on_clause_that_binds_one_table_is_named():
    warnings = warnings_for("SELECT COUNT(*) FROM orders o JOIN customers c ON o.status = o.channel")
    assert len(warnings) == 1
    assert "compares only columns of orders AS o" in warnings[0]
    assert "customers AS c" in warnings[0]


def test_a_correlated_subquery_is_checked_in_its_own_scope():
    sql = (
        "SELECT COUNT(*) FROM orders o JOIN customers c ON o.customer_id = c.customer_id "
        "WHERE EXISTS (SELECT 1 FROM cities WHERE cities.city_id = o.customer_id)"
    )
    assert warnings_for(sql) == []


def test_a_paired_subquery_is_checked_in_its_own_scope():
    sql = "SELECT * FROM (SELECT * FROM cities, states) pair JOIN orders ON orders.customer_id = pair.city_id"
    warnings = warnings_for(sql)
    assert len(warnings) == 1
    assert "cities" in warnings[0] and "states" in warnings[0]


def test_each_union_branch_is_checked():
    warnings = warnings_for("SELECT city_id FROM cities, states UNION ALL SELECT region_id FROM regions, tags")
    assert len(warnings) == 2


def test_validate_sql_reports_it(modeled):
    check = modeled.validate_sql("SELECT COUNT(*) FROM regions, states")
    assert check["valid"] is True
    assert any("cartesian product" in w for w in check["warnings"])


def test_validate_sql_suggests_a_known_join(modeled):
    check = modeled.validate_sql("SELECT COUNT(*) FROM regions, states")
    warning = next(w for w in check["warnings"] if "cartesian product" in w)
    assert "for example states.region_id = regions.region_id" in warning


def test_validate_sql_does_not_invent_a_join(modeled):
    check = modeled.validate_sql("SELECT COUNT(*) FROM tags, product_categories")
    warning = next(w for w in check["warnings"] if "cartesian product" in w)
    assert "for example" not in warning
    assert "product_categories" in warning


def test_validate_sql_stays_quiet_when_a_predicate_links_the_tables(modeled):
    check = modeled.validate_sql(
        "SELECT COUNT(*) FROM orders, customers WHERE orders.customer_id = customers.customer_id"
    )
    assert not any("cartesian product" in w for w in check["warnings"])

@pytest.mark.parametrize(
    "sql",
    [
        # a bridge through a derived table or a CTE links the physical tables it joins
        """SELECT COUNT(*) FROM orders o
           JOIN (SELECT customer_id FROM customers GROUP BY customer_id) t ON t.customer_id = o.customer_id
           JOIN customers c ON c.customer_id = t.customer_id""",
        """WITH t AS (SELECT customer_id FROM customers)
           SELECT COUNT(*) FROM orders o, t, customers c
           WHERE o.customer_id = t.customer_id AND t.customer_id = c.customer_id""",
        # any operator or function between two sources counts, not a list of comparison types
        "SELECT * FROM orders o JOIN customers c ON o.customer_id IS NOT DISTINCT FROM c.customer_id",
        "SELECT * FROM orders o JOIN customers c ON o.status LIKE c.customer_name || '%'",
        "SELECT * FROM orders o JOIN customers c ON o.status GLOB c.customer_name",
        "SELECT * FROM orders o JOIN customers c ON instr(o.status, c.customer_name) > 0",
        # a mixed side: columns of two tables on one side of the comparison
        """SELECT * FROM orders o
           JOIN order_lines l ON l.order_id = o.order_id
           JOIN customers c ON o.order_id + l.order_line_id = c.customer_id""",
    ],
)
def test_a_link_through_a_bridge_or_an_expression_is_not_reported(sql):
    assert warnings_for(sql) == []


def test_a_derived_table_on_its_own_is_not_reported():
    assert warnings_for("SELECT COUNT(*) FROM orders o, (SELECT COUNT(*) AS n FROM customers) t") == []


def test_a_pair_tied_only_inside_exists_is_still_reported():
    sql = (
        "SELECT COUNT(*) FROM orders o, customers c "
        "WHERE EXISTS (SELECT 1 FROM cities WHERE cities.city_id = o.customer_id AND cities.city_id = c.customer_id)"
    )
    warnings = warnings_for(sql)
    assert len(warnings) == 1
    assert "cartesian product" in warnings[0]


def test_a_mysql_null_safe_join_is_a_link():
    """`<=>` parses as IS NOT DISTINCT FROM, and the conjunct rule does not care which it is."""
    sql = "SELECT * FROM orders o JOIN customers c ON o.customer_id <=> c.customer_id"
    assert cartesian_products(guard_readonly(sql, "mysql")) == []


@pytest.mark.parametrize(
    "sql",
    [
        """SELECT COUNT(*) FROM orders o
           JOIN (SELECT customer_id FROM customers) t ON t.customer_id = o.customer_id
           JOIN products p ON t.customer_id > 0""",
        """WITH t AS (SELECT customer_id FROM customers)
           SELECT COUNT(*) FROM orders o JOIN t ON t.customer_id = o.customer_id
           JOIN products p ON t.customer_id > 0""",
    ],
)
def test_a_lonely_table_whose_on_binds_a_cte_or_derived_table_is_named(sql):
    """The owner of that ON has a name but no table label, and the message must still be built."""
    warnings = warnings_for(sql)
    assert len(warnings) == 1
    assert "compares only columns of t" in warnings[0]
