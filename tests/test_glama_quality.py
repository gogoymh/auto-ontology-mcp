"""Quality checks aligned with Glama's TDQS and maintenance criteria."""

from __future__ import annotations

import json
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastmcp import Client

from auto_ontology_mcp import __version__
from auto_ontology_mcp.server import TOOL_GROUPS, build_server

from strict_skip import skip_unless_required

ROOT = Path(__file__).resolve().parent.parent

ALLOWED_OPENING_VERBS = {
    "Assemble",
    "Compute",
    "Create",
    "Edit",
    "Evaluate",
    "Execute",
    "Fetch",
    "Generate",
    "Import",
    "Insert",
    "Inspect",
    "List",
    "Mine",
    "Modify",
    "Query",
    "Re-read",
    "Register",
    "Retrieve",
    "Return",
    "Run",
    "Save",
    "Search",
    "Serialize",
    "Store",
    "Update",
    "Validate",
}

TAUTOLOGICAL_PARAM_DESCRIPTIONS = {
    "Search query.",
    "What to do.",
    "New name.",
    "New description.",
    "Entity name.",
}


def _walk_schema_properties(schema: dict[str, Any], path: str = "") -> list[tuple[str, dict[str, Any]]]:
    found: list[tuple[str, dict[str, Any]]] = []
    if not isinstance(schema, dict):
        return found
    for name, prop in (schema.get("properties") or {}).items():
        here = f"{path}.{name}" if path else name
        found.append((here, prop))
        found.extend(_walk_schema_properties(prop, here))
    if isinstance(schema.get("items"), dict):
        found.extend(_walk_schema_properties(schema["items"], f"{path}[]"))
    for key in ("anyOf", "oneOf", "allOf"):
        for branch in schema.get(key) or []:
            if isinstance(branch, dict):
                found.extend(_walk_schema_properties(branch, path))
    for def_name, def_schema in (schema.get("$defs") or {}).items():
        if isinstance(def_schema, dict):
            found.extend(_walk_schema_properties(def_schema, f"$defs.{def_name}"))
    return found


def _int_bounds(prop: dict[str, Any]) -> tuple[int | None, int | None]:
    if "minimum" in prop or "maximum" in prop:
        return prop.get("minimum"), prop.get("maximum")
    for branch in prop.get("anyOf") or []:
        if isinstance(branch, dict) and branch.get("type") == "integer":
            return branch.get("minimum"), branch.get("maximum")
    return None, None


def _has_delete_capability(schema: dict[str, Any]) -> bool:
    props = schema.get("properties") or {}
    if "delete" in props or "delete_ids" in props:
        return True
    action = props.get("action") or {}
    return "delete" in (action.get("enum") or [])


async def test_tool_descriptions_titles_and_annotations(settings, svc):
    mcp = build_server(settings, svc)
    async with Client(mcp) as client:
        tools = await client.list_tools()

    tool_names = {t.name for t in tools}
    assert tool_names == set(TOOL_GROUPS)
    assert len(tools) == 35

    for tool in tools:
        title = tool.title or ""
        assert len(title) >= 12, f"{tool.name}: title too short/generic: {title!r}"
        assert "Sql" not in title, f"{tool.name}: acronym casing error in title {title!r}"

        desc = (tool.description or "").strip()
        assert 220 <= len(desc) <= 650, f"{tool.name}: description length {len(desc)} outside [220, 650]"

        first_word = re.split(r"[\s,.:]+", desc, maxsplit=1)[0]
        assert first_word in ALLOWED_OPENING_VERBS, f"{tool.name}: must open with action verb, got {first_word!r}"

        siblings = tool_names - {tool.name}
        mentioned_siblings = [s for s in siblings if re.search(rf"\b{re.escape(s)}\b", desc)]
        assert mentioned_siblings, f"{tool.name}: description does not reference any sibling tool for disambiguation"

        ann = tool.annotations
        assert ann is not None, f"{tool.name}: missing ToolAnnotations"
        for hint in ("read_only_hint", "destructive_hint", "idempotent_hint", "open_world_hint"):
            assert isinstance(getattr(ann, hint), bool), f"{tool.name}: annotation {hint} must be explicit bool"

        if ann.read_only_hint:
            assert ann.destructive_hint is False, f"{tool.name}: read-only tool cannot be destructive"
            assert ann.idempotent_hint is True, f"{tool.name}: read-only tool must be idempotent"

        if _has_delete_capability(tool.input_schema):
            assert ann.read_only_hint is False, f"{tool.name}: delete-capable tool cannot be read-only"
            assert ann.destructive_hint is True, f"{tool.name}: delete-capable tool must set destructive_hint=True"


async def test_tool_input_schemas_complete_and_bounded(settings, svc):
    mcp = build_server(settings, svc)
    async with Client(mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}

    for name, tool in tools.items():
        for prop_path, prop_schema in _walk_schema_properties(tool.input_schema):
            desc = (prop_schema.get("description") or "").strip()
            assert len(desc) >= 8, f"{name}.{prop_path}: missing or too-short description ({desc!r})"
            assert desc not in TAUTOLOGICAL_PARAM_DESCRIPTIONS, f"{name}.{prop_path}: tautological description ({desc!r})"

    expected_bounds: dict[tuple[str, str], tuple[int | None, int | None]] = {
        ("get_modeling_worklist", "limit"): (1, 10),
        ("suggest_sql_attributes", "limit"): (1, None),
        ("search_text", "limit"): (1, 50),
        ("search_text", "source"): (1, None),
        ("list_terms", "limit"): (1, 500),
        ("list_terms", "offset"): (0, None),
        ("recent_changes", "limit"): (1, 500),
        ("search_ontology", "limit"): (1, 50),
        ("lookup_values", "limit"): (1, 100),
        ("run_sql", "max_rows"): (1, None),
    }
    for (tool_name, param), expected in expected_bounds.items():
        prop = tools[tool_name].input_schema["properties"][param]
        assert _int_bounds(prop) == expected, f"{tool_name}.{param}: expected bounds {expected}, got {_int_bounds(prop)}"


