"""Deterministic relationship inference.

Auto Ontology finds undeclared foreign keys with an LLM (which columns look
like FKs), a vector search (which primary key they point at) and a
sample-value SQL probe (do the values actually match). Here the first two
steps are name heuristics and the third is kept as is: a containment probe
against the live database. Every candidate keeps its evidence so the host
model can review it with ``list_relationships`` / ``set_relationship``.
"""

from __future__ import annotations

import difflib
import logging
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from typing import Any, Callable

from .naming import compact, compact_singular, id_base, split_words
from .profiling import is_numeric_type, is_text_type

logger = logging.getLogger(__name__)

# Containment ≥ this and a good name match → accepted automatically.
AUTO_ACCEPT_CONTAINMENT = 0.98
AUTO_ACCEPT_NAME = 0.75
# Below this the values do not support a join at all.
MIN_CONTAINMENT = 0.6

_SELF_REFERENCE = {"parent", "manager", "reportsto", "supervisor", "boss", "lead", "mentor", "predecessor", "successor", "referrer", "referredby"}

_MEASURE_HINTS = (
    "amount", "price", "cost", "total", "qty", "quantity", "weight", "height", "length",
    "width", "size", "volume", "rate", "pct", "percent", "ratio", "score", "count",
)


@dataclass
class Candidate:
    source: dict[str, Any]
    target: dict[str, Any]
    name_score: float
    role: str | None
    reasons: list[str] = field(default_factory=list)
    containment: float | None = None
    probe_size: int | None = None


def _type_family(data_type: str | None) -> str:
    if is_numeric_type(data_type):
        t = (data_type or "").lower()
        return "int" if "int" in t or "serial" in t or t.startswith("number") else "num"
    if is_text_type(data_type):
        return "text"
    return (data_type or "").lower() or "unknown"


def _families_compatible(first: str, second: str) -> bool:
    """Whether two columns of these type families may join."""
    if first == second:
        return True
    numeric = {"int", "num"}
    if first in numeric and second in numeric:
        return True
    # A text code never references an integer key (state_code → state_id).
    return "unknown" in (first, second)


def infer_primary_key(table: dict[str, Any], columns: list[dict[str, Any]]) -> list[str]:
    """Pick an undeclared single-column PK: ``id`` / ``<table>_id`` that is unique and non-null."""
    tname = compact(table["name"])
    tsing = compact_singular(table["name"])
    best: tuple[int, str] | None = None
    for col in columns:
        if col.get("is_unique") is not True or (col.get("null_fraction") or 0) > 0:
            continue
        name = compact(col["name"])
        rank = None
        if name in ("id", "uuid", "guid"):
            rank = 0
        elif name in (f"{tsing}id", f"{tname}id", f"{tsing}key", f"{tsing}code", f"{tsing}uuid", f"{tsing}no"):
            rank = 1
        elif id_base(col["name"]) is not None and col.get("ordinal", 99) == 0:
            rank = 2
        if rank is not None and (best is None or rank < best[0]):
            best = (rank, col["name"])
    return [best[1]] if best else []


def names_own_table(column: str, table: str) -> bool:
    """``states.state_code`` / ``orders.order_no``: the table's own key or code, not a reference."""
    return id_base(column) in (compact(table), compact_singular(table))


def _looks_like_measure(column: str) -> bool:
    words = [w.lower() for w in split_words(column)]
    return any(w in _MEASURE_HINTS for w in words)


# Values of a primary key column that name the key itself rather than an entity: "customer_id"
# cannot reference a table by matching "id", so these fall through to the table-name rules.
_BARE_NAMES = ("id", "uuid", "guid", "key")
_FUZZY_CUTOFF = 0.85
# The fuzzy rule scores 0.6, and a column keeps only candidates within 0.1 of its best score.
# Once an indexed rule scores above this, no fuzzy candidate can be kept, so the scan is skipped.
_FUZZY_FLOOR = 0.7


@dataclass(frozen=True)
class _Prepared:
    """A column with the string work `_score_prepared` needs, done once instead of once per pair."""

    column: dict[str, Any]
    table: dict[str, Any]
    name: str
    base: str | None
    family: str
    table_compact: str
    table_singular: str


