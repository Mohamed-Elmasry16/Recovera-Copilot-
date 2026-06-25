"""
retrieval_engine.py - Hybrid RAG Retrieval Engine
==================================================
Deterministic code responsible for:
  1. Embedding user query via Jina AI
  2. For review analysis: hybrid vector+BM25 search on review_embeddings (FIXED)
  3. For other intents: pgvector cosine similarity + BM25 hybrid search
  4. Source diversity (disabled for review analysis)
  5. Query caching with 24-hour TTL

FIXES:
  - [Bug #3] ORDER BY was rating DESC (positive reviews first).
             Now uses semantic vector similarity as primary sort.
  - [Bug #4] Vector search was computed but never used for review_analysis.
             Now uses rag.review_embeddings for semantic similarity.
  - Added sentiment-aware fallback when no vector is available.
  - Added BM25 full-text layer on review content for hybrid review search.
"""

import asyncio
import time
import hashlib
import json
import logging
from typing import Optional
from dataclasses import dataclass, field

import asyncpg
import httpx

from app.core.config import settings

log = logging.getLogger(__name__)

# ================================================================
# CONFIGURATION
# ================================================================

DEFAULT_VECTOR_WEIGHT = 0.6
DEFAULT_BM25_WEIGHT   = 0.4
DEFAULT_TOP_K         = 30
REVIEW_FETCH_LIMIT    = 500     # Max reviews for analysis
MAX_DOCS_PER_SOURCE   = 2
CACHE_TTL_SECONDS     = 86400

# Intent → embedding tables mapping (non-review intents)
INTENT_TABLE_MAP = {
    "simple_lookup":        ["rag.schema_embeddings"],
    "aggregation":          ["rag.schema_embeddings", "rag.metrics_embeddings"],
    "trend_analysis":       ["rag.metrics_embeddings", "rag.schema_embeddings"],
    "anomaly_investigation":["rag.business_embeddings", "rag.schema_embeddings", "rag.metrics_embeddings"],
    "schema_discovery":     ["rag.schema_embeddings"],
    "kpi_definition":       ["rag.metrics_embeddings", "rag.schema_embeddings"],
    "sql_template_lookup":  ["rag.schema_embeddings", "rag.business_embeddings"],
    "leakage_root_cause_analysis": ["rag.business_embeddings", "rag.review_embeddings", "rag.schema_embeddings"],
}

# Intent → source type filter (non-review intents)
INTENT_SOURCE_FILTER = {
    "simple_lookup":        ["schema_doc", "sql_template", "kpi_glossary"],
    "aggregation":          ["kpi_glossary", "schema_doc", "sql_template", "leakage_scenario"],
    "trend_analysis":       ["kpi_glossary", "sql_template", "schema_doc"],
    "anomaly_investigation":["leakage_scenario", "business_rule", "sql_template", "schema_doc"],
    "schema_discovery":     ["schema_doc", "join_graph"],
    "kpi_definition":       ["kpi_glossary", "schema_doc"],
    "sql_template_lookup":  ["sql_template", "schema_doc", "leakage_scenario"],
    "leakage_root_cause_analysis": ["leakage_scenario", "business_rule", "review"],
}

# Keywords that hint at negative sentiment in the query
_NEGATIVE_KEYWORDS = (
    "negative", "سلبي", "bad", "شكوى", "يشتكي", "complaint", "complaints",
    "problem", "مشكلة", "مشاكل", "unhappy", "غير راضي", "poor",
    "غير مطابق", "mismatch", "not as described", "مختلف", "different",
    "return", "إرجاع", "refund", "استرداد",
)

_POSITIVE_KEYWORDS = (
    "positive", "إيجابي", "good", "يثنوا", "happy", "راضي",
    "great", "excellent", "satisfied", "satisfied customer",
)


# ================================================================
# RAG CONTEXT RESULT
# ================================================================

@dataclass
class RAGContext:
    schema_docs:     list[dict] = field(default_factory=list)
    business_rules:  list[dict] = field(default_factory=list)
    sql_templates:   list[dict] = field(default_factory=list)
    kpi_definitions: list[dict] = field(default_factory=list)
    review_chunks:   list[dict] = field(default_factory=list)
    anti_patterns:   list[dict] = field(default_factory=list)
    retrieved_count: int = 0
    elapsed_ms:      int = 0
    from_cache:      bool = False
    sources_used:    list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "schema_docs":     self.schema_docs,
            "business_rules":  self.business_rules,
            "sql_templates":   self.sql_templates,
            "kpi_definitions": self.kpi_definitions,
            "review_chunks":   self.review_chunks,
            "anti_patterns":   self.anti_patterns,
            "retrieved_count": self.retrieved_count,
            "elapsed_ms":      self.elapsed_ms,
            "from_cache":      self.from_cache,
            "sources_used":    self.sources_used,
        }


