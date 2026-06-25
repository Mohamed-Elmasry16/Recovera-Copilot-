"""
postgres.py - PostgreSQL Database Manager
==========================================
Manages:
  - Async connection pool
  - Schema context loading
  - SQL guard rules loading
  - Conversation history
  - Query logging
  - Retrieval logging
  - Health checks
"""

import os
import time
import json
import logging
from typing import Optional

import asyncpg

from app.core.config import settings

log = logging.getLogger(__name__)

# ================================================================
# POOL MANAGEMENT
# ================================================================

_pool: Optional[asyncpg.Pool] = None
_schema_cache: dict = {}
_schema_cache_ts: float = 0.0
_sql_guard_cache: list[dict] = []
_sql_guard_ts: float = 0.0

async def init_pool() -> asyncpg.Pool:
    """Initialize the asyncpg connection pool."""
    global _pool
    timeout_sec = settings.STATEMENT_TIMEOUT_MS / 1000

    _pool = await asyncpg.create_pool(
        host=settings.DB_HOST,
        port=settings.DB_PORT,
        database=settings.DB_NAME,
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
        min_size=settings.DB_POOL_MIN,
        max_size=settings.DB_POOL_MAX,
        command_timeout=timeout_sec,
        # Required for Supabase Session Pooler (PgBouncer):
        # PgBouncer does not support PostgreSQL prepared statements,
        # so asyncpg's built-in statement cache must be disabled.
        statement_cache_size=0,
        # Supabase pooler requires SSL.
        ssl="require",
        server_settings={"jit": "off"},
    )
    log.info(f"DB pool ready (min={settings.DB_POOL_MIN}, max={settings.DB_POOL_MAX})")
    return _pool


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialized. Call init_pool() first.")
    return _pool


# ================================================================
# SCHEMA LOADING
# ================================================================

async def load_schema_context() -> dict:
    """Load schema metadata with TTL cache."""
    global _schema_cache, _schema_cache_ts
    now = time.time()
    if _schema_cache and (now - _schema_cache_ts) < settings.SCHEMA_CACHE_TTL:
        return _schema_cache

    pool = get_pool()
    async with pool.acquire() as conn:
        # Schema metadata
        tables = await conn.fetch(
            "SELECT * FROM ml_output.schema_metadata ORDER BY table_schema, table_name"
        )

        # ENUM values
        enums_rows = await conn.fetch("""
            SELECT enum_name,
                   json_agg(json_build_object('value', enum_value, 'desc', description)
                            ORDER BY enum_value) AS values
            FROM ml_output.chatbot_enums
            GROUP BY enum_name
        """)
        enums = {r["enum_name"]: r["values"] for r in enums_rows}

        # Materialized views
        mv_rows = await conn.fetch("""
            SELECT m.matviewname AS table_name, sm.description
            FROM pg_matviews m
            LEFT JOIN ml_output.schema_metadata sm ON sm.table_name = m.matviewname
            WHERE m.schemaname = 'ml_output'
        """)
        mv_hints = {r["table_name"]: r["description"] for r in mv_rows}

        _schema_cache = {
            "tables": [dict(t) for t in tables],
            "enums": enums,
            "mv_hints": mv_hints,
            "loaded_at": now,
        }
        _schema_cache_ts = now
        log.info(f"Schema loaded: {len(tables)} tables, {len(enums)} enums")
        return _schema_cache


def invalidate_schema_cache():
    global _schema_cache, _schema_cache_ts
    _schema_cache = {}
    _schema_cache_ts = 0.0


# ================================================================
# SQL GUARD
# ================================================================

async def load_sql_guard() -> list[dict]:
    """Load SQL guard rules with cache."""
    global _sql_guard_cache, _sql_guard_ts
    now = time.time()
    if _sql_guard_cache and (now - _sql_guard_ts) < 3600:
        return _sql_guard_cache

    pool = get_pool()
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT guard_type, pattern, message, guard_mode FROM rag.sql_guard ORDER BY guard_id"
            )
            _sql_guard_cache = [dict(r) for r in rows]
            _sql_guard_ts = now
            log.info(f"SQL Guard loaded: {len(_sql_guard_cache)} rules")
            return _sql_guard_cache
    except Exception as e:
        log.warning(f"SQL Guard not loaded: {e}")
        return []


# ================================================================
# CONVERSATION HISTORY
# ================================================================

async def load_history(session_id: str, max_turns: int = 10) -> list[dict]:
    """Load conversation history for a session."""
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT role, content
            FROM ml_output.chatbot_conversations
            WHERE session_id = $1 AND role IN ('user', 'assistant')
            ORDER BY turn_index DESC
            LIMIT $2
        """, session_id, max_turns * 2)
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


async def save_turn(session_id: str, turn_index: int, role: str, content: str):
    """Save a conversation turn.

    This intentionally avoids ``ON CONFLICT`` because some deployed databases do
    not have a unique constraint on (session_id, turn_index, role).  The previous
    implementation caused: "there is no unique or exclusion constraint matching
    the ON CONFLICT specification".
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        status = await conn.execute("""
            UPDATE ml_output.chatbot_conversations
            SET content = $4, created_at = NOW()
            WHERE session_id = $1 AND turn_index = $2 AND role = $3
        """, session_id, turn_index, role, content)

        # asyncpg returns strings like "UPDATE 0" or "UPDATE 1".
        try:
            updated = int(status.split()[-1])
        except Exception:
            updated = 0

        if updated == 0:
            await conn.execute("""
                INSERT INTO ml_output.chatbot_conversations
                    (session_id, turn_index, role, content)
                VALUES ($1, $2, $3, $4)
            """, session_id, turn_index, role, content)


