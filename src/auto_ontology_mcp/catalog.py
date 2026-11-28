"""Catalog extraction: read schemas, profile columns, infer keys and joins.

This is Auto Ontology's ingestion step ("read sources: extract schemas,
profile columns, and sample values into the catalog") together with the
deterministic half of relationship resolution. Nothing here calls a model.
"""

from __future__ import annotations

import fnmatch
import logging
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Iterable

import sqlalchemy as sa

from . import inference
from .config import split_patterns
from .datasources import (
    Connections,
    native_name,
    qualified_name,
    quote_ident,
    scans_default_schema_only,
    short_error,
    sqlglot_dialect,
)
from .profiling import EMPTY_PROFILE, LOW_CARDINALITY_MAX, pii_column_names, profile_rows
from .sqlcheck import transpile
from .store import Store, new_id, now

logger = logging.getLogger(__name__)

_SYSTEM_SCHEMAS = {
    "information_schema", "pg_catalog", "pg_toast", "sys", "guest", "mysql",
    "performance_schema", "crdb_internal", "pg_extension",
}


def _default_schemas(inspector: sa.Inspector, backend: str) -> list[str | None]:
    if scans_default_schema_only(backend):
        return [None]
    try:
        names = inspector.get_schema_names()
    except Exception:
        return [None]
    keep = [
        n for n in names
        if n.lower() not in _SYSTEM_SCHEMAS and not n.lower().startswith(("pg_temp", "pg_toast", "db_"))
    ]
    return keep or [None]


SQLITE_SHADOW_SUFFIXES = ("_data", "_idx", "_content", "_docsize", "_config")


@dataclass(frozen=True)
class TableFilter:
    """Which tables and views a scan reads.

    Patterns are case-insensitive globs (``*``, ``?``; ``%`` works like ``*``;
    ``_`` is a literal character). A pattern with a dot matches
    ``schema.table``, otherwise the table name; tables in the login user's
    own schema (stored with an empty schema) match under that schema's name.
    ``!pattern`` excludes. With only exclusions, everything else is included.
    """

    include: tuple[str, ...]
    exclude: tuple[str, ...]
    given: tuple[str, ...] = ()

    @classmethod
    def parse(cls, patterns: str | Iterable[str] | None) -> "TableFilter | None":
        include: list[str] = []
        exclude: list[str] = []
        given: list[str] = []
        for item in split_patterns(patterns):
            negate = item.startswith("!")
            item = item[1:].strip() if negate else item
            if not item:
                continue
            given.append(f"!{item}" if negate else item)
            pattern = item.replace("%", "*").casefold()
            (exclude if negate else include).append(pattern)
        if not exclude and (not include or "*" in include):
            return None  # nothing given, or everything asked for
        return cls(tuple(include), tuple(exclude), tuple(given))

    @property
    def patterns(self) -> list[str]:
        """The patterns as written, for reports."""
        return list(self.given)

    @staticmethod
    def _hit(pattern: str, schema: str, table: str) -> bool:
        target = f"{schema}.{table}" if "." in pattern else table
        return fnmatch.fnmatchcase(target, pattern)

    def matches(self, schema: str | None, table: str, default_schema: str | None = None) -> bool:
        """*default_schema* is the login user's schema, used when *schema* is empty."""
        s, t = (schema or default_schema or "").casefold(), table.casefold()
        if self.include and not any(self._hit(p, s, t) for p in self.include):
            return False
        return not any(self._hit(p, s, t) for p in self.exclude)


