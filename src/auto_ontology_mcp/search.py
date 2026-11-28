"""Lexical and optional hybrid semantic search over the ontology.

By default this server runs without an embedding model, scoring token overlap
(camel/snake-case split, singularized), trigram similarity for typos and
inflected words, and phrase containment with weights per field. When the
optional ``semantic`` extra (``fastembed`` + ``sqlite-vec``) is installed,
``search`` blends lexical scores with local vector similarity to improve recall
on synonyms, paraphrases, and multilingual queries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .naming import QUESTION_WORDS, normalize_text, tokens, trigram_similarity
from .semantic import ONTOLOGY_MIN_SEMANTIC, blend_ontology_score

KINDS = ("term", "attribute", "sql_attribute", "glossary", "table", "example", "source")


@dataclass(frozen=True)
class Field:
    """A field's text, tokenized and normalized once, when its document is built."""

    tokens: frozenset[str]
    norm: str
    weight: float


@dataclass
class Doc:
    kind: str
    id: str
    title: str
    fields: list[tuple[str, float]]
    payload: dict[str, Any] = field(default_factory=dict)
    prepared: list[Field] = field(init=False, repr=False, default_factory=list)

    def __post_init__(self) -> None:
        # Scoring a document used to tokenize every field on every query, and a question is scored
        # against every document once per entity. The tokens do not change, so they are made here.
        # An empty text or one with no tokens scores 0, and dropping it changes nothing: the
        # second-best bonus adds 0 for it.
        for text, weight in self.fields:
            found = tokens(text) if text else []
            if found:
                self.prepared.append(Field(frozenset(found), normalize_text(text), weight))


def _token_match(q: str, f_set: set[str]) -> float:
    """How well one query token matches a field: 1.0 exact, else the best fuzzy match."""
    if q in f_set:
        return 1.0
    best = 0.0
    for f in f_set:
        if len(q) >= 2 and len(f) >= 2 and not q.isascii() and (f in q or q in f):
            best = max(best, 0.8)
        elif abs(len(f) - len(q)) <= 3:
            sim = trigram_similarity(q, f)
            if sim >= 0.45:
                best = max(best, sim * 0.8)
    return best


def _field_score(
    q_tokens: list[str], q_norm: str, field: Field, dropped: list[str] | None = None
) -> float:
    f_set = field.tokens
    matches = [_token_match(q, f_set) for q in q_tokens]
    # A question word the field itself carries is part of the name the caller asked
    # for ("Order No", "Has Children"), so it counts like any other query token, but
    # only once a content word matches the field too: "no" alone must not make
    # "Order No" a hit for "Which employees have no manager?".
    # One the field does not carry costs a little instead of being free.
    present = [d for d in (dropped or []) if d in f_set] if any(m > 0 for m in matches) else []
    q_tokens = [*q_tokens, *present]
    matches += [1.0] * len(present)
    unmatched = len(dropped or []) - len(present)
    coverage = sum(matches) / len(q_tokens)
    if len(q_norm) >= 3 and q_norm in field.norm:
        coverage = max(coverage, 1.0)
    # Prefer tight matches: "Order" beats "Order Line" for the query "order".
    extra = max(0, len(f_set) - len(set(q_tokens))) + unmatched
    return coverage * (1.0 / (1.0 + 0.04 * extra))


def score(query: str, doc: Doc) -> float:
    # A question's auxiliaries do not decide the match, but the field may carry
    # them as part of its name ("Order No"), so they are handed to the field
    # scorer rather than dropped outright. The field text itself is tokenized as
    # written, which is why a column named "Make" keeps scoring.
    raw = tokens(query)
    if not raw:
        raw = tokens(query, drop_stopwords=False)
    q_tokens = [t for t in raw if t not in QUESTION_WORDS]
    dropped = [t for t in raw if t in QUESTION_WORDS]
    if not q_tokens:
        q_tokens, dropped = raw, []
    if not q_tokens:
        return 0.0
    q_norm = normalize_text(query).strip()
    scored = sorted((_field_score(q_tokens, q_norm, field, dropped) * field.weight for field in doc.prepared), reverse=True)
    if not scored:
        return 0.0
    best = scored[0] + (0.15 * scored[1] if len(scored) > 1 else 0.0)
    return round(min(1.0, best), 4)


