"""
query_executor.py - safe read-only PostgreSQL executor v2
=========================================================

Key improvements:
- accepts SELECT and WITH/CTE queries;
- avoids substring false positives such as created_at / updated_at;
- validates SQL with AST safety and PostgreSQL EXPLAIN before execution;
- executes inside a READ ONLY transaction with local timeouts;
- uses a dedicated rag.query_result_cache table instead of overloading
  rag.retrieval_cache.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Optional

import asyncpg

from app.agents.sql_compiler import explain_validate
from app.agents.sql_safety import ensure_outer_limit, normalize_sql, validate_table_access
from app.core.config import settings
from app.database.postgres import load_sql_guard

log = logging.getLogger(__name__)


class QueryExecutor:
    """Safe SQL execution with caching and observability."""

    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool
        self.max_rows = settings.MAX_QUERY_ROWS
        self.statement_timeout = settings.STATEMENT_TIMEOUT_MS
        self.compile_timeout = min(3000, max(1000, settings.STATEMENT_TIMEOUT_MS // 5))

    async def execute(self, sql: str, use_cache: bool = True) -> tuple[list[dict], int, Optional[str], bool]:
        clean = normalize_sql(sql)
        log.info("[query_executor] start chars=%s cache=%s", len(clean or ""), use_cache)

        safety = validate_table_access(clean)
        if safety.fatal:
            err = "; ".join(safety.errors)
            log.warning("[query_executor] safety_blocked error=%s", err)
            return [], 0, err, False
        clean = safety.normalized_sql or clean
        log.info("[query_executor] safety_ok warnings=%s", len(safety.warnings))

        guard_error = await self._apply_sql_guard(clean)
        if guard_error:
            log.warning("[query_executor] sql_guard_blocked error=%s", guard_error)
            return [], 0, guard_error, False
        log.info("[query_executor] sql_guard_ok")

        if use_cache:
            cached = await self._check_cache(clean)
            if cached is not None:
                log.info("[query_executor] cache_hit rows=%s truncated=%s", len(cached[0]), cached[1])
                return cached[0], 0, None, cached[1]
            log.info("[query_executor] cache_miss")

        async with self.pool.acquire() as conn:
            compile_result = await explain_validate(conn, clean, timeout_ms=self.compile_timeout)
            if not compile_result.ok:
                log.warning("[query_executor] compile_failed error=%s", compile_result.error)
                return [], 0, f"SQL compile failed: {compile_result.error}", False

            start = time.time()
            try:
                await conn.execute("BEGIN READ ONLY")
                await conn.execute("SET LOCAL search_path = ml_output, ecommerce, marketing, rag, public")
                await conn.execute(f"SET LOCAL statement_timeout = '{int(self.statement_timeout)}ms'")
                await conn.execute("SET LOCAL lock_timeout = '1000ms'")
                await conn.execute("SET LOCAL idle_in_transaction_session_timeout = '30000ms'")

                limited_sql = ensure_outer_limit(clean, self.max_rows)
                rows = await conn.fetch(limited_sql)
                await conn.execute("COMMIT")

                elapsed = int((time.time() - start) * 1000)
                was_truncated = len(rows) > self.max_rows
                result_rows = [dict(r) for r in rows[: self.max_rows]]

                if use_cache:
                    await self._save_cache(clean, result_rows, was_truncated)

                log.info("[query_executor] execute_ok rows=%s elapsed_ms=%s truncated=%s", len(result_rows), elapsed, was_truncated)
                return result_rows, elapsed, None, was_truncated

            except asyncpg.exceptions.QueryCanceledError:
                await self._safe_rollback(conn)
                elapsed = int((time.time() - start) * 1000)
                err = f"Query timed out after {self.statement_timeout}ms"
                log.warning("[query_executor] timeout elapsed_ms=%s error=%s", elapsed, err)
                return [], elapsed, err, False
            except Exception as e:
                await self._safe_rollback(conn)
                elapsed = int((time.time() - start) * 1000)
                log.warning("[query_executor] execute_failed elapsed_ms=%s error=%s", elapsed, e)
                return [], elapsed, str(e), False

    async def _safe_rollback(self, conn: asyncpg.Connection) -> None:
        try:
            await conn.execute("ROLLBACK")
        except Exception:
            pass

    async def _apply_sql_guard(self, sql: str) -> Optional[str]:
        try:
            rules = await load_sql_guard()
        except Exception:
            rules = []
        for rule in rules:
            pattern = rule.get("pattern")
            if not pattern:
                continue
            try:
                import re
                if re.search(pattern, sql, re.I | re.S):
                    mode = (rule.get("guard_mode") or "block").lower()
                    message = rule.get("message") or f"Blocked by SQL guard: {rule.get('guard_type')}"
                    if mode == "block":
                        return message
                    log.warning("[sql_guard] warn: %s", message)
            except Exception as e:
                log.warning("[sql_guard] bad rule ignored: %s", e)
        return None

    def _cache_key(self, sql: str) -> str:
        return hashlib.sha256(sql.encode("utf-8")).hexdigest()

    async def _check_cache(self, sql: str) -> Optional[tuple[list[dict], bool]]:
        try:
            key = self._cache_key(sql)
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT result_json, was_truncated
                    FROM rag.query_result_cache
                    WHERE query_hash = $1
                      AND expires_at > NOW()
                    LIMIT 1
                    """,
                    key,
                )
                if row:
                    await conn.execute(
                        "UPDATE rag.query_result_cache SET hit_count = hit_count + 1 WHERE query_hash = $1",
                        key,
                    )
                    result_json = row["result_json"]
                    if isinstance(result_json, str):
                        result_json = json.loads(result_json)
                    return result_json, bool(row["was_truncated"])
        except Exception as e:
            # Cache table may not exist until migration is applied; never fail query.
            log.debug("[query_cache] check skipped: %s", e)
        return None

    async def _save_cache(self, sql: str, rows: list[dict], was_truncated: bool) -> None:
        try:
            key = self._cache_key(sql)
            result_json = json.dumps(rows, ensure_ascii=False, default=str)
            ttl = int(settings.QUERY_CACHE_TTL)
            async with self.pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO rag.query_result_cache
                        (cache_key, query_hash, sql_text, result_json, row_count, was_truncated, expires_at, hit_count)
                    VALUES
                        ($1, $1, $2, $3::jsonb, $4, $5, NOW() + ($6 * INTERVAL '1 second'), 1)
                    ON CONFLICT (cache_key)
                    DO UPDATE SET
                        result_json   = EXCLUDED.result_json,
                        row_count     = EXCLUDED.row_count,
                        was_truncated = EXCLUDED.was_truncated,
                        expires_at    = EXCLUDED.expires_at,
                        created_at    = NOW(),
                        hit_count     = rag.query_result_cache.hit_count + 1
                    """,
                    key,
                    sql,
                    result_json,
                    len(rows),
                    was_truncated,
                    ttl,
                )
        except Exception as e:
            log.debug("[query_cache] save skipped: %s", e)


async def execute_sql(pool: asyncpg.Pool, sql: str, use_cache: bool = True) -> tuple[list[dict], int, Optional[str], bool]:
    executor = QueryExecutor(pool)
    return await executor.execute(sql, use_cache)