def _prepare(column: dict[str, Any], table: dict[str, Any]) -> _Prepared:
    return _Prepared(
        column=column,
        table=table,
        name=compact(column["name"]),
        base=id_base(column["name"]),
        family=_type_family(column.get("data_type")),
        table_compact=compact(table["name"]),
        table_singular=compact_singular(table["name"]),
    )


def _similar(base: str, table_name: str) -> float:
    """``SequenceMatcher.ratio()``, with the two cheap upper bounds that can rule it out first."""
    matcher = difflib.SequenceMatcher(None, base, table_name)
    if matcher.real_quick_ratio() < _FUZZY_CUTOFF or matcher.quick_ratio() < _FUZZY_CUTOFF:
        return 0.0
    return matcher.ratio()


def _score_prepared(source: _Prepared, target: _Prepared) -> tuple[float, str | None, str] | None:
    """Name evidence that *source* references *target* (a PK).

    The order of these rules is part of the behavior: the first match wins, so a 0.8 rule taken
    early beats a 0.85 rule taken later.
    """
    s_name, base, s_col = source.name, source.base, source.column["name"]
    t_name, t_col = target.name, target.column["name"]

    # Same column name on both sides, and it is not a bare id: customerid vs customers.customerid.
    if s_name == t_name and t_name not in ("id", "uuid", "guid", "key", "code"):
        return 0.95, None, f"same column name as {target.table['name']}.{t_col}"

    if base is None:
        return None

    # Within one table only genuine self references count (parent_id, manager_id).
    if source.table["id"] == target.table["id"]:
        if base in _SELF_REFERENCE or base.startswith("parent"):
            return 0.7, base, f"self-reference '{s_col}'"
        return None

    # Target PK is a bare id: customer_id -> customers.id
    if t_name in ("id", "uuid", "guid", "key"):
        if base in (target.table_singular, target.table_compact):
            return 0.9, None, f"'{s_col}' names table {target.table['name']}"
        for stem in (target.table_singular, target.table_compact):
            if stem and len(stem) >= 3 and base.endswith(stem):
                role = base[: -len(stem)] or None
                return 0.8, role, f"'{s_col}' ends with {target.table['name']} (role '{role}')"
        ratio = _similar(base, target.table_singular)
        if ratio >= _FUZZY_CUTOFF:
            return 0.6, None, f"'{base}' resembles {target.table['name']} ({ratio:.2f})"
        return None

    # Target PK is entity-named: delivery_city_id -> cities.city_id, billingcustomerid -> customerid
    if len(t_name) >= 4 and s_name.endswith(t_name):
        role = s_name[: -len(t_name)] or None
        return 0.8, role, f"'{s_col}' ends with {t_col} (role '{role}')"
    t_base = target.base
    if t_base and base == t_base:
        return 0.85, None, f"same entity stem '{base}'"
    if t_base and len(t_base) >= 3 and base.endswith(t_base):
        role = base[: -len(t_base)] or None
        return 0.75, role, f"'{s_col}' ends with entity '{t_base}' (role '{role}')"
    return None


