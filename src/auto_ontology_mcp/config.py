"""Runtime settings, read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


TOOLSETS = ("answer", "model", "knowledge", "share")


def split_patterns(value: str | Iterable[str] | None) -> list[str]:
    """Table filter patterns from a comma-separated string or a list, stripped, blanks dropped.

    Every entry point (env, CLI, MCP arguments, the saved filter) splits with this, so blank
    input means "not given" everywhere.
    """
    if value is None:
        return []
    items = value.split(",") if isinstance(value, str) else [part for v in value for part in str(v or "").split(",")]
    return [p.strip() for p in items if p.strip()]


def _patterns(raw: str | None) -> tuple[str, ...]:
    return tuple(split_patterns(raw))


def _toolsets(raw: str | None) -> frozenset[str]:
    if not raw or raw.strip().lower() == "all":
        return frozenset(TOOLSETS)
    chosen = {x.strip().lower() for x in raw.split(",") if x.strip()}
    unknown = chosen - set(TOOLSETS)
    if unknown:
        raise ValueError(f"AUTO_ONTOLOGY_TOOLSETS: unknown toolset(s) {sorted(unknown)}; choose from {', '.join(TOOLSETS)} or all.")
    return frozenset(chosen | {"answer"})


@dataclass(frozen=True)
class Settings:
    home: Path
    """Directory holding the ontology store (and the demo database)."""

    store_path: Path
    """SQLite file that holds catalogs, terms, relationships and logs."""

    max_rows: int
    """Upper bound on rows ``run_sql`` returns to the model."""

    query_timeout_s: int
    """Per-statement timeout applied to queries against source databases."""

    profile_sample_rows: int
    """Rows sampled per table when profiling columns."""

    allow_datasource_tools: bool
    """Expose ``add_datasource`` / ``scan_datasource`` over MCP."""

    token: str | None
    """Shared secret required by the HTTP transport, when set."""

    host: str
    port: int

    vault: Path | None = None
    """Obsidian vault for originals (AUTO_ONTOLOGY_VAULT). Unset: the knowledge export folder is the vault."""

    oracle_client_lib: str | None = None
    """Oracle thick mode (AUTO_ONTOLOGY_ORACLE_CLIENT_LIB): an Instant Client directory, or 1 to use the
    client found on the library path, for servers that enforce native network encryption. Unset: python-oracledb
    thin mode (no client install). Both modes need Oracle 12.1+."""

    scan_tables: tuple[str, ...] = ()
    """Default table filter for scans (AUTO_ONTOLOGY_SCAN_TABLES, comma-separated globs such as
    ``SALES_*,HR.EMP*,!*_LOG``). Empty: every table. A datasource's own filter or the scan's
    ``tables`` argument takes precedence."""

    toolsets: frozenset[str] = frozenset({"answer", "model", "knowledge", "share"})
    """Tool groups to expose (AUTO_ONTOLOGY_TOOLSETS, comma-separated; default all). A client that only
    asks questions of modeled databases needs 'answer'; fewer tools leave the model more context."""

    @classmethod
    def from_env(cls) -> "Settings":
        home = Path(os.environ.get("AUTO_ONTOLOGY_HOME", "~/.auto-ontology-mcp")).expanduser()
        store = os.environ.get("AUTO_ONTOLOGY_STORE")
        return cls(
            home=home,
            store_path=Path(store).expanduser() if store else home / "ontology.db",
            max_rows=max(1, _int("AUTO_ONTOLOGY_MAX_ROWS", 500)),
            query_timeout_s=max(1, _int("AUTO_ONTOLOGY_QUERY_TIMEOUT_S", 30)),
            profile_sample_rows=max(10, _int("AUTO_ONTOLOGY_PROFILE_ROWS", 1000)),
            allow_datasource_tools=_bool("AUTO_ONTOLOGY_ALLOW_DATASOURCE_TOOLS", True),
            token=os.environ.get("AUTO_ONTOLOGY_MCP_TOKEN") or None,
            host=os.environ.get("AUTO_ONTOLOGY_MCP_HOST", "127.0.0.1"),
            port=_int("AUTO_ONTOLOGY_MCP_PORT", 8765),
            vault=Path(os.environ["AUTO_ONTOLOGY_VAULT"]).expanduser() if os.environ.get("AUTO_ONTOLOGY_VAULT") else None,
            toolsets=_toolsets(os.environ.get("AUTO_ONTOLOGY_TOOLSETS")),
            oracle_client_lib=os.environ.get("AUTO_ONTOLOGY_ORACLE_CLIENT_LIB") or None,
            scan_tables=_patterns(os.environ.get("AUTO_ONTOLOGY_SCAN_TABLES")),
        )
