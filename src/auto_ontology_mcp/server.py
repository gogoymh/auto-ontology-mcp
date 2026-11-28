"""The MCP surface: tools, prompts and a guide resource."""

from __future__ import annotations

import functools
import logging
from typing import Annotated, Any, Callable, Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import __version__, guidance
from .config import Settings
from .service import OntologyError, OntologyService

logger = logging.getLogger(__name__)

READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
READ_DB = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
WRITE_DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False)
WRITE_DB = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True)
WRITE_DB_DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=True)

TABLES_ARG = (
    "Tables and views to read, for very large schemas: case-insensitive globs such as ['SALES_*', 'HR.EMP*', "
    "'!*_LOG'] (* and ? wildcards, % works like *, _ is literal, schema.table when the pattern has a dot, ! excludes). "
    "Tables scanned earlier that fall outside the filter are kept, so a catalog can grow area by area. "
    "['*'] reads everything. Default: the datasource's saved filter, else AUTO_ONTOLOGY_SCAN_TABLES, else all tables."
)

DatasourceArg = Annotated[
    str | None,
    Field(description="Datasource name. Optional when only one datasource is registered."),
]


class ColumnAssignment(BaseModel):
    column: str = Field(description="Physical column name, exactly as listed in candidate_columns.")
    display_name: str = Field(description="User-friendly label with spaces, e.g. 'Order Date'.")
    description: str | None = Field(default=None, description="Optional one-sentence business description.")


class TermInput(BaseModel):
    name: str = Field(description="Business Term name with spaces, e.g. 'Purchase Order'.")
    description: str = Field(description="Business meaning: what one row is and which population it covers.")
    synonyms: list[str] = Field(default_factory=list, description="Alternate names actually used (abbreviations, other languages).")
    columns: list[ColumnAssignment] = Field(description="Every candidate column of the table, each assigned to exactly one Term.")


class ColumnUpdate(BaseModel):
    column: str = Field(description="Physical column name mapped by this Term.")
    display_name: str | None = Field(default=None, description="New user-friendly label with spaces, e.g. 'Order Date'; omit to keep current label.")
    description: str | None = Field(default=None, description="New one-sentence business description; omit to keep current description.")
    add_synonyms: list[str] | None = Field(
        default=None, description="The user's own words for this column to append, e.g. '주문 상태' for Order Status; search finds the column by them."
    )


class EntityColumn(BaseModel):
    name: str = Field(description="snake_case English column name, e.g. 'paid_on', 'total_krw'. 'id' and '_*' are reserved.")
    type: str = Field(default="text", description="text, integer, number, date, datetime, boolean, or ref:<Entity> (e.g. 'ref:Merchant').")
    display_name: str | None = Field(default=None, description="Label in the user's words, e.g. 'Paid On'.")
    description: str | None = Field(default=None, description="Meaning, unit or format.")
    required: bool = Field(default=False, description="Reject records without this value.")


class SourceInput(BaseModel):
    id: int | None = Field(default=None, description="Existing source id; omit to register a new source.")
    title: str | None = Field(default=None, description="File name, page title or a short label, e.g. 'IMG_2041.jpg'.")
    kind: str | None = Field(default=None, description="Source format such as pdf, image, web, note, conversation, email, or book.")
    uri: str | None = Field(default=None, description="Path or URL of the original, when known.")
    author: str | None = Field(default=None, description="Author, organization, or speaker who created the source.")
    published_on: str | None = Field(default=None, description="Publication or capture date in YYYY-MM-DD format.")
    summary: str | None = Field(default=None, description="What the source is about, in a sentence or two.")
    content: str | None = Field(default=None, description="A short excerpt; save longer parts as passages and keep the full text as the original.")
    original: str | None = Field(default=None, description="Path of the original that is already in the Obsidian vault, relative to the vault, e.g. 'Clippings/Article.md'.")
    original_text: str | None = Field(default=None, description="Full markdown text saved as a new note in the vault's originals folder; passing updated text creates a new note and keeps the old one.")
    original_file: str | None = Field(default=None, description="Absolute path of a local file (PDF, image, document) to copy into the vault as the original; changed files are copied beside the old one.")


class PassageInput(BaseModel):
    text: str = Field(description="One idea from the source: verbatim words (kind='quote') or section summary (kind='summary'). Split long text into separate passages.")
    kind: Literal["quote", "summary", "note"] = Field(default="quote", description="quote (words as written), summary (a section in other words) or note (the user's own comment).")
    heading: str | None = Field(default=None, description="Section heading in the original; links then open the original at that heading.")
    locator: str | None = Field(default=None, description="Page, timestamp or line, e.g. 'p. 12', '00:13:20', 'line 40'.")
    terms: list[str] | None = Field(default=None, description="Terms (concepts or entities) the passage is about; missing ones are created as empty concepts.")
    note: str | None = Field(default=None, description="Optional caveat or personal comment attached to this passage.")
    id: int | None = Field(default=None, description="Existing passage id to update in place.")


class RelationInput(BaseModel):
    relation: str = Field(description="snake_case verb: is_a, part_of, has_part, instance_of, related_to, depends_on, causes, used_for, contrasts_with, same_as, example_of, ...")
    target: str = Field(description="Target Term name; created as an empty concept if it does not exist yet.")
    description: str | None = Field(default=None, description="Optional brief explanation of how the source Term relates to the target Term.")
    evidence: str | None = Field(default=None, description="Quote that supports the link.")


class RelationRef(BaseModel):
    relation: str = Field(description="Exact snake_case relation verb to remove, e.g. 'related_to'.")
    target: str = Field(description="Exact target Term name whose link should be removed.")


SourceArg = Annotated[
    int | SourceInput | None,
    Field(description="Where this came from: an existing source id, or a new source to register (deduplicated by uri or title)."),
]

DEFINE_ENTITY_DESCRIPTION = (
    "Create, extend, or delete a table-backed entity in the personal knowledge store (a Term with typed columns). "
    "Use for countable or filterable facts; search first with search_ontology or list_terms to reuse existing entities. "
    "Use save_concept for prose definitions and save_table_terms for external SQL databases. "
    "Adds new columns without altering existing column types; keys enable upsert in add_records. "
    "Pass delete=true to drop the entity (requires force=true if rows exist, permanently deleting all records). "
    "Returns the entity schema and table metadata."
)