# ================================================================
# JINA EMBEDDING CLIENT
# ================================================================

class EmbeddingClient:
    def __init__(self):
        self.api_key  = settings.JINA_API_KEY
        self.model    = settings.JINA_MODEL
        self.dim      = settings.JINA_EMBED_DIM
        self.base_url = settings.JINA_BASE_URL
        self._cache: dict[str, tuple[list[float], float]] = {}
        self._cache_ttl = settings.EMBED_CACHE_TTL

    async def embed(self, text: str) -> list[float]:
        if not self.api_key:
            raise RuntimeError("JINA_API_KEY not configured")
        query_hash = hashlib.md5(text.lower().strip().encode()).hexdigest()
        if query_hash in self._cache:
            vector, ts = self._cache[query_hash]
            if (time.time() - ts) < self._cache_ttl:
                return vector
        payload = {
            "model": self.model,
            "task": "retrieval.query",
            "normalized": True,
            "input": [text],
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.post(self.base_url, headers=headers, json=payload)
                    if resp.status_code == 429:
                        await asyncio.sleep(2 ** attempt)
                        continue
                    if resp.status_code == 401:
                        raise RuntimeError("Jina AI API key invalid")
                    resp.raise_for_status()
                    data = resp.json()
                    embedding = data["data"][0]["embedding"]
                    if len(embedding) != self.dim:
                        raise RuntimeError(f"Dimension mismatch: got {len(embedding)}, expected {self.dim}")
                    self._cache[query_hash] = (embedding, time.time())
                    return embedding
            except Exception as e:
                if attempt == 2:
                    raise
                await asyncio.sleep(2 ** attempt)
        raise RuntimeError("Embedding failed after 3 attempts")

    def get_dim(self) -> int:
        return self.dim


_embed_client = EmbeddingClient()


# ================================================================
# RETRIEVAL ENGINE
# ================================================================

class RetrievalEngine:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def retrieve(
        self,
        query_text: str,
        intent: str = "simple_lookup",
        top_k: int = DEFAULT_TOP_K,
        vector_weight: float = DEFAULT_VECTOR_WEIGHT,
        bm25_weight: float = DEFAULT_BM25_WEIGHT,
        use_cache: bool = True,
    ) -> RAGContext:
        start = time.time()
        context = RAGContext()

        if intent in ("review_analysis", "sentiment_analysis"):
            top_k = REVIEW_FETCH_LIMIT

        query_hash = self._hash_query(query_text, intent)
        if use_cache:
            cached = await self._check_cache(query_hash)
            if cached:
                context = self._build_context(cached)
                context.from_cache = True
                context.elapsed_ms = int((time.time() - start) * 1000)
                return context

        # Embed query
        query_vector_str: Optional[str] = None
        try:
            query_vector = await _embed_client.embed(query_text)
            query_vector_str = f"[{','.join(map(str, query_vector))}]"
        except Exception as e:
            log.warning(f"[retrieval] Embedding failed: {e}")

        docs = await self._hybrid_search(
            query_text, query_vector_str, intent, top_k, vector_weight, bm25_weight
        )
        docs = self._apply_diversity(docs, MAX_DOCS_PER_SOURCE, top_k, intent)

        if use_cache and docs:
            await self._save_cache(query_hash, docs)

        context = self._build_context(docs)
        context.elapsed_ms = int((time.time() - start) * 1000)
        log.info(f"[retrieval] Retrieved {context.retrieved_count} docs in {context.elapsed_ms}ms (intent={intent})")
        return context

    # ----------------------------------------------------------------
    # HYBRID SEARCH
    # ----------------------------------------------------------------

    async def _hybrid_search(
        self,
        query_text: str,
        query_vector_str: Optional[str],
        intent: str,
        top_k: int,
        vector_weight: float,
        bm25_weight: float,
    ) -> list[dict]:

        # ============================================================
        # REVIEW ANALYSIS — semantic vector search on review_embeddings
        # FIX: was a plain SQL fetch sorted by rating DESC (no semantics)
        # ============================================================
        if intent in ("review_analysis", "sentiment_analysis"):
            return await self._review_semantic_search(
                query_text, query_vector_str, intent, top_k
            )

        # ============================================================
        # DEFAULT HYBRID SEARCH (non-review intents)
        # ============================================================
        tables       = INTENT_TABLE_MAP.get(intent, ["rag.schema_embeddings", "rag.business_embeddings"])
        source_types = INTENT_SOURCE_FILTER.get(intent, [])
        all_results  = []

        async with self.pool.acquire() as conn:
            for table_name in tables:
                try:
                    if query_vector_str:
                        rows = await conn.fetch(
                            f"""
                            WITH bm25_results AS (
                                SELECT doc_id,
                                       ts_rank_cd(content_tsv, plainto_tsquery('english', $1)) AS bm25_score
                                FROM rag.documents
                                WHERE content_tsv @@ plainto_tsquery('english', $1)
                                  AND is_active = true
                                  AND (array_length($4::text[], 1) IS NULL OR source_type::text = ANY($4::text[]))
                            ),
                            vector_results AS (
                                SELECT e.doc_id,
                                       1 - (e.embedding <=> $2::vector) AS vector_score
                                FROM {table_name} e
                                JOIN rag.documents d ON e.doc_id = d.doc_id
                                WHERE d.is_active = true
                                  AND (array_length($4::text[], 1) IS NULL OR d.source_type::text = ANY($4::text[]))
                                ORDER BY e.embedding <=> $2::vector
                                LIMIT $3 * 2
                            )
                            SELECT
                                d.doc_id, d.source_type, d.title, d.content, d.priority,
                                COALESCE(b.bm25_score, 0) * $5 + COALESCE(v.vector_score, 0) * $6 AS hybrid_score
                            FROM bm25_results b
                            FULL OUTER JOIN vector_results v USING(doc_id)
                            JOIN rag.documents d ON COALESCE(b.doc_id, v.doc_id) = d.doc_id
                            WHERE d.is_active = true
                            ORDER BY hybrid_score DESC, d.priority DESC
                            LIMIT $3
                            """,
                            query_text, query_vector_str, top_k,
                            source_types, bm25_weight, vector_weight,
                        )
                    else:
                        rows = await conn.fetch(
                            f"""
                            SELECT
                                d.doc_id, d.source_type, d.title, d.content, d.priority,
                                ts_rank_cd(d.content_tsv, plainto_tsquery('english', $1)) AS hybrid_score
                            FROM rag.documents d
                            WHERE d.content_tsv @@ plainto_tsquery('english', $1)
                              AND d.is_active = true
                              AND (array_length($2::text[], 1) IS NULL OR d.source_type::text = ANY($2::text[]))
                            ORDER BY hybrid_score DESC, d.priority DESC
                            LIMIT $3
                            """,
                            query_text, source_types, top_k,
                        )
                    for row in rows:
                        all_results.append(dict(row))
                except Exception as e:
                    log.warning(f"[retrieval] Error in {table_name}: {e}")
                    continue

        # Deduplicate by doc_id, keep highest score
        seen: set[int] = set()
        unique: list[dict] = []
        for r in sorted(all_results, key=lambda x: x["hybrid_score"], reverse=True):
            if r["doc_id"] not in seen:
                seen.add(r["doc_id"])
                unique.append(r)
        return unique

    # ----------------------------------------------------------------
    # REVIEW SEMANTIC SEARCH (replaces broken plain-SQL fetch)
    # ----------------------------------------------------------------

    async def _review_semantic_search(
        self,
        query_text: str,
        query_vector_str: Optional[str],
        intent: str,
        top_k: int,
    ) -> list[dict]:
        """
        Hybrid semantic + BM25 search specifically for review documents.

        FIX Bug #3: was ORDER BY r.rating DESC → positive reviews first.
                    Now orders by semantic relevance (vector cosine similarity).
        FIX Bug #4: vector embedding was computed but ignored.
                    Now used as primary ranking signal via rag.review_embeddings.

        Strategy:
          1. If vector available: cosine similarity on rag.review_embeddings (primary)
             combined with BM25 on rag.documents.content_tsv (secondary).
          2. Fallback (no vector): BM25 + sentiment filter derived from query keywords.
        """
        lower_q = query_text.lower()

        # Detect sentiment hint from query keywords
        if any(k in lower_q for k in _POSITIVE_KEYWORDS):
            sentiment_hint = "positive"
        elif any(k in lower_q for k in _NEGATIVE_KEYWORDS):
            sentiment_hint = "negative"
        else:
            sentiment_hint = None

        log.info(
            f"[retrieval] Review semantic search: sentiment_hint={sentiment_hint!r}, "
            f"vector={'yes' if query_vector_str else 'no'}"
        )

        async with self.pool.acquire() as conn:
            # ----------------------------------------------------------
            # PATH A: Vector similarity available → hybrid semantic+BM25
            # ----------------------------------------------------------
            if query_vector_str:
                if sentiment_hint:
                    # Sentiment-filtered semantic search
                    rows = await conn.fetch(
                        """
                        WITH vector_scores AS (
                            SELECT re.doc_id,
                                   1 - (re.embedding <=> $1::vector) AS vector_score
                            FROM rag.review_embeddings re
                            ORDER BY re.embedding <=> $1::vector
                            LIMIT $2 * 3
                        ),
                        bm25_scores AS (
                            SELECT d.doc_id,
                                   ts_rank_cd(d.content_tsv, plainto_tsquery('simple', $3)) AS bm25_score
                            FROM rag.documents d
                            WHERE d.content_tsv @@ plainto_tsquery('simple', $3)
                              AND d.source_type = 'review'
                              AND d.is_active = true
                        )
                        SELECT
                            d.doc_id, d.source_type, d.title, d.content, d.priority,
                            COALESCE(v.vector_score, 0) * 0.7
                                + COALESCE(b.bm25_score, 0) * 0.3 AS hybrid_score
                        FROM vector_scores v
                        LEFT JOIN bm25_scores b USING(doc_id)
                        JOIN rag.documents d ON v.doc_id = d.doc_id
                        JOIN ecommerce.reviews r ON d.source_id = r.order_id::text
                        WHERE d.source_type = 'review'
                          AND d.is_active = true
                          AND d.content IS NOT NULL
                          AND length(d.content) > 15
                          AND r.sentiment = $4
                        ORDER BY hybrid_score DESC
                        LIMIT $2
                        """,
                        query_vector_str, top_k, query_text, sentiment_hint,
                    )
                else:
                    # All-sentiment semantic search
                    rows = await conn.fetch(
                        """
                        WITH vector_scores AS (
                            SELECT re.doc_id,
                                   1 - (re.embedding <=> $1::vector) AS vector_score
                            FROM rag.review_embeddings re
                            ORDER BY re.embedding <=> $1::vector
                            LIMIT $2 * 3
                        ),
                        bm25_scores AS (
                            SELECT d.doc_id,
                                   ts_rank_cd(d.content_tsv, plainto_tsquery('simple', $3)) AS bm25_score
                            FROM rag.documents d
                            WHERE d.content_tsv @@ plainto_tsquery('simple', $3)
                              AND d.source_type = 'review'
                              AND d.is_active = true
                        )
                        SELECT
                            d.doc_id, d.source_type, d.title, d.content, d.priority,
                            COALESCE(v.vector_score, 0) * 0.7
                                + COALESCE(b.bm25_score, 0) * 0.3 AS hybrid_score
                        FROM vector_scores v
                        LEFT JOIN bm25_scores b USING(doc_id)
                        JOIN rag.documents d ON v.doc_id = d.doc_id
                        WHERE d.source_type = 'review'
                          AND d.is_active = true
                          AND d.content IS NOT NULL
                          AND length(d.content) > 15
                        ORDER BY hybrid_score DESC
                        LIMIT $2
                        """,
                        query_vector_str, top_k, query_text,
                    )

            # ----------------------------------------------------------
            # PATH B: No vector → BM25 + sentiment-priority fallback
            # FIX Bug #3: fallback now prioritises low ratings for negative queries
            # ----------------------------------------------------------
            else:
                log.warning("[retrieval] No query vector — using BM25+sentiment fallback for reviews")
                if sentiment_hint:
                    rows = await conn.fetch(
                        """
                        SELECT
                            d.doc_id, d.source_type, d.title, d.content, d.priority,
                            CASE r.sentiment
                                WHEN $2 THEN 1.0
                                WHEN 'mixed' THEN 0.5
                                ELSE 0.2
                            END AS hybrid_score
                        FROM rag.documents d
                        JOIN ecommerce.reviews r ON d.source_id = r.order_id::text
                        WHERE d.source_type = 'review'
                          AND d.is_active = true
                          AND d.content IS NOT NULL
                          AND length(d.content) > 15
                          AND r.sentiment = $2
                        ORDER BY hybrid_score DESC,
                                 CASE $2 WHEN 'negative' THEN r.rating END ASC,
                                 CASE $2 WHEN 'positive' THEN r.rating END DESC,
                                 length(d.content) DESC
                        LIMIT $1
                        """,
                        top_k, sentiment_hint,
                    )
                else:
                    # Mixed-sentiment, balanced sample
                    rows = await conn.fetch(
                        """
                        SELECT
                            d.doc_id, d.source_type, d.title, d.content, d.priority,
                            CASE r.sentiment
                                WHEN 'negative' THEN 0.9
                                WHEN 'mixed'    THEN 0.7
                                WHEN 'neutral'  THEN 0.5
                                ELSE 0.3
                            END AS hybrid_score
                        FROM rag.documents d
                        JOIN ecommerce.reviews r ON d.source_id = r.order_id::text
                        WHERE d.source_type = 'review'
                          AND d.is_active = true
                          AND d.content IS NOT NULL
                          AND length(d.content) > 15
                        ORDER BY hybrid_score DESC, length(d.content) DESC
                        LIMIT $1
                        """,
                        top_k,
                    )

        results = [dict(row) for row in rows]
        log.info(
            f"[retrieval] Review semantic search returned {len(results)} docs "
            f"(sentiment_hint={sentiment_hint!r})"
        )
        return results

    # ----------------------------------------------------------------
    # DIVERSITY FILTER
    # ----------------------------------------------------------------

    def _apply_diversity(
        self,
        docs: list[dict],
        max_per_source: int,
        final_top_k: int,
        intent: str,
    ) -> list[dict]:
        # No diversity capping for review intents — we want as many as possible
        if intent in ("review_analysis", "sentiment_analysis"):
            return docs[:final_top_k]
        counts: dict[str, int] = {}
        diverse: list[dict] = []
        for doc in docs:
            st = doc.get("source_type", "unknown")
            if counts.get(st, 0) < max_per_source:
                diverse.append(doc)
                counts[st] = counts.get(st, 0) + 1
            if len(diverse) >= final_top_k:
                break
        return diverse

    # ----------------------------------------------------------------
    # CONTEXT BUILDER
    # ----------------------------------------------------------------

    def _build_context(self, docs: list[dict]) -> RAGContext:
        ctx = RAGContext(retrieved_count=len(docs))
        for doc in docs:
            st = doc.get("source_type", "")
            entry = {
                "doc_id":  doc.get("doc_id"),
                "title":   doc.get("title", ""),
                "content": doc.get("content", ""),
                "score":   float(doc.get("hybrid_score", 0)),
            }
            if st == "review":
                ctx.review_chunks.append(entry)
            elif st == "schema_doc":
                ctx.schema_docs.append(entry)
            elif st in ("leakage_scenario", "business_rule", "leakage_reason"):
                ctx.business_rules.append(entry)
            elif st == "sql_template":
                ctx.sql_templates.append(entry)
            elif st in ("kpi_glossary", "metric"):
                ctx.kpi_definitions.append(entry)
            elif st == "anti_pattern":
                ctx.anti_patterns.append(entry)
            else:
                ctx.schema_docs.append(entry)
        ctx.sources_used = list({d.get("source_type", "") for d in docs})
        return ctx

    # ----------------------------------------------------------------
    # CACHE
    # ----------------------------------------------------------------

    async def _check_cache(self, query_hash: str) -> Optional[list[dict]]:
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT result_json FROM rag.retrieval_cache
                    WHERE query_hash = $1
                      AND created_at > NOW() - INTERVAL '24 hours'
                    LIMIT 1
                    """,
                    query_hash,
                )
                if row:
                    await conn.execute(
                        "UPDATE rag.retrieval_cache SET hit_count = hit_count + 1 WHERE query_hash = $1",
                        query_hash,
                    )
                    return json.loads(row["result_json"])
        except Exception as e:
            log.warning(f"[retrieval] Cache check error: {e}")
        return None

    async def _save_cache(self, query_hash: str, docs: list[dict]):
        try:
            result_json = json.dumps(docs, ensure_ascii=False, default=str)
            async with self.pool.acquire() as conn:
                updated = await conn.execute(
                    """
                    UPDATE rag.retrieval_cache
                    SET result_json = $2, created_at = NOW(), hit_count = hit_count + 1
                    WHERE query_hash = $1
                    """,
                    query_hash, result_json,
                )
                if "UPDATE 0" in str(updated):
                    await conn.execute(
                        """
                        INSERT INTO rag.retrieval_cache (cache_key, query_hash, result_json, hit_count)
                        VALUES ($1, $1, $2, 1)
                        """,
                        query_hash, result_json,
                    )
        except Exception as e:
            log.warning(f"[retrieval] Cache save error: {e}")

    def _hash_query(self, query_text: str, intent: str) -> str:
        return hashlib.md5(f"{query_text.lower().strip()}|{intent}".encode()).hexdigest()


# ================================================================
# PUBLIC API
# ================================================================

async def retrieve_context(
    pool: asyncpg.Pool,
    query_text: str,
    intent: str = "simple_lookup",
    top_k: int = DEFAULT_TOP_K,
) -> RAGContext:
    """Public API for context retrieval."""
    engine = RetrievalEngine(pool)
    return await engine.retrieve(query_text, intent, top_k)