class _TargetIndex:
    """Name lookups that replace "compare this column with every primary key".

    Every rule in `_score_prepared` asks whether a source name *ends with* a target name or table
    name, so a target is found from a suffix of the source name. The index only narrows the
    candidates: the rules still decide, in order, and a target the index misses would only ever
    fail them, because each rule's condition is one of the keys here.
    """

    def __init__(self, targets: list[_Prepared]) -> None:
        self.targets = targets
        self.order = {target.column["id"]: i for i, target in enumerate(targets)}
        self._by_name: dict[str, list[_Prepared]] = {}
        self._by_stem: dict[str, list[_Prepared]] = {}
        self._by_base: dict[str, list[_Prepared]] = {}
        self._same_table: dict[str, list[_Prepared]] = {}
        self._fuzzy: list[_Prepared] = []
        for target in targets:
            self._by_name.setdefault(target.name, []).append(target)
            self._same_table.setdefault(target.table["id"], []).append(target)
            if target.base:
                self._by_base.setdefault(target.base, []).append(target)
            if target.name in _BARE_NAMES:
                for stem in {target.table_singular, target.table_compact}:
                    if stem:
                        self._by_stem.setdefault(stem, []).append(target)
                self._fuzzy.append(target)
        self._fuzzy.sort(key=lambda target: len(target.table_singular))
        self._fuzzy_lengths = [len(target.table_singular) for target in self._fuzzy]

    def candidates(self, source: _Prepared) -> list[_Prepared]:
        found: dict[str, _Prepared] = {}

        def add(items: list[_Prepared]) -> None:
            for target in items:
                found[target.column["id"]] = target

        add(self._same_table.get(source.table["id"], []))
        add(self._by_name.get(source.name, []))
        base = source.base
        if base is None:
            return list(found.values())
        # A source name ends with the target it references, so every suffix is a key to try.
        for i in range(len(base)):
            suffix = base[i:]
            add(self._by_stem.get(suffix, []))
            add(self._by_base.get(suffix, []))
        for i in range(len(source.name) - 3):
            add(self._by_name.get(source.name[i:], []))
        return list(found.values())

    def fuzzy_candidates(self, source: _Prepared) -> list[_Prepared]:
        """The bare-id targets the fuzzy rule could reach.

        It wants a ratio of 0.85 between the source's entity stem and the target's table name,
        which caps how far apart their lengths can be: only that window is worth scoring.
        """
        base = source.base
        if base is None:
            return []
        low = (85 * len(base) + 114) // 115
        high = (115 * len(base)) // 85
        return self._fuzzy[bisect_left(self._fuzzy_lengths, low) : bisect_right(self._fuzzy_lengths, high)]


def candidate_pairs(
    tables: list[dict[str, Any]],
    columns_by_table: dict[str, list[dict[str, Any]]],
    declared: set[tuple[str, str]],
) -> tuple[list[Candidate], list[dict[str, Any]]]:
    """Name-based FK candidates, plus identifier-like columns left without one.

    ``declared`` holds ``(source_column_id, target_column_id)`` already known.
    """
    declared_sources = {s for s, _ in declared}
    targets: list[_Prepared] = []
    for table in tables:
        pk = table.get("pk") or []
        if len(pk) != 1:
            continue
        for col in columns_by_table.get(table["id"], []):
            if col["name"] == pk[0]:
                targets.append(_prepare(col, table))
    index = _TargetIndex(targets)

    candidates: list[Candidate] = []
    unlinked: list[dict[str, Any]] = []
    for table in tables:
        pk = set(table.get("pk") or [])
        for col in columns_by_table.get(table["id"], []):
            if col["id"] in declared_sources:
                continue
            is_own_pk = col["name"] in pk and len(pk) == 1
            source = _prepare(col, table)
            id_like = source.base is not None
            if _looks_like_measure(col["name"]) and not id_like:
                continue
            def score_all(candidates: Any) -> list[tuple[float, str | None, str, _Prepared]]:
                out: list[tuple[float, str | None, str, _Prepared]] = []
                for target in candidates:
                    if target.column["id"] == col["id"]:
                        continue
                    if is_own_pk and target.table["id"] == table["id"]:
                        continue
                    if not _families_compatible(source.family, target.family):
                        continue
                    hit = _score_prepared(source, target)
                    if hit is None:
                        continue
                    score, role, reason = hit
                    # A table's own single PK only references another table in 1:1 extension tables.
                    out.append((score - 0.2 if is_own_pk else score, role, reason, target))
                return out

            found = score_all(index.candidates(source))
            if not found or max(item[0] for item in found) <= _FUZZY_FLOOR:
                matched = {item[3].column["id"] for item in found}
                found += score_all(target for target in index.fuzzy_candidates(source) if target.column["id"] not in matched)
            if found:
                # The old loop appended candidates in target order and sorted by score (stable), so
                # an equal score keeps the earlier target. Sorting by (-score, position) reproduces
                # that order without walking the targets this column did not match.
                found.sort(key=lambda item: (-item[0], index.order[item[3].column["id"]]))
                top = found[0][0]
                candidates.extend(
                    Candidate(
                        source={**col, "table": table},
                        target={**target.column, "table": target.table},
                        name_score=score,
                        role=role,
                        reasons=[reason],
                    )
                    for score, role, reason, target in found
                    if score >= top - 0.1
                )
            elif id_like and not is_own_pk and col["name"] not in pk and not names_own_table(col["name"], table["name"]):
                unlinked.append({"table": table, "column": col})
    return candidates, unlinked