class GlossaryEntry(BaseModel):
    name: str = Field(description="Abbreviation or jargon, e.g. 'MRR'.")
    definition: str = Field(description="What it means, e.g. 'monthly recurring revenue'.")


def _tool_errors(fn: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except OntologyError as exc:
            raise ToolError(str(exc)) from exc

    return wrapper


# Which group each tool belongs to (AUTO_ONTOLOGY_TOOLSETS). 'answer' is always on.
TOOL_GROUPS: dict[str, str] = {
    **dict.fromkeys(
        ["check_readiness", "search_ontology", "get_term", "describe_table", "find_join_path", "check_answerable",
         "get_query_context", "lookup_values", "validate_sql", "run_sql", "save_example_query", "search", "fetch"],
        "answer",
    ),
    **dict.fromkeys(
        ["add_datasource", "scan_datasource", "get_modeling_worklist", "save_table_terms", "list_relationships",
         "set_relationship", "suggest_sql_attributes", "save_sql_attribute", "update_term", "save_glossary"],
        "model",
    ),
    **dict.fromkeys(
        ["define_entity", "add_records", "edit_records", "save_concept", "save_passages", "search_text", "get_source",
         "list_terms", "recent_changes", "export_wiki"],
        "knowledge",
    ),
    **dict.fromkeys(["export_ontology", "import_ontology"], "share"),
}


def build_server(settings: Settings | None = None, service: OntologyService | None = None) -> FastMCP:
    settings = settings or Settings.from_env()
    svc = service or OntologyService(settings)
    semantic_on = bool(svc.semantic_status().get("enabled"))
    instructions = guidance.server_instructions(settings.toolsets, semantic=semantic_on)
    logger.info("server instructions: search guidance=%s", "hybrid" if semantic_on else "lexical")
    mcp = FastMCP(
        name="auto-ontology",
        instructions=instructions,
        version=__version__,
        website_url="https://github.com/gogoymh/auto-ontology-mcp",
    )

    def tool(annotations: ToolAnnotations, **kw: Any) -> Callable[[Callable[..., Any]], Any]:
        def deco(fn: Callable[..., Any]) -> Any:
            if TOOL_GROUPS[fn.__name__] not in settings.toolsets:
                return fn  # not exposed; the function stays importable for tests
            return mcp.tool(annotations=annotations, **kw)(_tool_errors(fn))

        return deco

    # ------------------------------------------------------------- readiness
    @tool(READ_DB, title="Check datasource and ontology readiness")
    def check_readiness() -> dict[str, Any]:
        """Inspect every registered datasource and the personal knowledge store to report SQL connectivity, modeling coverage (Terms, relationships, SQL attributes), record counts, and recommended next steps. Call at the start of a session or after unexpected failures; use list_terms or describe_table to inspect specific ontology objects instead. Opens registered databases read-only and returns per-datasource status summaries."""
        return svc.check_readiness()

    # ---------------------------------------------------------- datasources
    if settings.allow_datasource_tools:

        @tool(WRITE_DB, title="Register a database datasource")
        def add_datasource(
            name: Annotated[str, Field(description="Short datasource identifier without path separators, e.g. 'shop'.")],
            url: Annotated[
                str,
                Field(
                    description="SQLAlchemy URL, e.g. sqlite:////abs/path/file.db, "
                    "postgresql+psycopg://user:pass@host:5432/db, mysql+pymysql://user:pass@host/db, duckdb:///file.duckdb, "
                    "oracle+oracledb://user:pass@host:1521/?service_name=ORCLPDB1."
                ),
            ],
            description: Annotated[str | None, Field(description="Brief business summary of what this database holds.")] = None,
            schemas: Annotated[list[str] | None, Field(description="Schemas to include (default: all non-system schemas).")] = None,
            scan: Annotated[bool, Field(description="Scan the schema and profile columns immediately after connecting.")] = True,
            tables: Annotated[list[str] | None, Field(description=TABLES_ARG + " Saved with the datasource for later scans.")] = None,
        ) -> dict[str, Any]:
            """Register a relational database by SQLAlchemy URL, verify read-only connectivity, and optionally scan its schema. Use only when the user supplies a URL; never invent credentials, and use scan_datasource instead to re-read an already registered database. Connections are strictly read-only and never modify the target database. Reusing an existing name updates its URL and saved filter while preserving modeled Terms; fails with the connection or driver error if unreachable."""
            return svc.add_datasource(name, url, description, schemas, scan, tables)

        @tool(WRITE_DB, title="Scan database schema and profiles")
        def scan_datasource(
            datasource: DatasourceArg = None,
            schemas: Annotated[list[str] | None, Field(description="Limit this scan to these database schemas.")] = None,
            profile: Annotated[bool, Field(description="Sample rows to profile columns and verify inferred joins.")] = True,
            tables: Annotated[list[str] | None, Field(description=TABLES_ARG + " Applies to this scan only.")] = None,
        ) -> dict[str, Any]:
            """Re-read a registered database's tables, columns, declared keys, column profiles (samples, uniqueness, formats, small value sets), and name-inferred joins verified by value containment. Use after schema changes or to refresh stale profiles; use add_datasource for new databases and get_modeling_worklist to inspect unmodeled tables afterwards. Connects read-only to the database, preserves existing Terms, reviewed relationships, and SQL attributes, refreshes unreviewed inferred joins, and returns counts of newly discovered tables and columns."""
            return svc.scan_datasource(datasource, schemas, profile, tables)

    # ------------------------------------------------------------- modeling
    @tool(READ, title="Get unmodeled tables worklist")
    def get_modeling_worklist(
        datasource: DatasourceArg = None,
        limit: Annotated[int, Field(ge=1, le=10, description="Maximum unmodeled or updated tables to return (1-10).")] = 3,
        tables: Annotated[list[str] | None, Field(description="Specific table names to model or re-model instead of the next pending batch.")] = None,
    ) -> dict[str, Any]:
        """Fetch the next batch of unmodeled database tables (or modeled tables with newly scanned columns), including candidate column profiles, foreign keys, linked Terms, and modeling rules. Use while building an ontology over a SQL database, paired with save_table_terms until remaining is 0; use define_entity for personal knowledge entities and describe_table to inspect one table without modeling rules. Returns tables, remaining count, and current_terms for tables with new columns."""
        return svc.get_modeling_worklist(datasource, limit, tables)

    @tool(WRITE_DESTRUCTIVE, title="Save business Terms for a table")
    def save_table_terms(
        table: Annotated[str, Field(description="Physical table name, schema-qualified if needed (e.g. 'sales.orders').")],
        terms: Annotated[list[TermInput], Field(description="Proposed business Terms for this table (usually exactly one Term per table).")],
        column_descriptions: Annotated[
            dict[str, str] | None, Field(description="Optional {column: one-sentence description} map for non-obvious columns.")
        ] = None,
        is_junction: Annotated[
            bool | None, Field(description="Set true only when each row solely associates two or more entities (bridge table).")
        ] = None,
        datasource: DatasourceArg = None,
    ) -> dict[str, Any]:
        """Save business Terms and column mappings for one scanned database table. Use for tables returned by get_modeling_worklist; use update_term to tweak a single existing Term, define_entity for knowledge store tables, and save_concept for prose definitions. Replaces the table's previous Terms (same-named Terms update; unreferenced Terms without SQL attributes are removed; unassigned candidate columns attach to the first Term). Validates atomically so unknown columns, duplicates, or cross-table name clashes save nothing and return every error. New Terms start as draft."""
        return svc.save_table_terms(table, [t.model_dump() for t in terms], column_descriptions, is_junction, datasource)

    @tool(READ, title="List table join relationships")
    def list_relationships(
        status: Annotated[
            Literal["proposed", "accepted", "rejected", "all"],
            Field(description="Filter joins by review status: 'proposed', 'accepted', 'rejected', or 'all'."),
        ] = "proposed",
        table: Annotated[str | None, Field(description="Restrict results to relationships touching this table.")] = None,
        datasource: DatasourceArg = None,
    ) -> dict[str, Any]:
        """List table join relationships with their evidence (declared FK, inferred name/containment scores, or manual), plus multi-role joins, unlinked identifier columns, and ad-hoc query joins not yet recorded. Use to audit the join graph before or during modeling and act with set_relationship; use find_join_path to get the shortest join route between two Terms. Returns grouped relationships, role notes, and review rules."""
        return svc.list_relationships(datasource, status, table)

    @tool(WRITE_DESTRUCTIVE, title="Update a table join relationship")
    def set_relationship(
        action: Annotated[
            Literal["accept", "reject", "add", "update", "delete"],
            Field(description="Operation to perform: accept or reject a proposed join, add a missing one, update metadata/default flag, or delete permanently."),
        ],
        relationship_id: Annotated[str | None, Field(description="Relationship id from list_relationships (alternative to passing source and target).")] = None,
        source: Annotated[str | None, Field(description="Foreign-key column formatted as 'table.column' (used with target when adding or without relationship_id).")] = None,
        target: Annotated[str | None, Field(description="Referenced key column formatted as 'table.column' (used with source).")] = None,
        role: Annotated[str | None, Field(description="Business role when multiple columns reference the same table, e.g. 'billing'.")] = None,
        description: Annotated[str | None, Field(description="Business meaning of this join relationship.")] = None,
        is_default: Annotated[bool | None, Field(description="Set true to mark this join as the default path between its two tables.")] = None,
        datasource: DatasourceArg = None,
    ) -> dict[str, Any]:
        """Modify one join relationship between two table columns: accept or reject a proposed join, add a missing one, update its role/description/default flag, or delete it permanently. Use after reviewing joins in list_relationships; use find_join_path to inspect active routes instead. Identify the join by relationship_id or by source and target ('table.column'). Only accepted joins enter join paths and survive re-scans; action='delete' removes the record permanently while 'reject' preserves the negative decision. Fails if the columns or relationship do not exist."""
        return svc.set_relationship(action, relationship_id, source, target, role, description, is_default, datasource)

    @tool(READ, title="Suggest reusable SQL attributes")
    def suggest_sql_attributes(
        term: Annotated[str | None, Field(description="Restrict candidate mining to one Term name, synonym, or id.")] = None,
        limit: Annotated[int, Field(ge=1, description="Maximum candidate SQL attributes to return (>= 1).")] = 15,
        datasource: DatasourceArg = None,
    ) -> dict[str, Any]:
        """Mine candidate reusable SQL attributes (measures, filters, dimensions, and join patterns) from executed queries, verified examples, bridge tables, and column profiles, alongside promotion rules. Use after Terms are modeled and persist worthwhile candidates with save_sql_attribute; use get_term to inspect attributes already saved on a Term. Returns candidates and a more_available count."""
        return svc.suggest_sql_attributes(datasource, term, limit)

    @tool(WRITE_DB_DESTRUCTIVE, title="Save or delete a SQL attribute")
    def save_sql_attribute(
        term: Annotated[str, Field(description="Owning Term name, synonym, or id.")],
        name: Annotated[str, Field(description="Title Case attribute name, e.g. 'Net Revenue'.")],
        expression: Annotated[
            str | None,
            Field(
                description="SQL in the datasource dialect. measure/dimension/filter: a fragment over the Term's own "
                "table qualified by the bare table name (SUM(order_lines.line_total)). join/query: a full SELECT."
            ),
        ] = None,
        description: Annotated[str | None, Field(description="Business meaning of the expression and when to use it.")] = None,
        kind: Annotated[
            Literal["measure", "filter", "dimension", "join", "query"],
            Field(description="Expression category: 'measure', 'filter', or 'dimension' for single-table fragments; 'join' or 'query' for full SELECT statements."),
        ] = "measure",
        status: Annotated[Literal["draft", "certified", "deprecated"] | None, Field(description="Set 'certified' only after explicit user approval.")] = None,
        delete: Annotated[bool, Field(description="Set true to permanently delete this SQL attribute.")] = False,
        datasource: DatasourceArg = None,
    ) -> dict[str, Any]:
        """Create, update, certify, or permanently delete a named SQL expression attached to a Term. Use to persist candidates from suggest_sql_attributes or reusable business metrics; use save_example_query for full question-and-SQL pairs and update_term for Term definitions. Probes the expression read-only against the database before saving and rejects broken SQL without saving. Editing a certified attribute's expression resets it to draft; delete=true removes it permanently."""
        return svc.save_sql_attribute(term, name, expression, description, kind, status, delete, datasource)

    @tool(WRITE_DESTRUCTIVE, title="Edit, certify, or delete a Term")
    def update_term(
        term: Annotated[str, Field(description="Existing Term name, synonym, or id.")],
        name: Annotated[str | None, Field(description="Replacement business Term name with spaces; omit to keep current name.")] = None,
        description: Annotated[str | None, Field(description="Replacement one-to-two sentence business definition; omit to keep current definition.")] = None,
        synonyms: Annotated[list[str] | None, Field(description="Complete synonym list replacing all existing synonyms.")] = None,
        add_synonyms: Annotated[list[str] | None, Field(description="Additional synonyms to append, e.g. user vocabulary or translations.")] = None,
        status: Annotated[
            Literal["draft", "certified", "deprecated"] | None,
            Field(description="Approval state; pass 'certified' only when a person explicitly confirmed the definition."),
        ] = None,
        note: Annotated[str | None, Field(description="Reviewer note explaining the change or certification.")] = None,
        column_updates: Annotated[list[ColumnUpdate] | None, Field(description="Updates to display names or descriptions of mapped columns.")] = None,
        delete: Annotated[bool, Field(description="Set true to permanently delete this Term, its column mappings, SQL attributes, and relations.")] = False,
        datasource: DatasourceArg = None,
    ) -> dict[str, Any]:
        """Edit metadata, synonyms, column labels, or approval status of one existing Term, or delete it permanently. Use for targeted edits after modeling or to add user synonyms; use save_table_terms to remodel all Terms on a table, save_concept for concept markdown bodies and typed relations, save_glossary for abbreviations, and define_entity(delete=true) to drop knowledge entities. Editing a certified Term's name or description resets it to draft unless status is also passed; delete=true permanently removes the Term, its mappings, SQL attributes, and relations."""
        updates = [c.model_dump() for c in column_updates] if column_updates else None
        return svc.update_term(term, name, description, synonyms, add_synonyms, status, note, updates, delete, datasource)

    @tool(WRITE_DESTRUCTIVE, title="Save or delete glossary entries")
    def save_glossary(
        entries: Annotated[list[GlossaryEntry] | None, Field(description="Glossary abbreviation/jargon entries to create or update.")] = None,
        delete: Annotated[list[str] | None, Field(description="Exact entry names to permanently delete from the selected scope.")] = None,
        global_scope: Annotated[bool, Field(description="Set true to store or delete entries across all datasources instead of one datasource.")] = False,
        datasource: DatasourceArg = None,
    ) -> dict[str, Any]:
        """Create, update, or permanently delete glossary entries mapping abbreviations and internal jargon (e.g. MRR to monthly recurring revenue) in either one datasource or global scope. Use for shorthand expansions that feed search_ontology and get_query_context; use update_term or save_table_terms for column-backed business Terms and save_concept for longer explanations. Same-named entries upsert in place; names passed in delete are permanently removed from the chosen scope."""
        payload = [e.model_dump() for e in entries] if entries else None
        return svc.save_glossary(payload, delete, datasource, global_scope)

    # ------------------------------------------------------- knowledge store
    @tool(WRITE_DESTRUCTIVE, title="Define, alter, or delete a knowledge entity", description=DEFINE_ENTITY_DESCRIPTION)
    def define_entity(
        name: Annotated[str, Field(description="Singular entity (Term) name, e.g. 'Receipt', 'Book', or '운동 기록'.")],
        columns: Annotated[list[EntityColumn] | None, Field(description="Columns to create or append; existing column types cannot be changed.")] = None,
        description: Annotated[str | None, Field(description="One-sentence definition of what one record represents.")] = None,
        table: Annotated[str | None, Field(description="snake_case SQLite table name; required when name is not English.")] = None,
        keys: Annotated[list[str] | None, Field(description="Natural-key column names (e.g. ['isbn']) used to upsert matching rows in add_records.")] = None,
        synonyms: Annotated[list[str] | None, Field(description="Alternate names or translations for this entity.")] = None,
        delete: Annotated[bool, Field(description="Set true to drop the entity (fails while rows exist unless force=true).")] = False,
        force: Annotated[bool, Field(description="Set true with delete=true to permanently delete the entity and all of its records.")] = False,
    ) -> dict[str, Any]:
        cols = [c.model_dump() for c in columns] if columns else None
        return svc.knowledge.define_entity(name, cols, description, table, keys, synonyms, delete, force)

    @tool(WRITE, title="Insert or upsert knowledge records")
    def add_records(
        entity: Annotated[str, Field(description="Existing knowledge entity (Term) name, e.g. 'Receipt'.")],
        records: Annotated[
            list[dict[str, Any]],
            Field(
                description="List of record objects mapping column names to values. Strings like '12,000원' or '2026/10/03' are normalized; "
                "ref columns accept an id, name (auto-created when create_missing_refs=true), or object. Optional per record: id (update row), _locator, _evidence, _note, _source."
            ),
        ],
        source: SourceArg = None,
        on_conflict: Annotated[
            Literal["update", "skip", "error"],
            Field(description="Conflict policy when a record matches an existing row by natural keys or identical values: 'update', 'skip', or 'error'."),
        ] = "update",
        create_missing_refs: Annotated[bool, Field(description="Set true to auto-create referenced entity rows (e.g. a new merchant or author) by name.")] = True,
    ) -> dict[str, Any]:
        """Insert or upsert structured records into an existing knowledge entity with source provenance, locators, and supporting quotes. Use after define_entity for countable facts; use edit_records to update or delete existing rows by id, save_concept for prose explanations, and save_passages for searchable document excerpts. Validates the entire batch atomically (normalizing currency/dates and resolving refs) so any invalid record aborts the write and returns all errors. Returns inserted, updated, and skipped ids."""
        src = source.model_dump(exclude_none=True) if isinstance(source, SourceInput) else source
        return svc.knowledge.add_records(entity, records, src, on_conflict, create_missing_refs)

    @tool(WRITE_DESTRUCTIVE, title="Update or delete knowledge records by ID")
    def edit_records(
        entity: Annotated[str, Field(description="Knowledge entity (Term) name whose records to modify, e.g. 'Receipt'.")],
        action: Annotated[
            Literal["update", "delete"],
            Field(description="Operation to perform on the specified record ids: 'update' fields or 'delete' permanently."),
        ],
        ids: Annotated[list[int], Field(description="Integer record ids to modify (find them with run_sql or get_source).")],
        values: Annotated[dict[str, Any] | None, Field(description="For action='update': column values to set; may include _locator, _evidence, and _note.")] = None,
        source: SourceArg = None,
    ) -> dict[str, Any]:
        """Update specific fields or permanently delete existing knowledge entity records by integer id. Use after locating row ids with run_sql or get_source; use add_records to insert or upsert new rows and define_entity to alter columns or drop the whole entity. action='update' modifies only the supplied columns and provenance fields; action='delete' is permanent, requires user confirmation, and fails if other records reference any target row."""
        src = source.model_dump(exclude_none=True) if isinstance(source, SourceInput) else source
        return svc.knowledge.edit_records(entity, action, ids, values, src)

    @tool(WRITE, title="Create or update a knowledge concept")
    def save_concept(
        name: Annotated[str, Field(description="Concept (Term) name in any language, e.g. 'Spaced Repetition'.")],
        description: Annotated[str | None, Field(description="One-sentence business or conceptual definition.")] = None,
        body: Annotated[str | None, Field(description="Markdown wiki body replacing the current body: overview, mechanics, examples, open questions.")] = None,
        append_body: Annotated[str | None, Field(description="Markdown section appended to the current body without replacing existing text.")] = None,
        synonyms: Annotated[list[str] | None, Field(description="Additional synonyms to append to this concept.")] = None,
        relations: Annotated[list[RelationInput] | None, Field(description="Typed directed links from this concept to other Terms.")] = None,
        remove_relations: Annotated[list[RelationRef] | None, Field(description="Existing typed links to unlink from this concept.")] = None,
        source: SourceArg = None,
        locator: Annotated[str | None, Field(description="Location in the source (page, chapter, heading, or timestamp).")] = None,
        evidence: Annotated[str | None, Field(description="Supporting quote from the source.")] = None,
        status: Annotated[Literal["draft", "certified", "deprecated"] | None, Field(description="Set 'certified' only when the user explicitly confirms.")] = None,
        datasource: Annotated[str | None, Field(description="Target datasource; defaults to 'knowledge', or pass a database datasource to document its Terms.")] = None,
    ) -> dict[str, Any]:
        """Create or update a concept Term with a one-sentence definition, markdown wiki body, typed relations, and source provenance. Use for ideas, frameworks, or documenting existing entity Terms; use define_entity and add_records for countable facts, save_passages for document excerpts, and update_term(delete=true) to delete a Term. body replaces existing prose while append_body adds to it; missing relation targets are created as empty stub concepts. Editing a certified concept resets its status to draft."""
        src = source.model_dump(exclude_none=True) if isinstance(source, SourceInput) else source
        rels = [r.model_dump() for r in relations] if relations else None
        removes = [r.model_dump() for r in remove_relations] if remove_relations else None
        return svc.knowledge.save_concept(name, description, body, append_body, synonyms, rels, removes, src, locator, evidence, status, datasource)

    @tool(WRITE_DESTRUCTIVE, title="Save or replace source passages")
    def save_passages(
        passages: Annotated[list[PassageInput] | None, Field(description="Excerpts or section summaries to insert or update (up to 200 per call).")] = None,
        source: SourceArg = None,
        delete_ids: Annotated[list[int] | None, Field(description="Passage ids to permanently delete (confirm with the user first).")] = None,
        replace: Annotated[
            bool,
            Field(description="Set true when re-ingesting a revised source: passages becomes the complete set and unmentioned passages of that source are permanently deleted."),
        ] = False,
    ) -> dict[str, Any]:
        """Store, update, replace, or delete searchable excerpts and section summaries for one source document, linked to Terms and an Obsidian vault original. Use for articles, papers, books, or notes you want searchable via search_text; use add_records for structured facts and save_concept for concept definitions. Identical text from the same source updates in place; replace=true requires source and passages and permanently prunes stale passages; delete_ids permanently removes specified passages. Returns inserted, updated, unchanged, deleted ids, and any stub concepts created."""
        src = source.model_dump(exclude_none=True) if isinstance(source, SourceInput) else source
        items = [x.model_dump(exclude_none=True) for x in passages] if passages else None
        return svc.knowledge.save_passages(items, src, delete_ids, replace)

    @tool(READ, title="Full-text search in the knowledge store")
    def search_text(
        query: Annotated[str, Field(description="Words to search for as written in sources, passages, or Term bodies (any language).")],
        kinds: Annotated[
            list[Literal["passage", "term", "source"]] | None,
            Field(description="Restrict hits to 'passage', 'term', or 'source' (default: all three)."),
        ] = None,
        source: Annotated[int | None, Field(ge=1, description="Restrict results to passages belonging to this integer source id.")] = None,
        term: Annotated[str | None, Field(description="Restrict results to passages linked to this Term name.")] = None,
        limit: Annotated[int, Field(ge=1, le=50, description="Maximum ranked hits to return (1-50).")] = 10,
    ) -> dict[str, Any]:
        """Run BM25 full-text, compound-substring, and optional local hybrid vector search across knowledge store passages, Term bodies, and sources. Use when searching inside stored prose or quotes; use search_ontology to search structured schema catalogs across any datasource, and get_source to inspect everything from one source. Strips Korean particles, falls back to any-word matching, and fuses local embeddings via RRF when the semantic extra is installed. Returns ranked snippets, Obsidian [[links]], fts_query, and a SQL example."""
        return svc.knowledge.search_text(query, list(kinds) if kinds else None, limit, source, term)

    @tool(READ, title="Inspect a knowledge source and provenance")
    def get_source(
        source: Annotated[str, Field(description="Source id (e.g. '3' or 'source:3') or a substring of its title.")],
    ) -> dict[str, Any]:
        """Retrieve one knowledge source by id or title substring together with its Obsidian original path and every item derived from it (entity records, concepts, relations, and passages). Use to cite provenance or audit what a document contributed; use search_text to find sources by content and describe_table for physical database tables. Fails if no source matches or multiple titles match ambiguously."""
        return svc.knowledge.get_source(source)

    @tool(READ, title="List Terms in a datasource")
    def list_terms(
        kind: Annotated[
            Literal["all", "entity", "concept"],
            Field(description="Filter Terms by kind: 'all', 'entity' (table-backed records), or 'concept' (markdown definitions)."),
        ] = "all",
        status: Annotated[Literal["draft", "certified", "deprecated"] | None, Field(description="Filter to Terms with this approval status.")] = None,
        limit: Annotated[int, Field(ge=1, le=500, description="Maximum Terms per page (1-500).")] = 200,
        offset: Annotated[int, Field(ge=0, description="Zero-based pagination offset (>= 0).")] = 0,
        datasource: DatasourceArg = None,
    ) -> dict[str, Any]:
        """List a paginated index of every Term (entities and concepts) in one datasource with one-line definitions, status, synonyms, record/relation counts, and empty-stub flags. Use to browse the catalog or check existing Terms before creating new ones; use search_ontology for keyword search and get_term for one Term's complete details. Returns total count, offset, and paged term summaries."""
        return svc.list_terms(datasource, kind, status, limit, offset)

    @tool(READ, title="List recent ontology and knowledge changes")
    def recent_changes(
        limit: Annotated[int, Field(ge=1, le=500, description="Maximum changelog entries to return, newest first (1-500).")] = 30,
    ) -> dict[str, Any]:
        """Return the newest changelog entries across all datasources and the personal knowledge store, recording Terms, records, concepts, relationships, passages, and SQL attributes added, updated, certified, or deleted. Use to review recent session activity or answer 'what changed lately'; use check_readiness for current aggregate counts and get_term for a Term's current state. Returns up to limit entries ordered newest first with timestamps and action details."""
        return svc.recent_changes(limit)

    @tool(WRITE, title="Export ontology as an Obsidian vault")
    def export_wiki(datasource: DatasourceArg = None) -> dict[str, Any]:
        """Generate an Obsidian-compatible markdown vault for one datasource (defaulting to the knowledge store): one page per Term with synonym aliases, [[links]], and passages, one per source, plus index.md and log.md. Use to browse or inspect the knowledge graph in Obsidian; use export_ontology to serialize the semantic layer as a single portable YAML/JSON file. Writes into AUTO_ONTOLOGY_VAULT/auto-ontology (or ~/.auto-ontology-mcp/wiki/<datasource>), prunes only previously generated pages, never alters originals, and is strictly one-way (Obsidian edits are not read back)."""
        return svc.export_wiki(datasource)

    # -------------------------------------------------------------- reading
    @tool(READ, title="Search ontology catalog by keyword")
    def search_ontology(
        query: Annotated[str, Field(description="Lexical keywords to match: a business noun, metric, column name, or stored value.")],
        kinds: Annotated[
            list[Literal["term", "attribute", "sql_attribute", "glossary", "table", "example", "source", "value"]] | None,
            Field(description="Restrict matches to specific catalog object kinds (default: all kinds)."),
        ] = None,
        limit: Annotated[int, Field(ge=1, le=50, description="Maximum ranked hits to return (1-50).")] = 10,
        datasource: DatasourceArg = None,
    ) -> dict[str, Any]:
        """Search one datasource's ontology catalog across Terms, mapped columns, SQL attributes, glossary entries, verified examples, sources, unmodeled tables, and (in the knowledge store) record values and top full-text hits. Use as the primary discovery tool when you do not know exact names; use list_terms to browse all Terms, get_term for one known Term, search_text for deep knowledge-store prose search, and search only for ChatGPT connector IDs. Uses lexical scoring by default and blends local embeddings when the semantic extra is installed."""
        return svc.search_ontology(query, list(kinds) if kinds else None, limit, datasource)

    @tool(READ, title="Get full details of one Term")
    def get_term(
        term: Annotated[str, Field(description="Exact Term name, synonym, or id.")],
        datasource: DatasourceArg = None,
    ) -> dict[str, Any]:
        """Retrieve complete business and schema details for one Term: definition, status, synonyms, markdown body, mapped columns with sample values/formats, SQL attributes, join conditions, typed relations, sources, and knowledge record counts. Use to explain 'what is X' or inspect one known Term; use search_ontology when the Term name is unknown, list_terms to browse all Terms, describe_table for raw physical columns, and get_query_context when gathering multi-Term SQL context. Fails with closest Term suggestions if unmatched."""
        return svc.get_term(term, datasource)

    @tool(READ, title="Describe a physical database table")
    def describe_table(
        table: Annotated[str, Field(description="Physical table name, schema-qualified if needed (e.g. 'sales.orders').")],
        datasource: DatasourceArg = None,
    ) -> dict[str, Any]:
        """Inspect one physical table's complete catalog metadata: all columns (including unmapped ones), keys, junction flag, attached Terms and attributes, join relationships, and SQL attributes. Use to verify raw table structure or unmapped columns; use get_term for business meaning, get_query_context for multi-Term SQL generation, and get_source for knowledge provenance. Fails with closest table name suggestions if the table does not exist."""
        return svc.describe_table(table, datasource)

    @tool(READ, title="Find shortest join path between Terms")
    def find_join_path(
        source: Annotated[str, Field(description="Starting Term name or physical table name.")],
        target: Annotated[str, Field(description="Destination Term name or physical table name.")],
        datasource: DatasourceArg = None,
    ) -> dict[str, Any]:
        """Compute the shortest chain of accepted relationships between two Terms or tables, returning ON conditions, a ready FROM/JOIN clause, fan-out warnings, shared-parent caveats, and alternative join roles (e.g. billing vs delivery city). Use when checking one specific pairwise join; use get_query_context to assemble columns, attributes, and joins for a full multi-Term query, and list_relationships to review unapproved joins. Returns found=false when no accepted path connects them."""
        return svc.find_join_path(source, target, datasource)

    # ------------------------------------------------------------ answering
    @tool(READ, title="Check if the ontology covers a question")
    def check_answerable(
        question: Annotated[str, Field(description="The user's natural-language question.")],
        entities: Annotated[
            list[str] | None,
            Field(description="1-5 entity phrases extracted from the question in the ontology's language. " + guidance.ENTITY_RULES),
        ] = None,
        datasource: DatasourceArg = None,
    ) -> dict[str, Any]:
        """Evaluate before writing SQL whether the ontology covers every entity in a user question, returning per-entity coverage (covered, covered_by_value, weak, or not_covered), an overall verdict, and next steps. Call first in the answering workflow and then call get_query_context for covered Terms; do not substitute uncovered metrics. Use search_ontology for ad-hoc exploration instead of question coverage checks."""
        return svc.check_answerable(question, entities, datasource)

    @tool(READ, title="Assemble SQL query context for Terms")
    def get_query_context(
        terms: Annotated[list[str], Field(description="List of Term names, synonyms, or ids needed to answer the question.")],
        question: Annotated[str | None, Field(description="User question used to retrieve matching verified example SQL queries.")] = None,
        datasource: DatasourceArg = None,
    ) -> dict[str, Any]:
        """Assemble everything needed to write SQL for a set of Terms: qualified tables, mapped columns with sample values/formats, SQL attributes, the connecting join path (with FROM/JOIN SQL and role notes), verified example queries, glossary entries, and dialect rules. Call after check_answerable instead of calling get_term, describe_table, and find_join_path separately; use lookup_values next for exact filter literals. Lists unknown inputs under unresolved with suggestions, and fails if zero Terms resolve."""
        return svc.get_query_context(terms, question, datasource)

    @tool(READ_DB, title="Look up stored values in a column")
    def lookup_values(
        column: Annotated[str, Field(description="Target column formatted as 'table.column' (or 'schema.table.column').")],
        contains: Annotated[str | None, Field(description="Case-insensitive substring to filter values; omit to return the most frequent values.")] = None,
        limit: Annotated[int, Field(ge=1, le=100, description="Maximum distinct values to return (1-100).")] = 20,
        datasource: DatasourceArg = None,
    ) -> dict[str, Any]:
        """Query live distinct values stored in one column ('table.column') to ground exact literal filters (places, products, statuses) before writing SQL. Use after get_query_context whenever a WHERE filter value is uncertain; use search_ontology to find which table or column holds a concept. Returns matching values with row counts, close spelling suggestions when contains matches nothing, or top values when contains is omitted."""
        return svc.lookup_values(column, contains, limit, datasource)

    @tool(READ_DB, title="Validate a SQL query against the ontology")
    def validate_sql(
        sql: Annotated[str, Field(description="Single read-only SELECT statement in the datasource's SQL dialect.")],
        datasource: DatasourceArg = None,
        question: Annotated[
            str | None,
            Field(description="User question; percentages mentioned in it are checked against the numeric scale of filtered columns."),
        ] = None,
    ) -> dict[str, Any]:
        """Validate a single SELECT query without returning rows: enforces the read-only guard, checks table/column names, verifies joins and default roles, flags near-miss literals, percent-vs-fraction scale mismatches, and one-to-many fan-out under aggregations, and runs a database dry run. Always call before run_sql and pass question when percentages appear; use run_sql only after fixing every reported error. Returns ok, errors, warnings, and the draft/certified status of relied-on Terms."""
        return svc.validate_sql(sql, datasource, question)

    @tool(READ_DB, title="Execute a read-only SQL query")
    def run_sql(
        sql: Annotated[str, Field(description="Single read-only SELECT statement in the datasource's SQL dialect.")],
        question: Annotated[
            str | None,
            Field(description="User question logged with the query for provenance and used to check percent-vs-fraction thresholds."),
        ] = None,
        max_rows: Annotated[int, Field(ge=1, description="Maximum rows to return (>= 1; capped by the server's AUTO_ONTOLOGY_MAX_ROWS limit).")] = 100,
        datasource: DatasourceArg = None,
    ) -> dict[str, Any]:
        """Execute one read-only SELECT query against a registered datasource and return columns, rows, row_count, truncated flag, relied-on Terms (with draft/certified status), and warnings. Call after validate_sql passes; use lookup_values to inspect single-column values instead. Refuses writes, DDL, multiple statements, and uncataloged tables on a read-only connection, and logs the query with question for provenance."""
        return svc.run_sql(sql, question, max_rows, datasource)

    @tool(WRITE_DB, title="Save a verified example SQL query")
    def save_example_query(
        question: Annotated[str, Field(description="Natural-language question in the user's words.")],
        sql: Annotated[str, Field(description="Validated read-only SELECT query that correctly answered the question.")],
        note: Annotated[str | None, Field(description="Optional rationale explaining business rules, filters, or exclusions in this SQL.")] = None,
        datasource: DatasourceArg = None,
    ) -> dict[str, Any]:
        """Store a user-confirmed question and SQL pair so get_query_context surfaces it for similar future questions and suggest_sql_attributes can mine reusable logic from it. Call only after the user confirms an answer is correct; use save_sql_attribute for standalone reusable expressions instead. Validates the SQL against the ontology and database first (saving nothing if validation fails); saving the same question again updates its SQL in place."""
        return svc.save_example_query(question, sql, note, datasource)

    # --------------------------------------------------------- import/export
    @tool(READ, title="Export semantic layer as YAML or JSON")
    def export_ontology(
        format: Annotated[Literal["yaml", "json"], Field(description="Output serialization format: 'yaml' (human-readable) or 'json'.")] = "yaml",
        datasource: DatasourceArg = None,
    ) -> dict[str, Any]:
        """Serialize one datasource's entire semantic layer as a single YAML or JSON document containing tables, relationships, Terms with column mappings, SQL attributes, concepts, typed relations, glossary entries, and example queries. Use for backup, review, version control, or migrating via import_ontology; use export_wiki to render Obsidian markdown pages instead. Does not include structured record rows from the knowledge store."""
        return svc.export_ontology(datasource, format)

    @tool(WRITE_DESTRUCTIVE, title="Import semantic layer document")
    def import_ontology(
        document: Annotated[str, Field(description="YAML or JSON ontology document previously produced by export_ontology.")],
        datasource: DatasourceArg = None,
    ) -> dict[str, Any]:
        """Import a YAML or JSON semantic layer document produced by export_ontology into an already scanned datasource, matching tables and columns by name. Prerequisite: register and scan the target datasource first with add_datasource or scan_datasource; use save_table_terms or update_term for incremental single-table edits. Upserts same-named items and replaces each imported table's Terms as in save_table_terms; items with missing tables, columns, or invalid SQL are skipped and listed (partial apply). Recreates missing knowledge entities empty without importing record rows."""
        return svc.import_ontology(document, datasource)

    # ------------------------------------------- ChatGPT connectors / research
    @tool(READ, title="Search ontology documents for connectors")
    def search(
        query: Annotated[str, Field(description="Lexical search words (Term names, table names, columns, or source titles) matched across all datasources.")],
    ) -> dict[str, Any]:
        """Search Terms, tables, and sources across all datasources and return compact {id, title, url} connector records for follow-up with fetch. Intended exclusively for ChatGPT connectors and deep-research clients that require the search/fetch pair; all other MCP clients should use search_ontology (catalog search) or search_text (knowledge-store full-text search) instead. Returns a results list with opaque document ids."""
        return {"results": svc.search_documents(query)}

    @tool(READ, title="Fetch one ontology document by connector ID")
    def fetch(
        id: Annotated[str, Field(description="Opaque document id returned by search, e.g. 'term:term_ab12', 'table:tbl_1', or 'source:3'.")],
    ) -> dict[str, Any]:
        """Retrieve the full text and metadata of one ontology document (a Term, physical table, or knowledge source) using an opaque id returned by search. Intended exclusively as the second step for ChatGPT connectors and deep-research clients; all other MCP clients should call get_term, describe_table, or get_source directly for structured JSON. Fails if the id is unknown or malformed."""
        return svc.fetch_document(id)

    # --------------------------------------------------------------- prompts
    @mcp.prompt(name="build_ontology", description="Walk through modeling a datasource end to end.")
    def build_ontology_prompt(datasource: str = "") -> str:
        target = f" for datasource '{datasource}'" if datasource else ""
        return (
            f"Build the semantic layer{target} with the auto-ontology tools.\n"
            "1. check_readiness. If not scanned, scan_datasource.\n"
            "2. list_relationships(status='proposed') and decide each with set_relationship; add relationships for "
            "unlinked_id_columns whose meaning is clear; set is_default where a table has several roles.\n"
            "3. Loop: get_modeling_worklist → save_table_terms for every table returned, until remaining is 0.\n"
            "4. suggest_sql_attributes → save_sql_attribute for the ones with business meaning.\n"
            "5. Summarize what you built and list what a person should review before certifying.\n\n"
            + guidance.TERM_RULES
        )

    @mcp.prompt(name="answer_question", description="Answer a data question through the ontology.")
    def answer_question_prompt(question: str) -> str:
        return (
            f"Answer this question from the data: {question}\n\n"
            "Follow: check_answerable → get_query_context → lookup_values (for literals) → write SQL → validate_sql → "
            "run_sql → check the rows → answer with the SQL.\n\n"
            + guidance.SQL_RULES
            + "\n"
            + guidance.ANSWER_CHECKLIST
        )

    @mcp.prompt(name="remember", description="Store what I shared (files, images, notes) in the knowledge store.")
    def remember_prompt(what: str = "") -> str:
        subject = f" about: {what}" if what else ""
        return (
            f"Read what I shared in this conversation{subject} and store it with the auto-ontology tools.\n"
            "1. search_ontology(datasource='knowledge') for the things involved; reuse existing Terms.\n"
            "2. Facts to count or compare: define_entity if needed, then add_records with source and _evidence.\n"
            "3. Ideas and explanations: save_concept with a definition, a body and typed relations, with the source.\n"
            "4. Documents (articles, papers, notes): keep the full text as the original in Obsidian (source.original, "
            "original_text or original_file) and save_passages for the excerpts and section summaries worth finding "
            "again, each with its heading or locator and the Terms it is about.\n"
            "5. Tell me what you stored, what you were unsure about, and anything you left out.\n\n"
            + guidance.KNOWLEDGE_RULES
        )

    @mcp.prompt(name="review_ontology", description="Find gaps and risks in the current ontology.")
    def review_ontology_prompt(datasource: str = "") -> str:
        target = f" of '{datasource}'" if datasource else ""
        return (
            f"Review the ontology{target}: call check_readiness, export_ontology and list_relationships(status='all'). "
            "Report Terms whose descriptions do not state their grain, attributes with unclear names, joins with "
            "roles but no default, unlinked identifier columns, measures without SQL attributes, and anything a "
            "person should certify. For the knowledge store also report empty (stub) concepts, records without a "
            "source, likely duplicate records or Terms, and concepts whose bodies contradict each other. "
            "Propose concrete fixes but do not certify or delete anything yourself."
        )

    @mcp.resource("ontology://guide", name="guide", description="How to use this server.", mime_type="text/markdown")
    def guide() -> str:
        return "\n\n".join(
            [
                "# Auto Ontology MCP",
                instructions,
                "## Modeling rules\n" + guidance.TERM_RULES,
                "## Relationship review\n" + guidance.RELATIONSHIP_RULES,
                "## SQL attributes\n" + guidance.SQL_ATTRIBUTE_RULES,
                "## Writing SQL\n" + guidance.SQL_RULES,
                "## Checking answers\n" + guidance.ANSWER_CHECKLIST,
                "## Storing knowledge\n" + guidance.KNOWLEDGE_RULES,
            ]
        )

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "version": __version__})

    mcp.service = svc  # type: ignore[attr-defined]
    return mcp
