"""Optional local vector embeddings (fastembed + sqlite-vec) for hybrid search.

When the optional ``semantic`` extra (``fastembed`` and ``sqlite-vec``) is not
installed, or when disabled via ``AUTO_ONTOLOGY_SEMANTIC=0``, this module stays
inert and search remains purely lexical (FTS5 bm25 + token/trigram matching).

Vectors are stored in a dedicated sidecar SQLite database (next to
``ontology.db``) so ``knowledge.sqlite`` and ``ontology.db`` stay standard
SQLite files readable without the ``sqlite-vec`` extension.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import logging
import math
import sqlite3
import struct
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Protocol, Sequence

from .store import now

logger = logging.getLogger(__name__)

DEFAULT_EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DEFAULT_MIN_SIMILARITY = 0.50
RRF_K = 60
# Ontology vector hits below this cosine leave the lexical score alone. Bare metric names
# sit just above it for short synonyms ("turnover" -> "Revenue" 0.517 with the default
# model), while unrelated queries stay under 0.36.
ONTOLOGY_MIN_SEMANTIC = 0.50
# search_ontology pairs a missed phrase with a later hit only this close (a share of the floor):
# "주문 상태" reaches "Order Status" at 0.31 with MiniLM, unrelated queries stay at or under 0.14.
LEARN_FLOOR_RATIO = 0.5

LARGER_EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-mpnet-base-v2"
# Each model spreads cosine similarity differently, so the floor that drops noise is per model.
# Measured on benchmarks/tasks.json semantic_search: unrelated queries stay at or under 0.14 with
# both models; the weakest true synonym ("turnover" -> "Revenue") is 0.52 with MiniLM and 0.46
# with mpnet. intfloat/multilingual-e5-small ranks well but squeezes every pair into 0.74-0.86,
# leaving no floor that separates noise, so it has no profile.
MODEL_MIN_SIMILARITY: dict[str, float] = {
    DEFAULT_EMBEDDING_MODEL: 0.50,
    LARGER_EMBEDDING_MODEL: 0.40,
}
# check_answerable needs a confident match: a vector hit counts only this far above the larger of
# the model's floor and the document's noise level (see NOISE_PROBES). Measured on
# benchmarks/tasks.json answerable_semantic (Korean phrases): the smallest margin that keeps
# precision at 1.0 is 0.30 with MiniLM and 0.40 with mpnet, which spreads unrelated words wider.
ANSWERABLE_MARGIN: dict[str, float] = {
    DEFAULT_EMBEDDING_MODEL: 0.30,
    LARGER_EMBEDDING_MODEL: 0.40,
}
DEFAULT_ANSWERABLE_MARGIN = 0.40
# Everyday words, one concept per row in several languages, that no business schema is about.
# A short name such as "Tag" sits at 0.8-0.9 from many of them ("재고", "광고", "주가" alike), so
# its cosine says nothing about a phrase. A document's noise level is its NOISE_RANK-th highest
# similarity across these concepts: high for such hub names, about 0.3 for ordinary ones.
NOISE_PROBES: tuple[tuple[str, ...], ...] = (
    ("날씨", "天気", "天气", "Wetter", "clima", "météo", "weather"),
    ("음악", "音楽", "音乐", "Musik", "música", "musique", "music"),
    ("바다", "海", "Meer", "océano", "océan", "ocean"),
    ("병원", "病院", "医院", "Krankenhaus", "hôpital", "hospital"),
    ("시집", "詩", "Gedicht", "poesía", "poésie", "poetry"),
    ("화산", "火山", "Vulkan", "volcán", "volcano"),
    ("요리법", "レシピ", "食谱", "Rezept", "receta", "recipe"),
    ("학교", "学校", "Schule", "escuela", "école", "school"),
    ("은하", "銀河", "Galaxie", "galaxia", "galaxy"),
    ("의자", "椅子", "Stuhl", "silla", "chaise", "chair"),
    ("축구", "サッカー", "足球", "Fußball", "fútbol", "soccer"),
    ("숲", "森", "森林", "Wald", "bosque", "forêt", "forest"),
    ("다리", "橋", "桥", "Brücke", "puente", "pont", "bridge"),
    ("꿈", "夢", "梦", "Traum", "sueño", "rêve", "dream"),
    ("빵", "パン", "面包", "Brot", "pan", "pain", "bread"),
    ("겨울", "冬", "Winter", "invierno", "hiver", "winter"),
)
NOISE_RANK = 3
# Short names accepted by AUTO_ONTOLOGY_EMBEDDING_MODEL.
MODEL_ALIASES: dict[str, str] = {
    "minilm": DEFAULT_EMBEDDING_MODEL,
    "default": DEFAULT_EMBEDDING_MODEL,
    "mpnet": LARGER_EMBEDDING_MODEL,
    "large": LARGER_EMBEDDING_MODEL,
}


def resolve_model_name(name: str | None) -> str:
    """Expand a short alias (``mpnet``) to the fastembed model id; other names pass through."""
    cleaned = (name or "").strip()
    return MODEL_ALIASES.get(cleaned.casefold(), cleaned) if cleaned else DEFAULT_EMBEDDING_MODEL


def model_min_similarity(model_name: str, override: float | None = None) -> float:
    """The cosine floor below which a vector hit is noise for ``model_name``."""
    if override is not None:
        return float(override)
    return MODEL_MIN_SIMILARITY.get(model_name, DEFAULT_MIN_SIMILARITY)


def answerable_margin(model_name: str) -> float:
    """How far above max(floor, noise) a vector hit must be for check_answerable with ``model_name``."""
    return ANSWERABLE_MARGIN.get(model_name, DEFAULT_ANSWERABLE_MARGIN)


_MODEL_LOCK = threading.Lock()
_MODEL_CACHE: dict[tuple[str, str | None], Any] = {}


def _sqlite_extension_supported() -> bool:
    return hasattr(sqlite3.Connection, "enable_load_extension")


def fastembed_available() -> bool:
    """Return True when ``fastembed`` is importable."""
    return importlib.util.find_spec("fastembed") is not None


def sqlite_vec_available() -> bool:
    """Return True when ``sqlite-vec`` is installed and SQLite can load extensions."""
    return importlib.util.find_spec("sqlite_vec") is not None and _sqlite_extension_supported()


def semantic_dependencies_available() -> bool:
    """Return True when both optional semantic dependencies are usable."""
    return fastembed_available() and sqlite_vec_available()


def normalize_vector(vec: Sequence[float]) -> list[float]:
    values = [float(x) for x in vec]
    norm = math.sqrt(sum(x * x for x in values))
    if norm <= 0.0:
        return values
    return [x / norm for x in values]


def serialize_float32(vec: Sequence[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *(float(x) for x in vec))


def deserialize_float32(blob: bytes) -> list[float]:
    if not blob:
        return []
    count = len(blob) // 4
    return list(struct.unpack(f"<{count}f", blob))


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    sim = dot / (na * nb)
    return max(-1.0, min(1.0, sim))


def content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:24]


def rrf_fuse(
    lexical: Sequence[tuple[str, float]],
    semantic: Sequence[tuple[str, float]],
    *,
    k: int = RRF_K,
    lexical_weight: float = 1.0,
    semantic_weight: float = 0.85,
    lexical_weights: dict[str, float] | None = None,
) -> dict[str, float]:
    """Combine lexical and semantic ranked lists with Reciprocal Rank Fusion.

    Scores are scaled by ``(k + 1)`` so a rank-1 lexical-only item scores
    ``lexical_weight`` (1.0), a rank-1 semantic-only item scores
    ``semantic_weight`` (0.85), and an item ranked #1 in both scores 1.85.
    """
    scale = float(k + 1)
    scores: dict[str, float] = {}
    for rank, (key, _) in enumerate(lexical, start=1):
        w = lexical_weights.get(key, lexical_weight) if lexical_weights else lexical_weight
        scores[key] = scores.get(key, 0.0) + scale * (w / (k + rank))
    for rank, (key, _) in enumerate(semantic, start=1):
        scores[key] = scores.get(key, 0.0) + scale * (semantic_weight / (k + rank))
    return {key: round(val, 4) for key, val in scores.items()}


def blend_ontology_score(
    lexical_score: float,
    semantic_score: float | None,
    *,
    min_semantic: float = ONTOLOGY_MIN_SEMANTIC,
    semantic_only_scale: float = 0.85,
    boost_weight: float = 0.35,
) -> float:
    """Blend a [0, 1] lexical score with a [0, 1] cosine similarity into [0, 1].

    - Weak semantic matches (below ``min_semantic``) do not alter lexical scores.
    - Pure lexical matches (acronyms, exact column names) keep their full score.
    - Pure semantic matches (paraphrases, translations) score ``semantic * scale``.
    - Items matched by both receive a bounded noisy-OR boost.
    """
    lex = max(0.0, min(1.0, float(lexical_score)))
    if semantic_score is None or semantic_score < min_semantic:
        return round(lex, 4)
    sem = max(0.0, min(1.0, float(semantic_score)))
    sem_base = sem * semantic_only_scale
    if lex <= 0.0:
        return round(min(1.0, sem_base), 4)
    base = max(lex, sem_base)
    bonus = min(lex, sem_base) * boost_weight
    return round(min(1.0, base + (1.0 - base) * bonus), 4)


def model_disk_bytes(model_name: str, cache_dir: Path | None = None) -> int | None:
    """Bytes the downloaded fastembed model files take, or None when it is not in the cache."""
    try:
        fastembed = importlib.import_module("fastembed")
        source = next(
            (m.get("sources", {}).get("hf") for m in fastembed.TextEmbedding.list_supported_models() if m["model"] == model_name),
            None,
        )
    except Exception:  # noqa: BLE001
        return None
    if not source:
        return None
    import tempfile

    root = Path(cache_dir) if cache_dir else Path(tempfile.gettempdir()) / "fastembed_cache"
    snapshots = root / f"models--{source.replace('/', '--')}" / "snapshots"
    if not snapshots.is_dir():
        return None
    return sum(f.stat().st_size for f in snapshots.rglob("*") if f.is_file())


def _concept_maxima(vectors: list[list[float]], probes: list[list[list[float]]]) -> list[list[float]]:
    """For each vector, its highest cosine to any word of each probe concept (vectors are unit length)."""
    try:
        import numpy as np

        docs = np.asarray(vectors, dtype=np.float32)
        cols = []
        for row in probes:
            words = np.asarray(row, dtype=np.float32)
            cols.append((docs @ words.T).max(axis=1))
        return np.stack(cols, axis=1).tolist()
    except ImportError:
        return [[max(cosine_similarity(v, w) for w in row) for row in probes] for v in vectors]


class Embedder(Protocol):
    @property
    def model_name(self) -> str: ...

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class FastEmbedder:
    """Lazy wrapper around ``fastembed.TextEmbedding`` with process-wide model caching."""

    def __init__(self, model_name: str = DEFAULT_EMBEDDING_MODEL, cache_dir: Path | None = None) -> None:
        self._model_name = model_name
        self._cache_dir = str(cache_dir) if cache_dir else None

    @property
    def model_name(self) -> str:
        return self._model_name

    def _model(self) -> Any:
        key = (self._model_name, self._cache_dir)
        cached = _MODEL_CACHE.get(key)
        if cached is not None:
            return cached
        with _MODEL_LOCK:
            cached = _MODEL_CACHE.get(key)
            if cached is not None:
                return cached
            fastembed = importlib.import_module("fastembed")
            kwargs: dict[str, Any] = {"model_name": self._model_name}
            if self._cache_dir:
                kwargs["cache_dir"] = self._cache_dir
            model = fastembed.TextEmbedding(**kwargs)
            _MODEL_CACHE[key] = model
            return model

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        items = list(texts)
        if not items:
            return []
        model = self._model()
        fn = getattr(model, "passage_embed", None) or model.embed
        return [normalize_vector(v) for v in fn(items)]

    def embed_query(self, text: str) -> list[float]:
        vecs = self.embed_queries([text])
        return vecs[0] if vecs else []

    def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        items = list(texts)
        if not items:
            return []
        model = self._model()
        fn = getattr(model, "query_embed", None) or model.embed
        return [normalize_vector(v) for v in fn(items)]


@dataclass(frozen=True)
class VectorHit:
    kind: str
    item_id: str
    similarity: float


class VectorIndex:
    """SQLite-backed local vector index using ``sqlite-vec`` (``vec0``) and ``fastembed``.

    Each item is keyed by ``(scope, kind, item_id)`` and tracked by a SHA-256
    content hash so repeated syncs only embed newly added or modified texts.
    """

    def __init__(
        self,
        path: Path,
        *,
        enabled: bool | None = None,
        model_name: str = DEFAULT_EMBEDDING_MODEL,
        cache_dir: Path | None = None,
        embedder: Embedder | None = None,
        use_sqlite_vec: bool | None = None,
        min_similarity: float | None = None,
    ) -> None:
        self.path = Path(path)
        self._enabled = enabled
        self._embedder = embedder
        self._model_name = embedder.model_name if embedder is not None else resolve_model_name(model_name)
        self._min_similarity_override = min_similarity
        self._cache_dir = cache_dir
        self._use_sqlite_vec = use_sqlite_vec
        self._lock = threading.RLock()
        self._sqlite_vec_loaded: bool | None = None
        self._last_error: str | None = None

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def min_similarity(self) -> float:
        """Cosine floor for this index's model (``AUTO_ONTOLOGY_MIN_SIMILARITY`` overrides it)."""
        return model_min_similarity(self._model_name, self._min_similarity_override)

    @property
    def answerable_margin(self) -> float:
        """check_answerable's margin over max(floor, noise) for this index's model."""
        return answerable_margin(self._model_name)

    def _should_use_sqlite_vec(self) -> bool:
        if self._use_sqlite_vec is not None:
            return self._use_sqlite_vec
        return sqlite_vec_available()

    def _can_load_sqlite_vec(self) -> bool:
        if self._sqlite_vec_loaded is not None:
            return self._sqlite_vec_loaded
        if not sqlite_vec_available():
            self._sqlite_vec_loaded = False
            return False
        con = sqlite3.connect(":memory:")
        try:
            sqlite_vec = importlib.import_module("sqlite_vec")
            con.enable_load_extension(True)
            sqlite_vec.load(con)
            con.enable_load_extension(False)
            self._sqlite_vec_loaded = True
        except Exception as exc:  # noqa: BLE001
            self._last_error = str(exc)
            logger.warning("sqlite-vec extension could not be loaded: %s", exc)
            self._sqlite_vec_loaded = False
        finally:
            con.close()
        return self._sqlite_vec_loaded

    def is_available(self) -> bool:
        """True when hybrid semantic indexing and search should run."""
        if self._enabled is False:
            return False
        if self._embedder is not None:
            if self._use_sqlite_vec is True:
                return self._can_load_sqlite_vec()
            return True
        if not semantic_dependencies_available():
            return False
        return self._can_load_sqlite_vec()

    def status(self) -> dict[str, Any]:
        """Compact readiness view describing semantic search availability."""
        if self._enabled is False:
            return {"enabled": False, "reason": "disabled by AUTO_ONTOLOGY_SEMANTIC"}
        if self.is_available():
            backend = "sqlite-vec" if self._should_use_sqlite_vec() and self._can_load_sqlite_vec() else "sqlite-blob"
            return {"enabled": True, "model": self._model_name, "backend": backend, "min_similarity": self.min_similarity}
        missing = []
        if self._embedder is None and not fastembed_available():
            missing.append("fastembed")
        if not sqlite_vec_available():
            missing.append("sqlite-vec" if _sqlite_extension_supported() else "sqlite3 extension loading")
        reason = (
            f"optional extra not installed ({', '.join(missing)}); install with `auto-ontology-mcp[semantic]`"
            if missing
            else (self._last_error or "semantic backend unavailable")
        )
        return {"enabled": False, "reason": reason}

    def _get_embedder(self) -> Embedder:
        if self._embedder is None:
            self._embedder = FastEmbedder(self._model_name, cache_dir=self._cache_dir)
        return self._embedder

    @contextmanager
    def _connect(self) -> Iterator[tuple[sqlite3.Connection, bool]]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode = WAL")
        use_vec0 = self._should_use_sqlite_vec() and self._can_load_sqlite_vec()
        if use_vec0:
            sqlite_vec = importlib.import_module("sqlite_vec")
            con.enable_load_extension(True)
            try:
                sqlite_vec.load(con)
            finally:
                con.enable_load_extension(False)
        try:
            self._ensure_base_schema(con)
            yield con, use_vec0
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    @staticmethod
    def _ensure_base_schema(con: sqlite3.Connection) -> None:
        con.execute("CREATE TABLE IF NOT EXISTS vec_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS vec_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope TEXT NOT NULL,
                kind TEXT NOT NULL,
                item_id TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (scope, kind, item_id)
            )
            """
        )
        con.execute("CREATE INDEX IF NOT EXISTS idx_vec_items_scope_kind ON vec_items (scope, kind)")

    @staticmethod
    def _meta(con: sqlite3.Connection) -> dict[str, str]:
        return {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM vec_meta")}

    @staticmethod
    def _reset_vectors(con: sqlite3.Connection, use_vec0: bool, model: str, dim: int | None) -> None:
        con.execute("DROP TABLE IF EXISTS vec_embeddings")
        con.execute("DROP TABLE IF EXISTS vec_blobs")
        con.execute("DELETE FROM vec_items")
        con.execute("INSERT OR REPLACE INTO vec_meta (key, value) VALUES ('model', ?)", (model,))
        con.execute("INSERT OR REPLACE INTO vec_meta (key, value) VALUES ('backend', ?)", ("vec0" if use_vec0 else "blob",))
        if dim is not None:
            con.execute("INSERT OR REPLACE INTO vec_meta (key, value) VALUES ('dim', ?)", (str(dim),))
            VectorIndex._ensure_vector_table(con, use_vec0, dim)
        else:
            con.execute("DELETE FROM vec_meta WHERE key = 'dim'")

    @staticmethod
    def _ensure_vector_table(con: sqlite3.Connection, use_vec0: bool, dim: int) -> None:
        if use_vec0:
            con.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_embeddings USING vec0("
                f"partition TEXT PARTITION KEY, embedding float[{int(dim)}] distance_metric=cosine)"
            )
        else:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS vec_blobs (
                    rowid INTEGER PRIMARY KEY,
                    partition TEXT NOT NULL,
                    embedding BLOB NOT NULL
                )
                """
            )
            con.execute("CREATE INDEX IF NOT EXISTS idx_vec_blobs_partition ON vec_blobs (partition)")

    @staticmethod
    def _partition(scope: str, kind: str) -> str:
        return f"{scope}:{kind}"

    def sync_items(self, scope: str, kind: str, items: dict[str, str]) -> dict[str, int]:
        """Incrementally sync ``{item_id: text}`` for ``(scope, kind)``.

        Only new or changed texts are embedded; removed items are deleted.
        """
        if not self.is_available():
            return {"embedded": 0, "deleted": 0, "unchanged": 0}
        cleaned = {str(k): v.strip() for k, v in items.items() if v and v.strip()}
        wanted_hashes = {k: content_hash(v) for k, v in cleaned.items()}
        partition = self._partition(scope, kind)

        with self._lock:
            try:
                with self._connect() as (con, use_vec0):
                    meta = self._meta(con)
                    expected_backend = "vec0" if use_vec0 else "blob"
                    if meta.get("model") != self._model_name or meta.get("backend") != expected_backend:
                        self._reset_vectors(con, use_vec0, self._model_name, None)
                        meta = self._meta(con)

                    existing = {
                        r["item_id"]: (int(r["id"]), r["content_hash"])
                        for r in con.execute(
                            "SELECT id, item_id, content_hash FROM vec_items WHERE scope = ? AND kind = ?",
                            (scope, kind),
                        )
                    }
                    gone_rowids = [rid for iid, (rid, _) in existing.items() if iid not in wanted_hashes]
                    to_embed = [
                        (iid, cleaned[iid], h)
                        for iid, h in wanted_hashes.items()
                        if existing.get(iid, (None, None))[1] != h
                    ]

                    if not gone_rowids and not to_embed:
                        return {"embedded": 0, "deleted": 0, "unchanged": len(wanted_hashes)}

                    stored_dim = int(meta["dim"]) if meta.get("dim") else None
                    table_name = "vec_embeddings" if use_vec0 else "vec_blobs"

                    if gone_rowids:
                        if stored_dim is not None:
                            con.executemany(f"DELETE FROM {table_name} WHERE rowid = ?", [(r,) for r in gone_rowids])
                        con.executemany("DELETE FROM vec_items WHERE id = ?", [(r,) for r in gone_rowids])

                    if to_embed:
                        vectors = self._get_embedder().embed_documents([text for _, text, _ in to_embed])
                        if len(vectors) != len(to_embed):
                            raise RuntimeError("Embedder returned mismatched vector count.")
                        dim = len(vectors[0])
                        if stored_dim is None:
                            con.execute("INSERT OR REPLACE INTO vec_meta (key, value) VALUES ('dim', ?)", (str(dim),))
                            self._ensure_vector_table(con, use_vec0, dim)
                        elif stored_dim != dim:
                            self._reset_vectors(con, use_vec0, self._model_name, dim)
                            existing = {}
                            to_embed = [(iid, cleaned[iid], h) for iid, h in wanted_hashes.items()]
                            vectors = self._get_embedder().embed_documents([text for _, text, _ in to_embed])

                        stamp = now()
                        for (iid, _, h), vec in zip(to_embed, vectors):
                            blob = serialize_float32(vec)
                            prev = existing.get(iid)
                            if prev is not None:
                                rid = prev[0]
                                con.execute(
                                    "UPDATE vec_items SET content_hash = ?, updated_at = ? WHERE id = ?",
                                    (h, stamp, rid),
                                )
                                con.execute(f"DELETE FROM {table_name} WHERE rowid = ?", (rid,))
                            else:
                                cur = con.execute(
                                    "INSERT INTO vec_items (scope, kind, item_id, content_hash, updated_at) VALUES (?, ?, ?, ?, ?)",
                                    (scope, kind, iid, h, stamp),
                                )
                                rid = int(cur.lastrowid)
                            con.execute(
                                f"INSERT INTO {table_name} (rowid, partition, embedding) VALUES (?, ?, ?)",
                                (rid, partition, blob),
                            )
                    return {
                        "embedded": len(to_embed),
                        "deleted": len(gone_rowids),
                        "unchanged": max(0, len(wanted_hashes) - len(to_embed)),
                    }
            except Exception as exc:  # noqa: BLE001
                self._last_error = str(exc)
                logger.warning("Vector index sync failed for %s/%s; continuing with lexical search: %s", scope, kind, exc)
                return {"embedded": 0, "deleted": 0, "unchanged": 0}

    def _probe_vectors(self) -> list[list[list[float]]]:
        cached = getattr(self, "_probe_cache", None)
        if cached is not None and cached[0] == self._model_name:
            return cached[1]
        embedder = self._get_embedder()
        words = [w for row in NOISE_PROBES for w in row]
        batch = getattr(embedder, "embed_queries", None)
        flat = batch(words) if batch else [embedder.embed_query(w) for w in words]
        out, i = [], 0
        for row in NOISE_PROBES:
            out.append(flat[i: i + len(row)])
            i += len(row)
        self._probe_cache = (self._model_name, out)
        return out

    def noise_levels(self, scope: str, kinds: Sequence[str], key: Any = None) -> dict[tuple[str, str], float]:
        """Each indexed item's similarity to unrelated everyday words (see NOISE_PROBES).

        Returns ``{(kind, key(item_id)): level}``; items that ``key`` maps together (a document's
        full-text and name vectors) share the per-concept maximum. Empty when embeddings are off.
        """
        if not self.is_available() or not self.path.exists():
            return {}
        started = time.perf_counter()
        with self._lock:
            try:
                probes = self._probe_vectors()
                with self._connect() as (con, use_vec0):
                    meta = self._meta(con)
                    if meta.get("model") != self._model_name or not meta.get("dim"):
                        return {}
                    table = "vec_embeddings" if use_vec0 else "vec_blobs"
                    rows: list[tuple[tuple[str, str], list[float]]] = []
                    for kind in dict.fromkeys(kinds):
                        for r in con.execute(
                            f"SELECT i.item_id, e.embedding FROM vec_items i JOIN {table} e ON e.rowid = i.id "
                            "WHERE i.scope = ? AND i.kind = ?",
                            (scope, kind),
                        ):
                            iid = key(r["item_id"]) if key else r["item_id"]
                            rows.append(((kind, iid), deserialize_float32(r["embedding"])))
            except Exception as exc:  # noqa: BLE001
                self._last_error = str(exc)
                logger.warning("Noise levels failed for %s; check_answerable uses the floor alone: %s", scope, exc)
                return {}
        if not rows:
            return {}
        per_concept = _concept_maxima([v for _, v in rows], probes)
        best: dict[tuple[str, str], list[float]] = {}
        for (k, _), sims in zip(rows, per_concept):
            cur = best.get(k)
            best[k] = sims if cur is None else [max(a, b) for a, b in zip(cur, sims)]
        rank = min(NOISE_RANK, len(NOISE_PROBES)) - 1
        levels = {k: round(sorted(v, reverse=True)[rank], 4) for k, v in best.items()}
        top = sorted(levels.items(), key=lambda x: -x[1])[:3]
        logger.info(
            "noise levels for %s: %d items in %.0fms, highest %s",
            scope, len(levels), (time.perf_counter() - started) * 1000.0,
            ", ".join(f"{k[1]}={v}" for k, v in top),
        )
        return levels

    def similarities(self, query: str, texts: Sequence[str]) -> list[float] | None:
        """Cosine similarity of ``query`` to each of ``texts``; None when embeddings are off or fail."""
        if not self.is_available() or not (query or "").strip() or not texts:
            return None
        with self._lock:
            try:
                qvec = self._get_embedder().embed_query(query.strip())
                vecs = self._get_embedder().embed_documents(list(texts))
            except Exception as exc:  # noqa: BLE001
                self._last_error = str(exc)
                logger.warning("Embedding for similarity failed: %s", exc)
                return None
        return [round(cosine_similarity(qvec, v), 4) for v in vecs]

    def search(
        self,
        scope: str,
        kinds: Sequence[str],
        query: str,
        *,
        limit: int = 20,
        min_similarity: float | None = None,
    ) -> list[VectorHit]:
        """Return nearest indexed items for ``query`` across ``kinds`` in ``scope``.

        ``min_similarity`` defaults to the model's own floor (:attr:`min_similarity`).
        """
        if not self.is_available() or not (query or "").strip() or not kinds:
            return []
        if min_similarity is None:
            min_similarity = self.min_similarity
        if not self.path.exists():
            return []
        limit = max(1, int(limit))

        with self._lock:
            try:
                qvec = self._get_embedder().embed_query(query.strip())
                if not qvec:
                    return []
                qblob = serialize_float32(qvec)

                with self._connect() as (con, use_vec0):
                    meta = self._meta(con)
                    expected_backend = "vec0" if use_vec0 else "blob"
                    if (
                        meta.get("model") != self._model_name
                        or meta.get("backend") != expected_backend
                        or not meta.get("dim")
                        or int(meta["dim"]) != len(qvec)
                    ):
                        return []

                    raw_hits: list[tuple[int, float]] = []
                    for kind in dict.fromkeys(kinds):
                        part = self._partition(scope, kind)
                        if use_vec0:
                            rows = con.execute(
                                "SELECT rowid, distance FROM vec_embeddings WHERE partition = ? AND embedding MATCH ? AND k = ? ORDER BY distance",
                                (part, qblob, limit),
                            ).fetchall()
                            for r in rows:
                                # vec0 returns NULL cosine distance for zero vectors; skip them
                                # instead of failing the whole vector search.
                                if r["distance"] is None:
                                    continue
                                sim = max(-1.0, min(1.0, 1.0 - float(r["distance"])))
                                if sim >= min_similarity:
                                    raw_hits.append((int(r["rowid"]), sim))
                        else:
                            rows = con.execute(
                                "SELECT rowid, embedding FROM vec_blobs WHERE partition = ?",
                                (part,),
                            ).fetchall()
                            scored: list[tuple[int, float]] = []
                            for r in rows:
                                sim = cosine_similarity(qvec, deserialize_float32(r["embedding"]))
                                if sim >= min_similarity:
                                    scored.append((int(r["rowid"]), sim))
                            scored.sort(key=lambda x: -x[1])
                            raw_hits.extend(scored[:limit])

                    if not raw_hits:
                        return []

                    rowids = list({rid for rid, _ in raw_hits})
                    marks = ",".join("?" for _ in rowids)
                    meta_rows = {
                        int(r["id"]): (r["kind"], r["item_id"])
                        for r in con.execute(f"SELECT id, kind, item_id FROM vec_items WHERE id IN ({marks})", rowids)
                    }
                    hits: list[VectorHit] = []
                    for rid, sim in raw_hits:
                        info = meta_rows.get(rid)
                        if info is not None:
                            hits.append(VectorHit(kind=info[0], item_id=info[1], similarity=round(sim, 4)))
                    hits.sort(key=lambda h: (-h.similarity, h.kind, h.item_id))
                    return hits[:limit]
            except Exception as exc:  # noqa: BLE001
                self._last_error = str(exc)
                logger.warning("Vector search failed for %s; falling back to lexical search: %s", scope, exc)
                return []