def probe_containment(
    candidate: Candidate,
    run: Callable[[str], list[list[Any]]],
    qualify: Callable[[dict[str, Any]], str],
    quote: Callable[[str], str],
    limit: int = 500,
) -> None:
    """Fraction of distinct source values found in the target column (sampled)."""
    s_tab, t_tab = candidate.source["table"], candidate.target["table"]
    s_col, t_col = quote(candidate.source["name"]), quote(candidate.target["name"])
    sql = (
        f"SELECT COUNT(*) AS total, COUNT(t.{t_col}) AS hits "
        f"FROM (SELECT DISTINCT {s_col} AS v FROM {qualify(s_tab)} WHERE {s_col} IS NOT NULL LIMIT {limit}) s "
        f"LEFT JOIN {qualify(t_tab)} t ON t.{t_col} = s.v"
    )
    try:
        rows = run(sql)
    except Exception as exc:  # noqa: BLE001
        candidate.reasons.append(f"value probe failed: {str(exc).splitlines()[0][:160]}")
        return
    if not rows:
        return
    total, hits = rows[0][0] or 0, rows[0][1] or 0
    candidate.probe_size = int(total)
    if total:
        candidate.containment = round(float(hits) / float(total), 4)
        candidate.reasons.append(f"{hits}/{total} sampled distinct values exist in the target")
    else:
        candidate.reasons.append("source column has no non-null values to probe")


def decide(candidate: Candidate) -> tuple[str, float] | None:
    """Status and confidence for a probed candidate, or None to drop it."""
    c = candidate.containment
    if c is None:
        if candidate.probe_size == 0:
            return "proposed", round(candidate.name_score * 0.6, 3)
        return "proposed", round(candidate.name_score * 0.5, 3)
    if c < MIN_CONTAINMENT:
        return None
    confidence = round(0.5 * candidate.name_score + 0.5 * c, 3)
    if c >= AUTO_ACCEPT_CONTAINMENT and candidate.name_score >= AUTO_ACCEPT_NAME:
        return "accepted", confidence
    return "proposed", confidence


def junction_verdict(table: dict[str, Any], columns: list[dict[str, Any]], fk_columns: set[str]) -> tuple[bool, str] | None:
    """Pure bridge tables are certain; payload-bearing ones are only candidates.

    Returns ``(is_certain, reason)`` or None when the table is not a junction.
    """
    if len(fk_columns) < 2:
        return None
    pk = set(table.get("pk") or [])
    others = [c for c in columns if c["name"] not in fk_columns and c["name"] not in pk]
    if not others:
        return True, f"every non-key column is a foreign key ({', '.join(sorted(fk_columns))})"
    payload_words = ("qty", "quantity", "role", "rank", "order", "position", "seq", "sort", "status", "date", "from", "to", "start", "end", "created", "updated", "weight", "is", "flag", "primary")
    if len(others) <= 2 and all(any(w in [x.lower() for x in split_words(c["name"])] for w in payload_words) for c in others):
        return False, (
            f"{len(fk_columns)} foreign keys plus association properties ({', '.join(c['name'] for c in others)})"
        )
    return None


def default_join_choice(group: list[dict[str, Any]]) -> str:
    """Among several FKs from one table to the same target, pick the unqualified role.

    ``orders.customer_id`` beats ``orders.billing_customer_id``; otherwise the
    alphabetically first column wins (Auto Ontology's deterministic fallback).
    """
    def key(rel: dict[str, Any]) -> tuple[int, str]:
        target_stem = compact_singular(rel["target_table"])
        base = id_base(rel["source_column"]) or compact(rel["source_column"])
        exact = base in (target_stem, compact(rel["target_table"]), compact(rel["target_column"]))
        return (0 if exact else 1, rel["source_column"].casefold())

    return min(group, key=key)["id"]

