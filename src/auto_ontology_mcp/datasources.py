"""Connections to source databases, with read-only execution.

Every statement that reaches a source database goes through
:func:`run_readonly`. Three layers keep it read-only:

1. the SQL guard (:mod:`auto_ontology_mcp.sqlcheck`) accepts a single
   SELECT / WITH / set-operation statement and nothing else;
2. the connection itself is opened read-only where the driver allows it
   (SQLite ``mode=ro``, Postgres ``default_transaction_read_only``, DuckDB
   ``read_only``, MySQL ``SET SESSION TRANSACTION READ ONLY``, Oracle
   ``SET TRANSACTION READ ONLY`` at the start of every transaction);
3. the transaction is always rolled back, never committed.

Names are kept the way the database stores them. Oracle (and other engines
SQLAlchemy marks ``requires_name_normalize``) store unquoted names in upper
case; SQLAlchemy lowercases them on reflection and in result keys.
:func:`native_name` undoes that so quoted probes such as ``"CUSTOMERS"`` and
result column names match the catalog exactly.
"""

from __future__ import annotations

import datetime as _dt
import decimal
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Engine, make_url

# SQLAlchemy dialect name → sqlglot dialect name.
_SQLGLOT_DIALECTS = {
    "postgresql": "postgres",
    "postgres": "postgres",
    "mysql": "mysql",
    "mariadb": "mysql",
    "sqlite": "sqlite",
    "mssql": "tsql",
    "oracle": "oracle",
    "snowflake": "snowflake",
    "bigquery": "bigquery",
    "duckdb": "duckdb",
    "redshift": "redshift",
    "trino": "trino",
    "presto": "presto",
    "databricks": "databricks",
    "clickhouse": "clickhouse",
}


def sqlglot_dialect(sa_dialect: str | None) -> str | None:
    if not sa_dialect:
        return None
    return _SQLGLOT_DIALECTS.get(sa_dialect.split("+")[0].lower())


def normalize_url(url: str) -> str:
    """Make relative SQLite paths absolute so the URL works from any cwd.

    A bare ``oracle://`` URL gets the maintained ``oracledb`` driver instead of
    SQLAlchemy's legacy cx_Oracle default.
    """
    parsed = make_url(url)
    if parsed.drivername == "oracle":
        parsed = parsed.set(drivername="oracle+oracledb")
    if parsed.get_backend_name() == "sqlite" and parsed.database not in (None, "", ":memory:"):
        db = parsed.database
        if not db.startswith("file:"):
            parsed = parsed.set(database=str(Path(db).expanduser().resolve()))
    return parsed.render_as_string(hide_password=False)


def mask_url(url: str) -> str:
    try:
        return make_url(url).render_as_string(hide_password=True)
    except Exception:
        return "<unparseable url>"


# Backends whose scan covers only the connection's default schema unless schemas are named.
# Oracle lists every database user as a schema (SYS, XDB, MDSYS, ...), so it belongs here.
_DEFAULT_SCHEMA_ONLY = frozenset({"sqlite", "mysql", "mariadb", "oracle"})


def scans_default_schema_only(backend: str | None) -> bool:
    return (backend or "").split("+")[0].lower() in _DEFAULT_SCHEMA_ONLY


def builtin_sources(backend: str | None) -> list[tuple[str, str]]:
    """Row sources every query may read besides the catalog (Oracle's one-row ``DUAL``)."""
    if (backend or "").split("+")[0].lower() == "oracle":
        return [("", "DUAL"), ("SYS", "DUAL")]
    return []


def ping_sql(backend: str | None) -> str:
    return "SELECT 1 FROM DUAL" if (backend or "").split("+")[0].lower() == "oracle" else "SELECT 1"


def native_name(dialect: sa.Dialect, name: Any) -> Any:
    """The name as the database stores it.

    Pass the names the inspector returns (``quoted_name`` objects), so a name
    that really is lower case in Oracle stays lower case.
    """
    if name is None or not getattr(dialect, "requires_name_normalize", False):
        return name
    return str(dialect.denormalize_name(name))


def _thick_mode(oracle_client_lib: str | None) -> Any:
    value = (oracle_client_lib or "").strip()
    if not value or value.lower() in {"0", "false", "no", "off"}:
        return None
    if value.lower() in {"1", "true", "yes", "on"}:
        return True
    return {"lib_dir": str(Path(value).expanduser())}