async def get_next_turn_index(session_id: str) -> int:
    """Get next turn index for a session."""
    pool = get_pool()
    async with pool.acquire() as conn:
        val = await conn.fetchval("""
            SELECT COALESCE(MAX(turn_index), -1) + 1
            FROM ml_output.chatbot_conversations
            WHERE session_id = $1
        """, session_id)
        return val or 0


# ================================================================
# QUERY LOGGING - SCHEMA COMPLIANT
# ================================================================

async def log_query(
    session_id: str,
    turn_id: int,
    user_question: str,
    generated_sql: Optional[str],
    tables_used: list[str],
    execution_ms: int,
    row_count: int,
    error_message: Optional[str],
    input_tokens: int,
    output_tokens: int,
    model: str,
    intent: str = "sql_generation",
):
    """Log query execution with full metadata.

    Schema: ml_output.chatbot_query_log
    Columns: id, session_id, turn_id, user_question, detected_intent, 
             generated_sql, tables_used, execution_ms, row_count, 
             was_helpful, error_message, llm_model, input_tokens, 
             output_tokens, cost_usd, created_at
    """
    pool = get_pool()
    # cost_usd formula per schema docs: gpt-4o-mini pricing
    cost_usd = (input_tokens * 0.000003) + (output_tokens * 0.000015)
    try:
        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO ml_output.chatbot_query_log
                    (session_id, turn_id, user_question, generated_sql, tables_used,
                     execution_ms, row_count, error_message,
                     input_tokens, output_tokens, cost_usd, llm_model, detected_intent)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
            """, session_id, turn_id, user_question, generated_sql, tables_used,
                  execution_ms, row_count, error_message,
                  input_tokens, output_tokens, cost_usd, model, intent)
    except Exception as e:
        log.error(f"[log_query] {e}")


async def log_retrieval(
    query_text: str,
    intent: str,
    generated_sql: Optional[str],
    sql_valid: bool,
    sql_executed: bool,
    exec_error: Optional[str],
    latency_ms: int = 0,
):
    """Log RAG retrieval event.

    Schema: rag.retrieval_log
    Columns: log_id, query_text, query_intent, generated_sql, sql_valid,
             sql_executed, execution_error, latency_ms, created_at
    """
    pool = get_pool()
    valid_intents = {
        "trend_analysis", "aggregation", "simple_lookup",
        "anomaly_investigation", "sql_generation",
        "sentiment_analysis", "schema_discovery", "kpi_definition",
    }
    safe_intent = intent if intent in valid_intents else "sql_generation"

    try:
        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO rag.retrieval_log
                    (query_text, query_intent, generated_sql,
                     sql_valid, sql_executed, execution_error, latency_ms)
                VALUES ($1, $2::rag.query_intent_t, $3, $4, $5, $6, $7)
            """, query_text, safe_intent, generated_sql,
                  sql_valid, sql_executed, exec_error, latency_ms)
    except Exception as e:
        log.warning(f"[log_retrieval] {e}")


# ================================================================
# HEALTH CHECK
# ================================================================

async def health_check() -> dict:
    """Full health check of database and subsystems."""
    pool = get_pool()
    try:
        async with pool.acquire() as conn:
            version = await conn.fetchval("SELECT version()")
            doc_count = await conn.fetchval("SELECT COUNT(*) FROM rag.documents WHERE is_active = true")
            schema_emb = await conn.fetchval("SELECT COUNT(*) FROM rag.schema_embeddings")
            biz_emb = await conn.fetchval("SELECT COUNT(*) FROM rag.business_embeddings")
            met_emb = await conn.fetchval("SELECT COUNT(*) FROM rag.metrics_embeddings")
            rev_emb = await conn.fetchval("SELECT COUNT(*) FROM rag.review_embeddings")
            retrieval_cache_entries = await conn.fetchval("SELECT COUNT(*) FROM rag.retrieval_cache")
            retrieval_cache_hits = await conn.fetchval("SELECT COALESCE(SUM(hit_count), 0) FROM rag.retrieval_cache")
            try:
                query_cache_entries = await conn.fetchval("SELECT COUNT(*) FROM rag.query_result_cache")
                query_cache_hits = await conn.fetchval("SELECT COALESCE(SUM(hit_count), 0) FROM rag.query_result_cache")
            except Exception:
                query_cache_entries = 0
                query_cache_hits = 0

            return {
                "connected": True,
                "postgres_version": version.split()[1] if version else "unknown",
                "active_documents": doc_count,
                "embeddings": {
                    "schema": schema_emb,
                    "business": biz_emb,
                    "metrics": met_emb,
                    "review": rev_emb,
                },
                "retrieval_cache": {
                    "entries": retrieval_cache_entries,
                    "total_hits": retrieval_cache_hits,
                },
                "query_result_cache": {
                    "entries": query_cache_entries,
                    "total_hits": query_cache_hits,
                },
            }
    except Exception as e:
        return {"connected": False, "error": str(e)}