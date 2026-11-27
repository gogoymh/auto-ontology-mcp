"""A scan reads and writes the same catalog whether it runs serially or in parallel."""

from __future__ import annotations

from dataclasses import replace

from auto_ontology_mcp.catalog import _Probe
from auto_ontology_mcp.config import Settings
from auto_ontology_mcp.service import OntologyService


def _service(tmp_path, demo_path, workers: int) -> OntologyService:
    home = tmp_path / f"home-{workers}"
    settings = replace(
        Settings.from_env(),
        home=home,
        store_path=home / "ontology.db",
        token=None,
        vault=None,
        profile_sample_rows=100,
        scan_workers=workers,
    )
    svc = OntologyService(settings)
    svc.add_datasource("demo", f"sqlite:///{demo_path}")
    return svc


def _dump(svc: OntologyService) -> dict:
    ds = svc.ds("demo")
    labels: dict[str, str] = {}
    tables = []
    for table in svc.store.tables(ds["id"]):
        columns = svc.store.columns(table["id"])
        for column in columns:
            labels[column["id"]] = f"{table['name']}.{column['name']}"
        tables.append(
            (
                table["name"],
                tuple(table.get("pk") or ()),
                table.get("row_count"),
                table.get("is_junction"),
                tuple(
                    (
                        c["name"], c["is_unique"], c["null_fraction"], c["distinct_in_sample"],
                        c["min_value"], c["max_value"], c["format"], c["value_set_complete"],
                        tuple(c["sample_values"] or ()),
                    )
                    for c in columns
                ),
            )
        )
    relationships = sorted(
        (
            labels.get(r["source_column_id"]), labels.get(r["target_column_id"]), r["origin"],
            r["status"], round(r["confidence"], 4), r["role"], r.get("cardinality"),
        )
        for r in svc.store.relationships(ds["id"], ("accepted", "proposed", "rejected"))
    )
    return {"tables": sorted(tables), "relationships": relationships}


def test_a_parallel_scan_reports_what_a_serial_scan_did(tmp_path, demo_path):
    serial = _service(tmp_path, demo_path, 1)
    serial.scan_datasource("demo")
    parallel = _service(tmp_path, demo_path, 4)
    parallel.scan_datasource("demo")

    assert _dump(parallel) == _dump(serial)


def test_the_scan_summary_does_not_depend_on_the_worker_count(tmp_path, demo_path):
    serial = _service(tmp_path, demo_path, 1)
    parallel = _service(tmp_path, demo_path, 4)

    first = serial.scan_datasource("demo")
    second = parallel.scan_datasource("demo")

    for key in ("tables", "columns", "profiled_tables", "warnings"):
        assert first[key] == second[key], key


def test_a_failing_table_keeps_its_place_in_the_warnings(tmp_path, demo_path, monkeypatch):
    svc = _service(tmp_path, demo_path, 4)
    svc.scan_datasource("demo")
    ds = svc.ds("demo")
    tables = svc.store.tables(ds["id"])[:6]
    failing = {tables[1]["name"], tables[4]["name"]}
    original = svc.scanner._probe_table

    def flaky(ds_arg, table):
        if table["name"] in failing:
            return _Probe({}, None, [f"Profiling {table['name']} failed: boom"], False)
        return original(ds_arg, table)

    monkeypatch.setattr(svc.scanner, "_probe_table", flaky)
    probes = svc.scanner._probe_all(ds, tables, workers=4)

    assert [table["name"] for table, _ in probes] == [table["name"] for table in tables]
    assert [probe.warnings[0] for _, probe in probes if probe.warnings] == [
        f"Profiling {tables[1]['name']} failed: boom",
        f"Profiling {tables[4]['name']} failed: boom",
    ]


def test_the_worker_setting_is_clamped(monkeypatch):
    monkeypatch.setenv("AUTO_ONTOLOGY_SCAN_WORKERS", "0")
    assert Settings.from_env().scan_workers == 1
    monkeypatch.setenv("AUTO_ONTOLOGY_SCAN_WORKERS", "99")
    assert Settings.from_env().scan_workers == 8
    monkeypatch.delenv("AUTO_ONTOLOGY_SCAN_WORKERS")
    assert Settings.from_env().scan_workers == 1