def build_docs(
    terms: list[dict[str, Any]],
    attributes: list[dict[str, Any]],
    sql_attributes: list[dict[str, Any]],
    glossary: list[dict[str, Any]],
    tables: list[dict[str, Any]],
    examples: list[dict[str, Any]],
    relations: list[dict[str, Any]] | None = None,
    sources: list[dict[str, Any]] | None = None,
) -> list[Doc]:
    docs: list[Doc] = []
    attrs_by_term: dict[str, list[dict[str, Any]]] = {}
    for a in attributes:
        attrs_by_term.setdefault(a["term_id"], []).append(a)
    links: dict[str, list[str]] = {}
    for r in relations or []:
        links.setdefault(r["source_term_id"], []).append(f"{r['relation'].replace('_', ' ')} {r['target_name']}")
    modeled_tables = {t.get("primary_table_id") for t in terms} | {a["table_id"] for a in attributes}

    for t in terms:
        syn = " ".join(t.get("synonyms") or [])
        docs.append(
            Doc(
                "term", t["id"], t["name"],
                [(t["name"], 1.0), (syn, 0.95), (t.get("table_name") or "", 0.6), (t.get("description") or "", 0.45),
                 (" ".join(a["display_name"] for a in attrs_by_term.get(t["id"], [])), 0.3),
                 ((t.get("body") or "")[:20000], 0.35), (" ".join(links.get(t["id"], [])), 0.3)],
                {"status": t["status"], "term_kind": t.get("kind") or "entity", "table": t.get("table_name"),
                 "description": t.get("description")},
            )
        )
    for a in attributes:
        samples = a.get("sample_values")
        sample_text = " ".join(str(v) for v in samples) if isinstance(samples, list) else ""
        docs.append(
            Doc(
                "attribute", a["id"], f"{a['term_name']} › {a['display_name']}",
                [(a["display_name"], 0.9), (a["column_name"], 0.75), (f"{a['term_name']} {a['display_name']}", 0.85),
                 (a.get("description") or "", 0.4), (sample_text, 0.35)],
                {"term": a["term_name"], "column": f"{a['table_name']}.{a['column_name']}", "data_type": a.get("data_type")},
            )
        )
    for s in sql_attributes:
        docs.append(
            Doc(
                "sql_attribute", s["id"], f"{s['term_name']} › {s['name']}",
                [(s["name"], 1.0), (s.get("description") or "", 0.5), (s["expression"], 0.25), (s["term_name"], 0.3)],
                {"term": s["term_name"], "sql_kind": s["kind"], "status": s["status"], "expression": s["expression"]},
            )
        )
    for g in glossary:
        docs.append(Doc("glossary", g["id"], g["name"], [(g["name"], 1.0), (g["definition"], 0.6)], {"definition": g["definition"]}))
    for t in tables:
        if t["id"] in modeled_tables:
            continue
        docs.append(
            Doc("table", t["id"], t["name"], [(t["name"], 0.8), (t.get("description") or "", 0.4)],
                {"schema": t.get("schema_name"), "note": "table has no Term yet"})
        )
    for src in sources or []:
        docs.append(
            Doc("source", str(src["id"]), src["title"],
                [(src["title"], 0.9), (src.get("summary") or "", 0.45), (src.get("uri") or "", 0.3), (src.get("content") or "", 0.3)],
                {"source_kind": src.get("kind"), "uri": src.get("uri")})
        )
    for e in examples:
        docs.append(Doc("example", e["id"], e["question"], [(e["question"], 0.9), (e.get("note") or "", 0.4)], {"sql": e["sql"]}))
    return docs


def doc_text(doc: Doc, max_chars: int = 2000) -> str:
    """Canonical text representation of a Doc for local vector indexing."""
    parts: list[str] = []
    seen: set[str] = set()
    for raw in (doc.title, *(text for text, _ in doc.fields)):
        cleaned = (raw or "").strip()
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        parts.append(cleaned)
    joined = "\n".join(parts)
    return joined[:max_chars] if len(joined) > max_chars else joined


# How many leading fields of a Doc hold its name: a Term's name and its synonyms, else the
# name alone. Kinds left out (examples, sources) are long texts with no short name worth
# a vector of its own.
NAME_FIELDS = {"term": 2, "attribute": 1, "sql_attribute": 1, "glossary": 1, "table": 1}
NAME_SUFFIX = "#name"


def name_text(doc: Doc) -> str:
    """The Doc's bare name (and a Term's synonyms) for a second, name-only vector.

    The full ``doc_text`` mixes the name with sample values, SQL and column names, which
    pulls a short query such as "turnover" or "매출" far from it: "Order Line › Revenue" with
    its samples scores 0.37 for "매출", the word "Revenue" alone 0.70. Returns "" when the
    kind has no name vector or the name is all the document holds (the full vector covers it).
    """
    count = NAME_FIELDS.get(doc.kind)
    if not count:
        return ""
    parts = list(dict.fromkeys(t.strip() for t, _ in doc.fields[:count] if t and t.strip()))
    text = "\n".join(parts)
    return "" if not text or text.casefold() == doc_text(doc).casefold() else text


def search(
    query: str,
    docs: list[Doc],
    kinds: list[str] | None = None,
    limit: int = 10,
    min_score: float = 0.2,
    *,
    semantic_scores: dict[tuple[str, str], float] | None = None,
    min_semantic: float = ONTOLOGY_MIN_SEMANTIC,
) -> list[dict[str, Any]]:
    wanted = set(kinds or KINDS)
    hits = []
    for doc in docs:
        if doc.kind not in wanted:
            continue
        lex = score(query, doc)
        sem = semantic_scores.get((doc.kind, doc.id)) if semantic_scores is not None else None
        s = blend_ontology_score(lex, sem, min_semantic=min_semantic) if semantic_scores is not None else lex
        if s >= min_score:
            hit: dict[str, Any] = {**doc.payload, "kind": doc.kind, "id": doc.id, "name": doc.title, "score": s}
            if sem is not None and sem >= min_semantic:
                hit["semantic_score"] = round(sem, 4)
                hit["match"] = "semantic" if lex < min_score else "hybrid"
            hits.append(hit)
    hits.sort(key=lambda h: (-h["score"], {"term": 0, "sql_attribute": 1, "attribute": 2, "glossary": 3, "example": 4, "source": 5, "table": 6}[h["kind"]]))
    return hits[:limit]