@dataclass(frozen=True)
class PiiPatterns:
    """Which columns are tagged PII.

    Case-insensitive globs like :class:`TableFilter`, matched by how many dots a pattern has:
    ``email`` matches a column name in any table, ``users.*`` a table's columns and
    ``hr.staff.ssn`` a schema-qualified column. ``!pattern`` removes the tag. A column no pattern
    names keeps the tag it has, so a client without the patterns never lifts one.
    """

    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()

    @classmethod
    def parse(cls, patterns: str | Iterable[str] | None) -> "PiiPatterns":
        include: list[str] = []
        exclude: list[str] = []
        for item in split_patterns(patterns):
            negate = item.startswith("!")
            item = item[1:].strip() if negate else item
            if item:
                (exclude if negate else include).append(item.replace("%", "*").casefold())
        return cls(tuple(include), tuple(exclude))

    def __bool__(self) -> bool:
        return bool(self.include or self.exclude)

    @staticmethod
    def _hit(pattern: str, schema: str, table: str, column: str) -> bool:
        target = {0: column, 1: f"{table}.{column}"}.get(pattern.count("."), f"{schema}.{table}.{column}")
        return fnmatch.fnmatchcase(target, pattern)

    def decide(self, schema: str | None, table: str, column: str) -> bool | None:
        """True to tag, False to untag, None to keep the column's current tag."""
        key = ((schema or "").casefold(), table.casefold(), column.casefold())
        if any(self._hit(p, *key) for p in self.exclude):
            return False
        if any(self._hit(p, *key) for p in self.include):
            return True
        return None


@dataclass
class _Probe:
    """What profiling one table read, before anything is written to the store."""

    stats: dict[str, dict[str, Any]]
    row_count: Any
    warnings: list[str]
    ok: bool


def _qualified_label(c: dict[str, Any]) -> str:
    return f"{c['schema_name']}.{c['table_name']}" if c.get("schema_name") else c["table_name"]


def _stored_empty_profile() -> dict[str, Any]:
    return {k: v for k, v in EMPTY_PROFILE.items() if k != "needs_distinct_probe"}


def _holds_values(c: dict[str, Any]) -> bool:
    return any(c.get(k) not in (None, 0, False) for k in _stored_empty_profile())