def _readonly_engine(url: str, timeout_s: int, oracle_client_lib: str | None = None) -> Engine:
    parsed = make_url(url)
    backend = parsed.get_backend_name()
    connect_args: dict[str, Any] = {}
    engine_args: dict[str, Any] = {}

    if backend == "sqlite":
        db = parsed.database
        if db and db != ":memory:" and not db.startswith("file:"):
            path = Path(db).expanduser().resolve()
            if not path.exists():
                raise FileNotFoundError(f"SQLite file not found: {path}")
            # URI mode lets the driver itself refuse writes.
            parsed = parsed.set(database=f"file:{path}?mode=ro", query={"uri": "true"})
        connect_args["check_same_thread"] = False
    elif backend == "postgresql":
        driver = parsed.get_driver_name()
        if driver in ("psycopg", "psycopg2", None, ""):
            connect_args["options"] = (
                f"-c default_transaction_read_only=on -c statement_timeout={timeout_s * 1000}"
            )
    elif backend == "duckdb":
        connect_args["read_only"] = True
    elif backend == "oracle":
        thick = _thick_mode(oracle_client_lib)
        if thick is not None and parsed.get_driver_name() == "oracledb":
            # Thick mode (Oracle Instant Client) for 11g or servers that enforce native network encryption.
            engine_args["thick_mode"] = thick

    engine = sa.create_engine(parsed, connect_args=connect_args, pool_pre_ping=True, **engine_args)

    if backend == "oracle":

        @sa.event.listens_for(engine, "connect")
        def _oracle_timeout(dbapi_con, _record):  # pragma: no cover - needs Oracle
            try:
                dbapi_con.call_timeout = timeout_s * 1000  # milliseconds, per round trip
            except Exception:
                pass

        @sa.event.listens_for(engine, "begin")
        def _oracle_readonly(conn):  # pragma: no cover - needs Oracle
            # Must be the first statement of each transaction; every use ends in a rollback.
            conn.exec_driver_sql("SET TRANSACTION READ ONLY")

    if backend in ("mysql", "mariadb"):

        @sa.event.listens_for(engine, "connect")
        def _mysql_readonly(dbapi_con, _record):  # pragma: no cover - needs MySQL
            cur = dbapi_con.cursor()
            cur.execute("SET SESSION TRANSACTION READ ONLY")
            try:
                cur.execute(f"SET SESSION MAX_EXECUTION_TIME = {timeout_s * 1000}")
            except Exception:
                pass
            cur.close()

    return engine


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool


def json_value(value: Any) -> Any:
    """Coerce driver values into JSON-friendly ones."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if value == value and value not in (float("inf"), float("-inf")) else str(value)
    if isinstance(value, decimal.Decimal):
        return int(value) if value == value.to_integral_value() and abs(value) < 2**53 else float(value)
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        return value.isoformat()
    if isinstance(value, _dt.timedelta):
        return str(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<{len(bytes(value))} bytes>"
    read = getattr(value, "read", None)
    if callable(read):  # a LOB the driver did not convert
        try:
            return json_value(read())
        except Exception:
            return str(value)
    if isinstance(value, (list, tuple)):
        return [json_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): json_value(v) for k, v in value.items()}
    return str(value)


class Connections:
    """Caches one read-only engine per datasource."""

    def __init__(self, timeout_s: int, oracle_client_lib: str | None = None) -> None:
        self.timeout_s = timeout_s
        self.oracle_client_lib = oracle_client_lib
        self._engines: dict[str, Engine] = {}
        self._lock = threading.Lock()

    def engine(self, datasource: dict[str, Any]) -> Engine:
        key = datasource["id"]
        with self._lock:
            engine = self._engines.get(key)
            if engine is None:
                engine = _readonly_engine(datasource["url"], self.timeout_s, self.oracle_client_lib)
                self._engines[key] = engine
            return engine

    def forget(self, datasource_id: str) -> None:
        with self._lock:
            engine = self._engines.pop(datasource_id, None)
        if engine is not None:
            engine.dispose()

    def test(self, datasource: dict[str, Any]) -> tuple[bool, str | None]:
        try:
            engine = self.engine(datasource)
            with engine.connect() as con:
                con.execute(sa.text(ping_sql(engine.dialect.name)))
                con.rollback()
            return True, None
        except Exception as exc:  # noqa: BLE001 - reported to the caller
            return False, _short_error(exc)

    def run_readonly(
        self,
        datasource: dict[str, Any],
        sql: str,
        *,
        max_rows: int | None = None,
        params: dict[str, Any] | None = None,
    ) -> QueryResult:
        """Execute *sql* (already guarded) and fetch at most ``max_rows`` rows."""
        engine = self.engine(datasource)
        with engine.connect() as con:
            timer = _arm_timeout(con, engine.dialect.name, self.timeout_s)
            try:
                result = con.execute(sa.text(sql), params or {})
                if not result.returns_rows:
                    return QueryResult(columns=[], rows=[], truncated=False)
                if engine.dialect.requires_name_normalize and result.cursor is not None:
                    # Raw names (CUSTOMER_ID), matching the catalog, not SQLAlchemy's lowercased keys.
                    columns = [d[0] for d in result.cursor.description]
                else:
                    columns = list(result.keys())
                if max_rows is None:
                    fetched = result.fetchall()
                    truncated = False
                else:
                    fetched = result.fetchmany(max_rows + 1)
                    truncated = len(fetched) > max_rows
                    fetched = fetched[:max_rows]
                rows = [[json_value(v) for v in row] for row in fetched]
                return QueryResult(columns=columns, rows=rows, truncated=truncated)
            finally:
                if timer is not None:
                    timer.cancel()
                con.rollback()


def _arm_timeout(con: sa.Connection, dialect: str, timeout_s: int) -> threading.Timer | None:
    """SQLite has no statement timeout; interrupt it from a timer instead."""
    if dialect != "sqlite":
        return None
    raw = con.connection.dbapi_connection
    timer = threading.Timer(timeout_s, lambda: raw.interrupt())  # type: ignore[union-attr]
    timer.daemon = True
    timer.start()
    return timer


def _short_error(exc: BaseException) -> str:
    text = str(getattr(exc, "orig", None) or exc).strip()
    first = text.splitlines()[0] if text else type(exc).__name__
    return first[:500]


short_error = _short_error


def quote_ident(name: str) -> str:
    """Quote an identifier in the neutral (postgres-style) syntax we transpile from."""
    return '"' + name.replace('"', '""') + '"'


def qualified_name(schema: str | None, table: str) -> str:
    return f"{quote_ident(schema)}.{quote_ident(table)}" if schema else quote_ident(table)
