"""Checks on the shape of a query that need no probe: only the parsed SQL and the catalog.

Ported from NVIDIA Auto Ontology (Apache-2.0), where they run between SQL generation and
execution: ``retrieval/text_to_sql/db_probe/literal_check.py`` (integer division) and
``retrieval/text_to_sql/agents/sql_parse_validation.py`` (placeholder queries, a GROUP BY that
cannot group, a many-side column selected next to an aggregate, and unquoted mixed-case
identifiers). Auto Ontology rewrites the SQL itself where it can; here the model wrote the SQL,
so each finding is returned to it with the corrected spelling.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from sqlglot import exp
from sqlglot.optimizer.scope import Scope, traverse_scope

from .sqlcheck import PhysicalColumn, _coarse_type, _trace

# Dialects where integer / integer drops the fraction. MySQL, DuckDB, Snowflake, BigQuery,
# ClickHouse and Oracle return a fractional result.
TRUNCATING_DIALECTS = {"sqlite", "postgres", "redshift", "tsql", "trino", "presto"}
# Dialects that fold an unquoted identifier to lowercase and compare quoted ones exactly.
# Snowflake and Oracle fold to uppercase, but SQLAlchemy reports their case-insensitive names
# in lowercase, so the stored spelling cannot be told from the catalog and they are left alone.
FOLDS_LOWER = {"postgres", "redshift"}

Lookup = Callable[[PhysicalColumn], dict[str, Any] | None]


def _scopes(tree: exp.Expression) -> list[Scope]:
    try:
        return list(traverse_scope(tree))
    except Exception:  # noqa: BLE001
        return []


# ----------------------------------------------------------- integer division
def _is_integer(node: exp.Expression | None, scope: Scope, lookup: Lookup) -> bool:
    """Whether *node* is known to be an integer. Anything not recognized counts as not known."""
    if node is None:
        return False
    if isinstance(node, (exp.Paren, exp.Neg)):
        return _is_integer(node.this, scope, lookup)
    if isinstance(node, exp.Literal):
        return not node.is_string and not any(c in node.this for c in ".eE")
    if isinstance(node, exp.Count):
        return True
    if isinstance(node, (exp.Sum, exp.Min, exp.Max, exp.Abs)):
        arg = node.this.expressions[0] if isinstance(node.this, exp.Distinct) and node.this.expressions else node.this
        return _is_integer(arg, scope, lookup)
    if isinstance(node, (exp.Add, exp.Sub, exp.Mul, exp.Mod, exp.Div, exp.IntDiv)):
        return _is_integer(node.left, scope, lookup) and _is_integer(node.right, scope, lookup)
    if isinstance(node, exp.Cast):
        return _coarse_type(node.to.sql()) == "BIGINT"
    if isinstance(node, exp.Coalesce):
        return all(_is_integer(e, scope, lookup) for e in [node.this, *node.expressions])
    if isinstance(node, exp.Case):
        results = [i.args.get("true") for i in node.args.get("ifs") or []]
        if node.args.get("default") is not None:
            results.append(node.args["default"])
        return bool(results) and all(_is_integer(r, scope, lookup) for r in results)
    if isinstance(node, exp.Column):
        phys = _trace(scope, node)
        col = lookup(phys) if phys else None
        return bool(col) and _coarse_type(col.get("data_type")) == "BIGINT"
    return False


def integer_divisions(tree: exp.Expression, dialect: str | None, lookup: Lookup) -> list[str]:
    """``a / b`` where both sides are integers and nothing casts either one: the fraction is dropped."""
    if (dialect or "").lower() not in TRUNCATING_DIALECTS:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for scope in _scopes(tree):
        for div in scope.find_all(exp.Div):
            if isinstance(div.parent, exp.Div) and div.parent.left is div:
                continue  # a / b / c: reported once, at the outer division
            if not (_is_integer(div.left, scope, lookup) and _is_integer(div.right, scope, lookup)):
                continue
            text = div.sql(dialect=dialect)
            if text in seen:
                continue
            seen.add(text)
            out.append(
                f"Integer division: both sides of {text} are integers, so the result is truncated to a whole "
                f"number (7 / 2 = 3). If the question wants a fraction, ratio, percentage or average, cast one "
                f"side: CAST({div.left.sql(dialect=dialect)} AS REAL) / {div.right.sql(dialect=dialect)}."
            )
    return out


# --------------------------------------------------------- placeholder queries
def _always_false(node: exp.Expression | None) -> bool:
    while isinstance(node, exp.Paren):
        node = node.this
    if isinstance(node, exp.Boolean):
        return node.this is False
    if isinstance(node, exp.Literal) and not node.is_string:
        return node.this in ("0", "0.0")
    if isinstance(node, (exp.EQ, exp.NEQ)) and isinstance(node.left, exp.Literal) and isinstance(node.right, exp.Literal):
        same = node.left.this == node.right.this and node.left.is_string == node.right.is_string
        return (not same) if isinstance(node, exp.EQ) else same
    if isinstance(node, exp.And):
        return _always_false(node.left) or _always_false(node.right)
    return False


def degenerate(tree: exp.Expression) -> str | None:
    """A query that cannot return an answer: a constant-false WHERE, or nothing but NULLs selected."""
    select = tree
    while isinstance(select, exp.Subquery):
        select = select.this
    if not isinstance(select, exp.Select):
        return None
    where = select.args.get("where")
    if where is not None and _always_false(where.this):
        return (
            f"WHERE {where.this.sql()} is never true, so this query returns no rows whatever the data holds. "
            "If the data cannot answer the question, say so instead of running a placeholder."
        )
    projections = [p.this if isinstance(p, exp.Alias) else p for p in select.expressions]
    if projections and all(isinstance(p, exp.Null) for p in projections):
        return (
            "Every selected value is the constant NULL, so this query does not read the data. If the data "
            "cannot answer the question, say so instead of running a placeholder."
        )
    return None


# ------------------------------------------------------------------- grouping
def _group_expressions(select: exp.Select) -> list[exp.Expression]:
    group = select.args.get("group")
    if group is None:
        return []
    out = []
    for g in group.expressions:
        if isinstance(g, exp.Literal) and not g.is_string and g.this.isdigit():
            index = int(g.this) - 1
            if 0 <= index < len(select.expressions):
                p = select.expressions[index]
                out.append(p.this if isinstance(p, exp.Alias) else p)
            continue
        out.append(g)
    return out


def _own_aggregates(select: exp.Select) -> list[exp.Expression]:
    return [a for a in select.find_all(exp.AggFunc) if a.find_ancestor(exp.Select) is select]


def _is_single_key(col: dict[str, Any]) -> bool:
    """Whether *col* alone is its table's primary key. Uniqueness seen while profiling does not
    count: a date column of a small table is unique by accident."""
    pk = col.get("table_pk")
    if isinstance(pk, str):
        try:
            pk = json.loads(pk)
        except ValueError:
            return False
    return isinstance(pk, list) and len(pk) == 1 and str(pk[0]).casefold() == col["name"].casefold()


def vacuous_group_by(tree: exp.Expression, dialect: str | None, lookup: Lookup) -> list[str]:
    """GROUP BY the primary key of the only table read: every group holds one row, so the
    aggregates aggregate nothing."""
    out: list[str] = []
    for scope in _scopes(tree):
        select = scope.expression
        if not isinstance(select, exp.Select) or select.args.get("joins"):
            continue
        sources = list(scope.sources.values())
        if len(sources) != 1 or not isinstance(sources[0], exp.Table):
            continue
        aggregates = _own_aggregates(select)
        if not aggregates:
            continue
        for g in _group_expressions(select):
            if not isinstance(g, exp.Column):
                continue
            phys = _trace(scope, g)
            col = lookup(phys) if phys else None
            if col and _is_single_key(col):
                out.append(
                    f"GROUP BY {phys.table}.{phys.column} groups by the primary key of {phys.table}, so "
                    f"every group is a single row and {aggregates[0].sql(dialect=dialect)} aggregates one value. "
                    f"Group by what the question asks to break down by, or join the table whose rows are to be "
                    f"counted or summed per {phys.table} row."
                )
                break
    return out


def missing_aggregation(
    tree: exp.Expression, dialect: str | None, one_to_many: set[tuple[str, str]]
) -> list[str]:
    """A column of the many side selected bare next to a GROUP BY on the one side: each group has
    several values for it, and the database returns one of them (or refuses the query).

    *one_to_many* holds ``(one_table, many_table)`` name pairs, casefolded, for the joins in the query.
    """
    if not one_to_many:
        return []
    out: list[str] = []
    for scope in _scopes(tree):
        select = scope.expression
        if not isinstance(select, exp.Select):
            continue
        groups = _group_expressions(select)
        if not groups:
            continue
        group_sql = {g.sql(dialect=dialect) for g in groups}
        group_tables = {
            p.table.casefold() for g in groups for c in g.find_all(exp.Column) if (p := _trace(scope, c)) is not None
        }
        reported: set[str] = set()
        for projection in select.expressions:
            inner = projection.this if isinstance(projection, exp.Alias) else projection
            if inner.sql(dialect=dialect) in group_sql:
                continue
            for column in inner.find_all(exp.Column):
                if column.find_ancestor(exp.Select) is not select:
                    continue
                ancestor, covered = column.parent, False
                while ancestor is not None and ancestor is not select:
                    if isinstance(ancestor, (exp.AggFunc, exp.Window)) or ancestor.sql(dialect=dialect) in group_sql:
                        covered = True
                        break
                    ancestor = ancestor.parent
                if covered or column.sql(dialect=dialect) in group_sql:
                    continue
                phys = _trace(scope, column)
                if phys is None or phys.table.casefold() in group_tables:
                    continue
                parents = sorted(one for one, many in one_to_many if many == phys.table.casefold() and one in group_tables)
                name = f"{phys.table}.{phys.column}"
                if parents and name not in reported:
                    reported.add(name)
                    out.append(
                        f"{name} is selected without an aggregate, but the query groups by {parents[0]} and "
                        f"{phys.table} has several rows per {parents[0]} row, so one arbitrary value is returned "
                        f"(or the database rejects the query). Aggregate it (MIN, MAX, COUNT, a list) or add it "
                        f"to GROUP BY."
                    )
    return out


# ------------------------------------------------------------ cartesian product
class _Groups:
    """Union-find over the sources of one scope, keyed by the name the query uses for each."""

    def __init__(self, keys: list[str]) -> None:
        self._parent = {key: key for key in keys}

    def find(self, key: str) -> str:
        while self._parent[key] != key:
            self._parent[key] = self._parent[self._parent[key]]
            key = self._parent[key]
        return key

    def union(self, first: str, second: str) -> None:
        left, right = self.find(first), self.find(second)
        if left != right:
            self._parent[right] = left

    def groups(self) -> list[list[str]]:
        out: dict[str, list[str]] = {}
        for key in self._parent:
            out.setdefault(self.find(key), []).append(key)
        return list(out.values())


def _is_table(source: Any) -> bool:
    """A physical table: not a CTE or derived table, and not a table-valued function."""
    return isinstance(source, exp.Table) and isinstance(source.this, exp.Identifier)


def _source_tables(scope: Scope) -> dict[str, exp.Table]:
    """The physical tables the scope reads, keyed by the name the query gives them.

    Two aliases of one table are two entries: ``orders o1 JOIN orders o2`` reads the table twice
    and pairs both halves.
    """
    return {name: source for name, source in scope.sources.items() if _is_table(source)}


def _source_label(name: str, table: exp.Table) -> str:
    """How a warning names a source: the table, and the alias when the query gives it one."""
    qualified = f"{table.db}.{table.name}" if table.db else table.name
    return qualified if name.casefold() == table.name.casefold() else f"{qualified} AS {name}"


def _alias_of(column: exp.Column, sources: dict[str, Any], lookup: Lookup | None) -> str | None:
    """The source a column comes from, when this scope can tell without guessing."""
    if column.table:
        folded = column.table.casefold()
        return next((name for name in sources if name.casefold() == folded), None)
    if len(sources) == 1:
        return next(iter(sources))
    if lookup is None:
        return None
    owners = [
        name
        for name, source in sources.items()
        if _is_table(source) and lookup(PhysicalColumn(source.db or "", source.name, column.name))
    ]
    return owners[0] if len(owners) == 1 else None


def _side_owner(node: exp.Expression | None, select: exp.Select, sources: dict[str, Any], lookup: Lookup | None) -> str | None:
    """The one source every column in *node* comes from; None when it has none or mixes sources."""
    if node is None:
        return None
    columns = [node] if isinstance(node, exp.Column) else node.find_all(exp.Column)
    owners: set[str] = set()
    for column in columns:
        if column.find_ancestor(exp.Select) is not select:
            continue  # a subquery of this scope has its own sources
        owner = _alias_of(column, sources, lookup)
        if owner is None:
            return None
        owners.add(owner)
    return owners.pop() if len(owners) == 1 else None


def _conjuncts(node: exp.Expression):
    """The AND-separated parts of a predicate: each one states how some rows relate."""
    while isinstance(node, exp.Paren):
        node = node.this
    if isinstance(node, exp.And):
        yield from _conjuncts(node.left)
        yield from _conjuncts(node.right)
    else:
        yield node


def cartesian_products(tree: exp.Expression, lookup: Lookup | None = None, hint: Callable[[str, str], str | None] | None = None) -> list[str]:
    """Sources read side by side that no predicate ties together.

    A CROSS JOIN, a JOIN with no ON and a comma in FROM pair every row of one source with every row
    of the other. The join checks and the containment probe do not see it when the two value ranges
    overlap, so the query answers with a multiplied count or an inflated sum. *hint* returns a known
    join between two tables, used as the example condition in the message.
    """
    out: list[str] = []
    for scope in _scopes(tree):
        select = scope.expression
        if not isinstance(select, exp.Select):
            continue
        # Every source of the scope: physical tables, CTEs and derived tables. A CTE or a derived
        # table carries a link between the tables it is joined to, so they all take part.
        sources: dict[str, Any] = dict(scope.sources)
        physical = _source_tables(scope)
        if len(sources) < 2:
            continue
        groups = _Groups(list(sources))

        def union_all(names: list[str]) -> None:
            unique = list(dict.fromkeys(names))
            for other in unique[1:]:
                groups.union(unique[0], other)

        def sources_in(node: exp.Expression) -> list[str]:
            names: list[str] = []
            for column in node.find_all(exp.Column):
                if column.find_ancestor(exp.Select) is not select:
                    continue  # a subquery of this scope has its own sources
                name = _alias_of(column, sources, lookup)
                if name is not None:
                    names.append(name)
            return names

        predicates: list[exp.Expression] = []
        where = select.args.get("where")
        if where is not None:
            predicates.extend(_conjuncts(where.this))
        joins = list(select.args.get("joins") or [])
        for join in joins:
            on = join.args.get("on")
            if on is not None:
                predicates.extend(_conjuncts(on))
        # Any operator or function counts. A conjunct that names two sources ties them, whether it
        # is `=`, `LIKE`, `IS NOT DISTINCT FROM`, a comparison of a function's result, or an
        # arithmetic expression with one table's columns on one side.
        for predicate in predicates:
            names = sources_in(predicate)
            if len(set(names)) >= 2:
                union_all(names)
        for join in joins:
            key = next((name for name in sources if name.casefold() == join.this.alias_or_name.casefold()), None)
            if key is None or not isinstance(join.this, exp.Table):
                continue
            # USING and NATURAL tie the joined table to the tables on its left. The columns of an
            # ON predicate were traced above. Either way the table is not left on its own.
            if join.args.get("using") or str(join.args.get("method") or "").upper() == "NATURAL":
                union_all([key, *[name for name in sources if name != key]])
        # Only physical tables are reported: a CTE or a derived table answers for itself in its own
        # scope, and a bridge through one shows up here as the tables it connects.
        components = [[name for name in component if name in physical] for component in groups.groups()]
        components = [component for component in components if component]
        if len(components) <= 1:
            continue
        labels = {name: _source_label(name, table) for name, table in physical.items()}

        def example(name: str) -> str | None:
            """A known join from *name* to another source, if the caller knows one."""
            if hint is None:
                return None
            for other, table in physical.items():
                if other == name:
                    continue
                found = hint(physical[name].name, table.name)
                if found:
                    return found
            return None

        lonely = [name for component in components if len(component) == 1 for name in component]
        if lonely and len(lonely) == len(physical):
            message = (
                f"Tables {', '.join(labels[name] for name in lonely)} are read in the same FROM/JOIN list with no "
                f"predicate linking them, so every row of one pairs with every row of the other (cartesian product): "
                f"counts come back multiplied and sums inflated."
            )
        elif lonely:
            if len(lonely) == 1:
                subject = f"Table {labels[lonely[0]]} appears in the FROM/JOIN list"
                pronoun, those = "it", "that table"
            else:
                subject = f"Tables {', '.join(labels[name] for name in lonely)} appear in the FROM/JOIN list"
                pronoun, those = "them", "those tables"
            message = (
                f"{subject} but no predicate links {pronoun} to the rest, so every row of {those} pairs with every "
                f"row of the others (cartesian product): counts come back multiplied and sums inflated."
            )
        else:
            names = [labels[component[0]] for component in components]
            message = (
                f"The tables {', '.join(names)} are read in groups with no predicate between the groups, so every "
                f"row of one group pairs with every row of the others (cartesian product): counts come back "
                f"multiplied and sums inflated."
            )
        details: list[str] = []
        for join in joins:
            on = join.args.get("on")
            key = next((name for name in sources if name.casefold() == join.this.alias_or_name.casefold()), None)
            if on is None or key not in lonely:
                continue
            owner = _side_owner(on, select, sources, lookup)
            if owner is not None and owner != key:
                # owner can be a CTE or a derived table, which has a name but no table label.
                details.append(f"The ON clause {on.sql()} compares only columns of {labels.get(owner, owner)}.")
        if lonely:
            suggestion = example(lonely[0])
            if suggestion:
                message += f" Add a condition that ties the tables together, for example {suggestion}."
            else:
                message += " Add a condition that ties the tables together, or remove the unlinked table from the query."
        else:
            message += " Add a condition that ties the groups together."
        if details:
            message += " " + " ".join(details[:2])
        out.append(message)
    return out

# ------------------------------------------------------- mixed-case identifiers
def quote_mixed_case(
    tree: exp.Expression, dialect: str | None, catalog_columns: list[dict[str, Any]]
) -> tuple[exp.Expression, list[str]]:
    """Quote unquoted table and column names whose stored spelling the dialect would fold away.

    PostgreSQL lowercases an unquoted ``OrderDate``, so the name no longer matches the stored one. Returns the corrected tree and the names it quoted; the tree
    is returned unchanged when nothing needed quoting.
    """
    if (dialect or "").lower() not in FOLDS_LOWER:
        return tree, []
    fold = str.lower

    def spellings(names: list[str]) -> dict[str, str]:
        by_fold: dict[str, set[str]] = {}
        for n in names:
            by_fold.setdefault(n.casefold(), set()).add(n)
        # Only names with one stored spelling that folding would change.
        return {k: next(iter(v)) for k, v in by_fold.items() if len(v) == 1 and fold(next(iter(v))) != next(iter(v))}

    columns = spellings([c["name"] for c in catalog_columns])
    tables = spellings([c["table_name"] for c in catalog_columns])
    if not columns and not tables:
        return tree, []

    defined = {cte.alias.casefold() for cte in tree.find_all(exp.CTE) if cte.alias}
    aliases = {a.alias.casefold() for a in tree.find_all(exp.Alias) if a.alias}
    table_aliases = {t.alias.casefold() for t in tree.find_all(exp.Table, exp.Subquery) if t.alias}
    fixed = tree.copy()
    quoted: list[str] = []

    def fix(identifier: exp.Expression | None, known: dict[str, str]) -> None:
        if not isinstance(identifier, exp.Identifier) or identifier.args.get("quoted"):
            return
        stored = known.get(identifier.name.casefold())
        if stored is None:
            return
        identifier.set("this", stored)
        identifier.set("quoted", True)
        if stored not in quoted:
            quoted.append(stored)

    for table in fixed.find_all(exp.Table):
        if table.name.casefold() not in defined:
            fix(table.this, tables)
    for column in fixed.find_all(exp.Column):
        if not column.table and column.name.casefold() in aliases:
            continue  # ORDER BY total: an output alias of this query, not a stored column
        fix(column.this, columns)
        qualifier = column.args.get("table")
        if qualifier is not None and qualifier.name.casefold() not in table_aliases | defined:
            fix(qualifier, tables)  # Orders.OrderDate, with the table used under its own name
    return (fixed, quoted) if quoted else (tree, [])