class Scanner:
    def __init__(self, store: Store, connections: Connections, sample_rows: int, workers: int = 1) -> None:
        self.store = store
        self.connections = connections
        self.sample_rows = sample_rows
        self.workers = max(1, workers)

    # ------------------------------------------------------------------ helpers
    def _run(self, ds: dict[str, Any], sql: str, max_rows: int | None = None) -> list[list[Any]]:
        dialect = sqlglot_dialect(ds.get("dialect"))
        return self.connections.run_readonly(ds, transpile(sql, dialect), max_rows=max_rows).rows

    @staticmethod
    def _qualify(table: dict[str, Any]) -> str:
        return qualified_name(table.get("schema_name") or None, table["name"])

    # --------------------------------------------------------------------- scan
    def scan(
        self,
        ds: dict[str, Any],
        *,
        schemas: list[str] | None = None,
        profile: bool = True,
        infer: bool = True,
        tables: TableFilter | None = None,
        workers: int | None = None,
        pii: PiiPatterns | None = None,
    ) -> dict[str, Any]:
        """Read the schema. With *tables*, only matching tables and views are read; tables
        scanned earlier that fall outside the filter stay in the catalog untouched. *workers*
        reads and profiles that many tables at once; the result does not depend on it."""
        workers = max(1, workers or self.workers)
        engine = self.connections.engine(ds)
        backend = engine.dialect.name
        dialect = engine.dialect

        def native(name: Any) -> Any:
            return native_name(dialect, name)

        def native_cols(names: list[Any] | None) -> list[Any]:
            return [native(n) for n in names or []]

        inspector = sa.inspect(engine)
        wanted = schemas or ds.get("schemas") or None
        schema_list: list[str | None] = list(wanted) if wanted else _default_schemas(inspector, backend)
        warnings: list[str] = []
        try:
            login_schema = native(inspector.default_schema_name) or ""
        except Exception:  # noqa: BLE001
            login_schema = ""
        # Tables the scan could not read, and schemas it could not list: their catalog rows are
        # kept, not deleted as gone from the database (one timed-out reflection call must not
        # drop reviewed work).
        unread: set[tuple[str, str]] = set()
        unlisted: set[str] = set()

        extracted: list[dict[str, Any]] = []
        skipped = 0
        shadow: set[str] = set()
        if backend == "sqlite":
            # FTS5 and other virtual tables keep their data in shadow tables (x_data, x_idx, ...); skip those.
            try:
                with engine.connect() as conn:
                    virtual = [r[0] for r in conn.exec_driver_sql(
                        "SELECT name FROM sqlite_master WHERE type = 'table' AND sql LIKE 'CREATE VIRTUAL TABLE%'"
                    )]
                shadow = {f"{v}{suffix}" for v in virtual for suffix in SQLITE_SHADOW_SUFFIXES}
            except Exception:  # noqa: BLE001
                shadow = set()
        tasks: list[tuple[Any, Any, str]] = []
        for schema in schema_list:
            try:
                names = [(n, "table") for n in inspector.get_table_names(schema=schema)]
                names += [(n, "view") for n in inspector.get_view_names(schema=schema)]
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"Could not list tables in schema {schema!r}: {short_error(exc)}")
                unlisted.add(native(schema) or "")
                continue
            for name, kind in names:
                if backend == "sqlite" and (name.startswith("sqlite_") or name in shadow):
                    continue
                if tables is not None and not tables.matches(native(schema), native(name), login_schema):
                    skipped += 1
                    continue
                tasks.append((schema, name, kind))

        # Reflection is four round trips per table. Each thread gets its own Inspector: SQLAlchemy's
        # is not thread safe, while the engine and its pool are.
        local = threading.local()

        def read_table(task: tuple[Any, Any, str]) -> tuple[dict[str, Any] | None, str | None, tuple[str, str] | None]:
            schema, name, kind = task
            found = getattr(local, "inspector", None)
            if found is None:
                found = local.inspector = sa.inspect(engine)  # type: ignore[attr-defined]
            try:
                cols = found.get_columns(name, schema=schema)
                pk = (found.get_pk_constraint(name, schema=schema) or {}).get("constrained_columns") or []
                fks = found.get_foreign_keys(name, schema=schema) if kind == "table" else []
            except Exception as exc:  # noqa: BLE001
                warning = f"Could not read {schema or ''}.{name}: {short_error(exc)}; its catalog entry is kept"
                return None, warning, (native(schema) or "", native(name))
            try:
                comment = (found.get_table_comment(name, schema=schema) or {}).get("text")
            except Exception:  # noqa: BLE001
                comment = None
            # Store names as the database spells them (Oracle: CUSTOMERS, not customers).
            cols = [{**c, "name": native(c["name"])} for c in cols]
            fks = [
                {
                    **fk,
                    "constrained_columns": native_cols(fk.get("constrained_columns")),
                    "referred_columns": native_cols(fk.get("referred_columns")),
                    "referred_table": native(fk.get("referred_table")),
                    "referred_schema": native(fk.get("referred_schema")),
                }
                for fk in fks
            ]
            return (
                {
                    "schema": native(schema) or "", "name": native(name), "kind": kind, "columns": cols,
                    "pk": native_cols(pk), "fks": fks, "comment": comment,
                },
                None,
                None,
            )

        if workers > 1 and len(tasks) > 1:
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="scan") as pool:
                read = list(pool.map(read_table, tasks))
        else:
            read = [read_table(task) for task in tasks]
        # Merged in table order, so the catalog rows and the warnings read the same whatever the
        # threads did first.
        for record, warning, unread_key in read:
            if warning is not None:
                warnings.append(warning)
                unread.add(unread_key)  # type: ignore[arg-type]
            if record is not None:
                extracted.append(record)

        if tables is not None and not extracted:
            warnings.append(f"The table filter {', '.join(tables.patterns)} matched no table or view.")
        table_ids = self._upsert_catalog(
            ds, extracted, tables, unread=unread, unlisted=unlisted, login_schema=login_schema
        )
        self._sync_declared_fks(ds, extracted, table_ids, warnings)
        # Tags go on before anything is sampled, so a column named PII is never read.
        pii_summary = self.tag_pii(ds, pii)

        profiled = 0
        if profile:
            # Only what this scan read: earlier areas of a filtered catalog are not re-profiled.
            scanned = set(table_ids.values())
            targets = [table for table in self.store.tables(ds["id"]) if table["id"] in scanned]
            for table, probe in self._probe_all(ds, targets, workers):
                warnings.extend(probe.warnings)
                if probe.ok:
                    self._commit_probe(table, probe)
                    profiled += 1

        inferred_summary: dict[str, Any] = {}
        if infer:
            # A filtered scan probes only candidate joins that touch what it read.
            scanned_ids = set(table_ids.values()) if tables is not None else None
            inferred_summary = self.infer_relationships(ds, probe=profile, scanned=scanned_ids)

        with self.store.tx() as con:
            con.execute(
                "UPDATE datasource SET scanned_at = ?, dialect = ? WHERE id = ?",
                (now(), backend, ds["id"]),
            )
        self.store.log("datasource", ds["id"], "scan", {"tables": len(extracted), "warnings": warnings[:20]})
        return {
            "tables": len(extracted),
            "columns": sum(len(t["columns"]) for t in extracted),
            "profiled_tables": profiled,
            **({"pii_columns": pii_summary["pii_columns"]} if pii_summary["pii_columns"] else {}),
            **({"table_filter": tables.patterns, "skipped_by_filter": skipped} if tables is not None else {}),
            **inferred_summary,
            "warnings": warnings[:30],
        }

    def _upsert_catalog(
        self,
        ds: dict[str, Any],
        extracted: list[dict[str, Any]],
        table_filter: TableFilter | None = None,
        *,
        unread: Iterable[tuple[str, str]] = (),
        unlisted: Iterable[str] = (),
        login_schema: str = "",
    ) -> dict[tuple[str, str], str]:
        unread, unlisted = set(unread), set(unlisted)
        existing = {(t["schema_name"], t["name"]): t for t in self.store.tables(ds["id"])}
        seen: dict[tuple[str, str], str] = {}
        with self.store.tx() as con:
            for t in extracted:
                key = (t["schema"], t["name"])
                row = existing.get(key)
                pk = list(t["pk"])
                if row is None:
                    tid = new_id("tbl")
                    self.store.insert(
                        "catalog_table",
                        {
                            "id": tid, "datasource_id": ds["id"], "schema_name": t["schema"], "name": t["name"],
                            "table_type": t["kind"], "description": t["comment"], "pk": pk, "pk_inferred": 0,
                        },
                        con,
                    )
                else:
                    tid = row["id"]
                    values: dict[str, Any] = {"table_type": t["kind"]}
                    if t["comment"]:
                        values["description"] = t["comment"]
                    if pk or not row.get("pk_inferred"):
                        values["pk"] = pk
                        values["pk_inferred"] = 0
                    self.store.update("catalog_table", tid, values, con)
                seen[key] = tid

                old_cols = {c["name"]: c for c in (con.execute("SELECT id, name, description FROM catalog_column WHERE table_id = ?", (tid,)).fetchall())}
                new_names = set()
                for ordinal, col in enumerate(t["columns"]):
                    name = col["name"]
                    new_names.add(name)
                    try:
                        dtype = str(col["type"])
                    except Exception:
                        dtype = type(col["type"]).__name__
                    if name in old_cols:
                        values = {"data_type": dtype, "is_nullable": bool(col.get("nullable", True)), "ordinal": ordinal}
                        if col.get("comment"):
                            values["description"] = col["comment"]
                        self.store.update("catalog_column", old_cols[name]["id"], values, con)
                    else:
                        self.store.insert(
                            "catalog_column",
                            {
                                "id": new_id("col"), "table_id": tid, "name": name, "data_type": dtype,
                                "is_nullable": bool(col.get("nullable", True)), "ordinal": ordinal,
                                "description": col.get("comment"),
                            },
                            con,
                        )
                for name, old in old_cols.items():
                    if name not in new_names:
                        con.execute("DELETE FROM catalog_column WHERE id = ?", (old["id"],))
            for key, row in existing.items():
                # Only tables the scan covered, listed and did not find are gone from the database.
                # Tables outside the filter, tables whose reflection failed and tables in a schema
                # that could not be listed were not looked at, so they are kept.
                if key in seen or key in unread or key[0] in unlisted:
                    continue
                if table_filter is None or table_filter.matches(key[0], key[1], login_schema):
                    con.execute("DELETE FROM catalog_table WHERE id = ?", (row["id"],))
        return seen

    def _column_index(self, ds: dict[str, Any]) -> dict[tuple[str, str, str], dict[str, Any]]:
        return {
            ((c["schema_name"] or ""), c["table_name"], c["name"]): c
            for c in self.store.columns_for_datasource(ds["id"])
        }

    def _sync_declared_fks(
        self, ds: dict[str, Any], extracted: list[dict[str, Any]], table_ids: dict[tuple[str, str], str], warnings: list[str]
    ) -> None:
        index = self._column_index(ds)
        with self.store.tx() as con:
            for t in extracted:
                for fk in t["fks"]:
                    target_schema = fk.get("referred_schema") or t["schema"]
                    if ds.get("dialect") == "sqlite" or not t["schema"]:
                        target_schema = fk.get("referred_schema") or ""
                    pairs = zip(fk.get("constrained_columns") or [], fk.get("referred_columns") or [])
                    for src, dst in pairs:
                        s = index.get((t["schema"], t["name"], src))
                        d = index.get((target_schema, fk.get("referred_table"), dst))
                        if not s or not d:
                            warnings.append(
                                f"Declared FK {t['name']}.{src} → {fk.get('referred_table')}.{dst} points outside the scanned tables"
                            )
                            continue
                        row = con.execute(
                            "SELECT id FROM relationship WHERE source_column_id = ? AND target_column_id = ?",
                            (s["id"], d["id"]),
                        ).fetchone()
                        if row:
                            con.execute(
                                "UPDATE relationship SET origin = 'declared', status = 'accepted', confidence = 1.0, updated_at = ? WHERE id = ?",
                                (now(), row["id"]),
                            )
                        else:
                            self.store.insert(
                                "relationship",
                                {
                                    "id": new_id("rel"), "datasource_id": ds["id"], "source_column_id": s["id"],
                                    "target_column_id": d["id"], "origin": "declared", "status": "accepted",
                                    "confidence": 1.0, "evidence": {"reasons": ["declared foreign key constraint"]},
                                    "is_default": 1, "updated_at": now(),
                                },
                                con,
                            )

    def tag_pii(self, ds: dict[str, Any], patterns: PiiPatterns | None) -> dict[str, Any]:
        """Apply *patterns* to the catalog's PII tags and withhold the values of tagged columns.

        A column tagged after it was profiled keeps nothing profiling derived from its values:
        a stale ``is_unique`` would still make it a key candidate. Idempotent, and run with no
        patterns it is the backfill for tags set before this pass existed.
        """
        tagged, untagged, cleared = [], [], 0
        with self.store.tx() as con:
            for c in self.store.columns_for_datasource(ds["id"]):
                label = f"{_qualified_label(c)}.{c['name']}"
                decision = patterns.decide(c.get("schema_name"), c["table_name"], c["name"]) if patterns else None
                pii = bool(c.get("pii")) if decision is None else decision
                if pii != bool(c.get("pii")):
                    self.store.update("catalog_column", c["id"], {"pii": pii}, con)
                    (tagged if pii else untagged).append(label)
                if pii and _holds_values(c):
                    self.store.update("catalog_column", c["id"], _stored_empty_profile(), con)
                    cleared += 1
        if tagged or untagged or cleared:
            logger.info(
                "PII tags on %s: %d tagged, %d untagged, %d value profiles cleared",
                ds["name"], len(tagged), len(untagged), cleared,
            )
        pii_columns = sorted(
            f"{_qualified_label(c)}.{c['name']}" for c in self.store.columns_for_datasource(ds["id"]) if c.get("pii")
        )
        return {"tagged": tagged, "untagged": untagged, "cleared": cleared, "pii_columns": pii_columns}

    def _profile_table(self, ds: dict[str, Any], table: dict[str, Any], warnings: list[str]) -> bool:
        """Profile one table in place (the knowledge store refreshes its catalog this way)."""
        probe = self._probe_table(ds, table)
        warnings.extend(probe.warnings)
        if not probe.ok:
            return False
        self._commit_probe(table, probe)
        return True

    def _probe_all(self, ds: dict[str, Any], tables: list[dict[str, Any]], workers: int) -> list[tuple[dict[str, Any], _Probe]]:
        """Probe each table (reads only) and return the results in table order.

        One failing table must not stop the scan, so the failure is a probe that carries the
        warning, exactly as the serial loop produced it.
        """

        def run(table: dict[str, Any]) -> _Probe:
            try:
                return self._probe_table(ds, table)
            except Exception as exc:  # noqa: BLE001
                return _Probe({}, None, [f"Profiling {table['name']} failed: {short_error(exc)}"], False)

        if workers > 1 and len(tables) > 1:
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="profile") as pool:
                return list(zip(tables, pool.map(run, tables)))
        return [(table, run(table)) for table in tables]

    def _probe_table(self, ds: dict[str, Any], table: dict[str, Any]) -> _Probe:
        """Read what profiling needs for one table: samples, a row count, small value sets.

        Nothing here writes to the store, so any number of these can run at once.
        """
        columns = self.store.columns(table["id"])
        if not columns:
            return _Probe({}, None, [], False)
        try:
            pii = pii_column_names(columns)
        except Exception as exc:  # noqa: BLE001
            # Guessing that no column is PII would move exactly the values the tags guard.
            logger.warning("Skipping value sampling of %s: could not read PII tags (%s)", table["name"], exc)
            return _Probe(
                {c["name"]: dict(EMPTY_PROFILE) for c in columns},
                table.get("row_count"),
                [f"Could not read the PII tags of {table['name']}: its values were not sampled and stored profiles were cleared"],
                True,
            )
        qualified = self._qualify(table)
        dialect = sqlglot_dialect(ds.get("dialect"))
        selected = [c["name"] for c in columns if c["name"] not in pii]
        stats: dict[str, dict[str, Any]] = {name: dict(EMPTY_PROFILE) for name in pii}
        if selected:
            # PII columns stay out of the SELECT itself, so their values never leave the database.
            select_list = ", ".join(quote_ident(n) for n in selected) if pii else "*"
            try:
                result = self.connections.run_readonly(
                    ds, transpile(f"SELECT {select_list} FROM {qualified} LIMIT {self.sample_rows}", dialect),
                    max_rows=self.sample_rows,
                )
            except Exception as exc:  # noqa: BLE001
                return _Probe({}, None, [f"Profiling {table['name']} failed: {short_error(exc)}"], False)
            stats.update(profile_rows(columns, result.columns, result.rows, pii))
        else:
            logger.info("Every column of %s is tagged PII: skipping value sampling", table["name"])
        row_count = None
        try:
            row_count = self._run(ds, f"SELECT COUNT(*) FROM {qualified}")[0][0]
        except Exception:  # noqa: BLE001
            pass

        for name, column_stats in stats.items():
            if not column_stats.get("needs_distinct_probe") or name in pii:
                continue
            try:
                quoted = quote_ident(name)
                rows = self._run(
                    ds,
                    f"SELECT DISTINCT {quoted} FROM {qualified} WHERE {quoted} IS NOT NULL LIMIT {LOW_CARDINALITY_MAX + 1}",
                )
                values = [r[0] for r in rows]
                if len(values) <= LOW_CARDINALITY_MAX:
                    merged = list(column_stats["sample_values"])
                    for value in values:
                        if value not in merged and not (isinstance(value, str) and len(value) > 60):
                            merged.append(value)
                    column_stats["sample_values"] = merged
                    column_stats["value_set_complete"] = True
            except Exception:  # noqa: BLE001
                pass
        return _Probe(stats, row_count, [], True)

    def _commit_probe(self, table: dict[str, Any], probe: _Probe) -> None:
        """Write one table's profile. Serial, in table order, in one transaction."""
        by_name = {c["name"]: c for c in self.store.columns(table["id"])}
        with self.store.tx() as con:
            for name, s in probe.stats.items():
                col = by_name.get(name)
                if not col:
                    continue
                self.store.update(
                    "catalog_column",
                    col["id"],
                    {
                        "sample_values": s["sample_values"],
                        "is_unique": s["is_unique"],
                        "null_fraction": s["null_fraction"],
                        "distinct_in_sample": s["distinct_in_sample"],
                        "min_value": s["min_value"],
                        "max_value": s["max_value"],
                        "format": s["format"],
                        "value_set_complete": bool(s.get("value_set_complete")),
                        "list_separator": s.get("list_separator"),
                    },
                    con,
                )
            self.store.update("catalog_table", table["id"], {"row_count": probe.row_count, "profiled_at": now()}, con)

    # --------------------------------------------------------------- inference
    def infer_relationships(
        self, ds: dict[str, Any], *, probe: bool = True, scanned: set[str] | None = None
    ) -> dict[str, Any]:
        """Infer keys and joins. With *scanned* (table ids a filtered scan read), candidate pairs with
        neither side among them keep their stored relationship and are not probed again, so a small
        filtered scan stays fast on a large catalog."""
        tables = self.store.tables(ds["id"])
        columns_by_table = {t["id"]: self.store.columns(t["id"]) for t in tables}

        # 1. Primary keys the schema does not declare.
        inferred_pks = []
        with self.store.tx() as con:
            for t in tables:
                if t.get("pk") and not t.get("pk_inferred"):
                    continue
                pk = inference.infer_primary_key(t, columns_by_table[t["id"]])
                if pk:
                    t["pk"] = pk
                    self.store.update("catalog_table", t["id"], {"pk": pk, "pk_inferred": 1}, con)
                    inferred_pks.append(f"{t['name']}.{pk[0]}")

        # 2. FK candidates by name, confirmed by value containment.
        existing = {
            (r["source_column_id"], r["target_column_id"]): r
            for r in self.store.relationships(ds["id"], ("accepted", "proposed", "rejected"))
        }
        locked = {k for k, r in existing.items() if r["origin"] in ("declared", "manual") or (r.get("evidence") or {}).get("reviewed")}
        candidates, unlinked = inference.candidate_pairs(tables, columns_by_table, locked)
        kept: set[tuple[str, str]] = set()
        counts = {"accepted": 0, "proposed": 0}
        with_probe = probe
        for cand in candidates:
            key = (cand.source["id"], cand.target["id"])
            if key in locked:
                kept.add(key)
                continue
            if scanned is not None and cand.source["table_id"] not in scanned and cand.target["table_id"] not in scanned:
                if key in existing:
                    kept.add(key)  # the evidence from the scan that read these tables stands
                continue
            if with_probe:
                inference.probe_containment(
                    cand,
                    run=lambda sql: self._run(ds, sql),
                    qualify=self._qualify,
                    quote=quote_ident,
                )
            verdict = inference.decide(cand)
            if verdict is None:
                continue
            status, confidence = verdict
            counts[status] += 1
            kept.add(key)
            evidence = {
                "reasons": cand.reasons,
                "name_score": cand.name_score,
                "containment": cand.containment,
                "probe_size": cand.probe_size,
            }
            with self.store.tx() as con:
                row = existing.get(key)
                values = {
                    "origin": "inferred", "status": status, "confidence": confidence, "evidence": evidence,
                    "role": cand.role, "updated_at": now(),
                }
                if row:
                    self.store.update("relationship", row["id"], values, con)
                else:
                    self.store.insert(
                        "relationship",
                        {"id": new_id("rel"), "datasource_id": ds["id"], "source_column_id": key[0], "target_column_id": key[1], "is_default": 1, **values},
                        con,
                    )
        # Inferred rows no longer supported by evidence disappear (reviewed ones stay).
        with self.store.tx() as con:
            for key, row in existing.items():
                if row["origin"] == "inferred" and key not in kept and key not in locked:
                    con.execute("DELETE FROM relationship WHERE id = ?", (row["id"],))

        self._refresh_cardinality_and_defaults(ds)
        junctions = self._refresh_junctions(ds, tables, columns_by_table)
        return {
            "inferred_primary_keys": inferred_pks,
            "relationships_auto_accepted": counts["accepted"],
            "relationships_proposed": counts["proposed"],
            "junction_tables": junctions,
            "unlinked_id_columns": [f"{u['table']['name']}.{u['column']['name']}" for u in unlinked][:50],
        }

    def _refresh_cardinality_and_defaults(self, ds: dict[str, Any]) -> None:
        rels = self.store.relationships(ds["id"], ("accepted", "proposed"))
        groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        with self.store.tx() as con:
            for r in rels:
                card = "one_to_one" if r.get("source_is_unique") else "many_to_one"
                con.execute("UPDATE relationship SET cardinality = ? WHERE id = ?", (card, r["id"]))
                if r["status"] == "accepted":
                    groups[(r["source_table_id"], r["target_table_id"])].append(r)
            for group in groups.values():
                if len(group) == 1:
                    if not (group[0].get("evidence") or {}).get("reviewed"):
                        con.execute("UPDATE relationship SET is_default = 1 WHERE id = ?", (group[0]["id"],))
                    continue
                if any((r.get("evidence") or {}).get("default_reviewed") for r in group):
                    continue
                chosen = inference.default_join_choice(group)
                for r in group:
                    con.execute("UPDATE relationship SET is_default = ? WHERE id = ?", (1 if r["id"] == chosen else 0, r["id"]))

    def _refresh_junctions(
        self, ds: dict[str, Any], tables: list[dict[str, Any]], columns_by_table: dict[str, list[dict[str, Any]]]
    ) -> list[str]:
        fk_cols: dict[str, set[str]] = defaultdict(set)
        for r in self.store.relationships(ds["id"], ("accepted",)):
            fk_cols[r["source_table_id"]].add(r["source_column"])
        found = []
        with self.store.tx() as con:
            for t in tables:
                if (t.get("junction_reason") or "").startswith("host:"):
                    if t.get("is_junction"):
                        found.append(t["name"])
                    continue
                verdict = inference.junction_verdict(t, columns_by_table[t["id"]], fk_cols.get(t["id"], set()))
                if verdict is None:
                    self.store.update("catalog_table", t["id"], {"is_junction": 0, "junction_reason": None}, con)
                    continue
                certain, reason = verdict
                self.store.update(
                    "catalog_table", t["id"],
                    {"is_junction": 1 if certain else 0, "junction_reason": reason if certain else f"candidate: {reason}"},
                    con,
                )
                found.append(t["name"] if certain else f"{t['name']} (candidate)")
        return found