def test_glama_json_manifest():
    manifest_path = ROOT / "glama.json"
    assert manifest_path.is_file(), "glama.json must exist at repository root"

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert set(data) <= {"$schema", "maintainers"}
    assert data.get("$schema") == "https://glama.ai/mcp/schemas/server.json"

    maintainers = data.get("maintainers")
    assert isinstance(maintainers, list) and len(maintainers) >= 1
    assert all(isinstance(m, str) and m.strip() for m in maintainers)
    assert len(set(maintainers)) == len(maintainers)
    assert "gogoymh" in maintainers


def _read_release_tags(repo_root: Path) -> tuple[dict[str, str] | None, str | None]:
    try:
        shallow = subprocess.run(
            ["git", "rev-parse", "--is-shallow-repository"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return None, "git executable is unavailable"
    if shallow.returncode != 0:
        return None, "git metadata is unavailable in this checkout"
    if shallow.stdout.strip().lower() == "true":
        return None, "shallow git clone does not guarantee release tags"

    refs = subprocess.run(
        ["git", "for-each-ref", "--format=%(refname:short) %(objecttype)", "refs/tags"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if refs.returncode != 0:
        return None, "git tags could not be read"
    tags = dict(line.split(maxsplit=1) for line in refs.stdout.strip().splitlines() if line.strip())
    if not tags:
        return None, "git checkout has no fetched tags"
    return tags, None


def test_release_workflow_and_version():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == __version__

    workflow_path = ROOT / ".github" / "workflows" / "release.yml"
    assert workflow_path.is_file(), ".github/workflows/release.yml must exist"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    on_block = workflow.get("on", workflow.get(True))
    assert "v*" in (on_block.get("push", {}).get("tags") or [])
    steps = workflow["jobs"]["release"]["steps"]
    commands = "\n".join(s.get("run", "") for s in steps if isinstance(s, dict))
    assert "__version__" in commands
    assert "gh release create" in commands


def test_release_tags():
    tags, skip_reason = _read_release_tags(ROOT)
    if skip_reason is not None:
        skip_unless_required(skip_reason, "CI")
    assert tags is not None
    for expected_tag in ("v0.4.0", "v0.5.0", "v0.6.0", f"v{__version__}"):
        assert tags.get(expected_tag) == "tag", f"annotated git tag {expected_tag} is missing"


def test_git_tag_detection_handles_shallow_and_tagless_repos(tmp_path: Path):
    plain_dir = tmp_path / "plain"
    plain_dir.mkdir()
    tags, reason = _read_release_tags(plain_dir)
    assert tags is None and reason is not None

    origin = tmp_path / "origin"
    origin.mkdir()
    git = ["git", "-c", "user.name=test", "-c", "user.email=test@example.com"]
    subprocess.run([*git, "init", "-q"], cwd=origin, check=True)
    (origin / "README.md").write_text("hi\n", encoding="utf-8")
    subprocess.run([*git, "add", "README.md"], cwd=origin, check=True)
    subprocess.run([*git, "commit", "-q", "-m", "init"], cwd=origin, check=True)

    tags, reason = _read_release_tags(origin)
    assert tags is None and "no fetched tags" in (reason or "")

    subprocess.run([*git, "tag", "-a", "v0.1.0", "-m", "v0.1.0"], cwd=origin, check=True)
    tags, reason = _read_release_tags(origin)
    assert reason is None and tags == {"v0.1.0": "tag"}

    shallow = tmp_path / "shallow"
    subprocess.run(
        ["git", "clone", "-q", "--depth", "1", "--no-tags", origin.as_uri(), str(shallow)],
        check=True,
    )
    tags, reason = _read_release_tags(shallow)
    assert tags is None and "shallow" in (reason or "")


def test_ci_workflow_fetches_tags_and_runs_semantic_tests():
    workflow_path = ROOT / ".github" / "workflows" / "ci.yml"
    assert workflow_path.is_file(), ".github/workflows/ci.yml must exist"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["test"]["steps"]

    checkout = next(s for s in steps if isinstance(s, dict) and str(s.get("uses", "")).startswith("actions/checkout@"))
    checkout_with = checkout.get("with") or {}
    assert checkout_with.get("fetch-depth") == 0
    assert checkout_with.get("fetch-tags") is True

    cache = next(s for s in steps if isinstance(s, dict) and str(s.get("uses", "")).startswith("actions/cache@"))
    cache_with = cache.get("with") or {}
    cache_path = cache_with.get("path")
    assert isinstance(cache_path, str) and cache_path.strip()
    assert isinstance(cache_with.get("key"), str) and "fastembed" in cache_with["key"]

    sync_idx = next(
        i for i, s in enumerate(steps) if isinstance(s, dict) and "uv sync --extra semantic" in (s.get("run") or "")
    )
    sem_idx, sem_step = next(
        (i, s)
        for i, s in enumerate(steps)
        if isinstance(s, dict)
        and "test_semantic_search.py" in (s.get("run") or "")
        and "test_benchmark_semantic_model.py" in (s.get("run") or "")
    )
    assert sync_idx < sem_idx
    sem_env = sem_step.get("env") or {}
    assert sem_env.get("AUTO_ONTOLOGY_EMBEDDING_CACHE") == cache_path
