"""PII-tagged columns: their values never leave the database through the server's own reads.

Ported from Auto Ontology's PII value withholding (87583ba9, 3eb8fa6b). A SQLite database holds
canary values in personal-data columns; every result the server reads from it, every row of the
store and every tool reply is checked for them. Each test also checks that a non-PII value does
move, so a server that samples nothing cannot pass for one that withholds the right columns.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from auto_ontology_mcp import catalog
from auto_ontology_mcp.catalog import PiiPatterns
from auto_ontology_mcp.profiling import pii_column_names
from auto_ontology_mcp.service import OntologyError, OntologyService

CANARY = "pii-canary-7f3a9c"


@pytest.fixture()
def people_db(tmp_path: Path) -> Path:
    path = tmp_path / "people.sqlite"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT, phone TEXT, status TEXT)")
    con.executemany(
        "INSERT INTO users VALUES (?, ?, ?, ?)",
        [(i, f"{CANARY}-{i}@example.invalid", f"{CANARY}-555-{i % 3}", s) for i, s in enumerate(["open", "open", "closed"] * 4)],
    )
    con.commit()
    con.close()
    return path


class Recorder:
    """Wraps Connections.run_readonly and keeps every statement and every row it returned."""

    def __init__(self, svc: OntologyService) -> None:
        self.statements: list[str] = []
        self.rows: list[list] = []
        inner = svc.connections.run_readonly

        def run(ds, sql, **kw):
            result = inner(ds, sql, **kw)
            self.statements.append(sql)
            self.rows.extend(result.rows)
            return result

        svc.connections.run_readonly = run  # type: ignore[method-assign]

    def text(self) -> str:
        return json.dumps(self.rows, default=str)


def store_dump(svc: OntologyService) -> str:
    con = sqlite3.connect(svc.store.path)
    try:
        names = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        return json.dumps({n: con.execute(f'SELECT * FROM "{n}"').fetchall() for n in names}, default=str)
    finally:
        con.close()


def column(svc: OntologyService, name: str) -> dict:
    ds = svc.ds("people")
    return next(c for c in svc.store.columns_for_datasource(ds["id"]) if c["name"] == name)


def new_service(settings, **changes) -> OntologyService:
    return OntologyService(replace(settings, **changes))


# ------------------------------------------------------------------ patterns


@pytest.mark.parametrize(
    "patterns,schema,table,col,expected",
    [
        ("email", "", "users", "email", True),
        ("EMAIL", "", "users", "Email", True),
        ("email", "", "users", "email_verified", None),
        ("users.*", "", "users", "status", True),
        ("users.*", "", "orders", "status", None),
        ("hr.staff.ssn", "hr", "staff", "ssn", True),
        ("hr.staff.ssn", "sales", "staff", "ssn", None),
        ("*phone*,!users.phone_type", "", "users", "phone_type", False),
        ("*phone*,!users.phone_type", "", "users", "mobile_phone", True),
    ],
)
def test_pattern_decisions(patterns, schema, table, col, expected):
    assert PiiPatterns.parse(patterns).decide(schema, table, col) is expected


def test_unreadable_tag_raises():
    assert pii_column_names([{"name": "a", "pii": 0}, {"name": "b", "pii": 1}]) == {"b"}
    with pytest.raises(LookupError):
        pii_column_names([{"name": "a"}])


# ------------------------------------------------------------------ canaries


def test_canary_never_read_stored_or_returned(settings, people_db):
    svc = new_service(settings)
    rec = Recorder(svc)
    out = svc.add_datasource("people", f"sqlite:///{people_db}", pii_columns=["users.email", "phone"])
    assert out["scan"]["pii_columns"] == ["users.email", "users.phone"]

    svc.save_table_terms("users", [{"name": "User", "description": "A user account.", "columns": [
        {"column": "id", "display_name": "User ID"}, {"column": "email", "display_name": "Email"},
        {"column": "phone", "display_name": "Phone"}, {"column": "status", "display_name": "Status"}]}])
    replies = [
        svc.describe_table("users"),
        svc.get_modeling_worklist(tables=["users"]),
        svc.get_term("User"),
        svc.get_query_context(["User"]),
        svc.check_answerable("users with pii canary in their email", ["pii canary", "closed"]),
        svc.search_ontology("pii canary"),
        svc.validate_sql("SELECT id FROM users WHERE email = 'PII-CANARY-7F3A9C-1@EXAMPLE.INVALID'"),
        svc.validate_sql("SELECT id FROM users WHERE phone LIKE '%PII-CANARY%'"),
        svc.scan_datasource("people"),
    ]
    with pytest.raises(OntologyError, match="tagged PII"):
        svc.lookup_values("users.email")

    assert CANARY not in rec.text()
    assert CANARY not in store_dump(svc)
    for reply in replies:
        assert CANARY not in json.dumps(reply, default=str)
    # Non-PII values still move.
    assert "closed" in rec.text()
    assert "closed" in json.dumps(svc.describe_table("users"))
    assert column(svc, "status")["sample_values"]
    view = {c["column"]: c for c in svc.describe_table("users")["columns"]}
    assert "pii" in view["email"] and "pii" not in view["status"]
    assert any('"status"' in s and "email" not in s for s in rec.statements)


def test_canary_all_columns_pii_skips_the_sample(settings, people_db):
    svc = new_service(settings, pii_columns=("users.*",))
    rec = Recorder(svc)
    svc.add_datasource("people", f"sqlite:///{people_db}")
    assert not any("LIMIT" in s and "FROM \"users\"" in s and "COUNT" not in s for s in rec.statements)
    assert CANARY not in rec.text() and CANARY not in store_dump(svc)
    assert column(svc, "status")["sample_values"] is None


# ------------------------------------------------------------------ unreadable tags


def test_unreadable_tags_skip_sampling_and_clear_profiles(settings, people_db, monkeypatch):
    svc = new_service(settings)
    svc.add_datasource("people", f"sqlite:///{people_db}")
    assert column(svc, "email")["sample_values"] and column(svc, "status")["sample_values"]

    def unreadable(columns):
        raise LookupError("tag store unavailable")

    monkeypatch.setattr(catalog, "pii_column_names", unreadable)
    rec = Recorder(svc)
    out = svc.scan_datasource("people", tables=["users"])
    assert not any("LIMIT" in s for s in rec.statements), rec.statements
    assert any("Could not read the PII tags of users" in w for w in out["warnings"])
    for name in ("id", "email", "phone", "status"):
        c = column(svc, name)
        assert c["sample_values"] is None and c["is_unique"] is None and c["min_value"] is None, c
    assert CANARY not in store_dump(svc)


# ------------------------------------------------------------------ tags added after a scan


def test_tagged_later_clears_stored_samples(settings, people_db):
    svc = new_service(settings)
    svc.add_datasource("people", f"sqlite:///{people_db}")
    assert CANARY in json.dumps(column(svc, "email")["sample_values"])

    out = svc.scan_datasource("people", profile=False, pii_columns=["users.email"])
    assert out["pii_columns"] == ["users.email"]
    email = column(svc, "email")
    assert email["pii"] == 1 and email["sample_values"] is None and email["is_unique"] is None
    assert column(svc, "status")["sample_values"]
    assert column(svc, "phone")["sample_values"]  # not tagged

    # A client without the pattern does not lift the tag, and a later full scan keeps it out.
    svc2 = new_service(settings)
    svc2.ds("people")  # the saved pattern is on the datasource
    svc2.store.update("datasource", svc2.ds("people")["id"], {"pii_columns": None})
    rec = Recorder(svc2)
    svc2.scan_datasource("people")
    assert column(svc2, "email")["pii"] == 1
    assert "@example.invalid" not in rec.text()


def test_tagged_later_by_environment_on_startup(settings, people_db):
    svc = new_service(settings)
    svc.add_datasource("people", f"sqlite:///{people_db}")
    assert column(svc, "phone")["sample_values"]
    svc2 = new_service(settings, pii_columns=("phone",))
    phone = column(svc2, "phone")
    assert phone["pii"] == 1 and phone["sample_values"] is None
    with pytest.raises(OntologyError, match="tagged PII"):
        svc2.lookup_values("users.phone")


def test_untag_with_exclusion(settings, people_db):
    svc = new_service(settings, pii_columns=("phone",))
    svc.add_datasource("people", f"sqlite:///{people_db}")
    assert column(svc, "phone")["pii"] == 1
    svc.scan_datasource("people", pii_columns=["!users.phone"])
    phone = column(svc, "phone")
    assert phone["pii"] == 0 and phone["sample_values"]


# ------------------------------------------------------------------ tools


def test_lookup_refuses_pii_and_serves_the_rest(settings, people_db):
    svc = new_service(settings)
    svc.add_datasource("people", f"sqlite:///{people_db}", pii_columns=["email"])
    with pytest.raises(OntologyError, match="tagged PII"):
        svc.lookup_values("users.email", contains="canary")
    values = {v["value"] for v in svc.lookup_values("users.status")["top_values"]}
    assert values == {"open", "closed"}


def test_validate_does_not_probe_pii_literals(settings, people_db):
    svc = new_service(settings)
    svc.add_datasource("people", f"sqlite:///{people_db}", pii_columns=["email"])
    rec = Recorder(svc)
    pii = svc.validate_sql("SELECT id FROM users WHERE email = 'PII-CANARY-7F3A9C-1@EXAMPLE.INVALID'")
    assert CANARY not in json.dumps(pii)
    assert CANARY not in rec.text()
    # The same check still runs on a column that is not PII.
    plain = svc.validate_sql("SELECT id FROM users WHERE status = 'Closed'")
    assert any("'closed'" in w for w in plain["warnings"]), plain["warnings"]
