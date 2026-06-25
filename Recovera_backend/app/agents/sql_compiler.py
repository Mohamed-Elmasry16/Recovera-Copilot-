"""PostgreSQL compiler validation via EXPLAIN.

EXPLAIN is the authoritative schema/syntax validator.  It checks real aliases,
CTE output columns, enum casts, function names, table names and column names
without executing the query.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

import asyncpg

from app.agents.sql_safety import normalize_sql, validate_table_access

log = logging.getLogger(__name__)


@dataclass(slots=True)
class CompileResult:
    ok: bool
    error: Optional[str] = None
    plan: Any = None
    estimated_cost: Optional[float] = None


async def explain_validate(conn: asyncpg.Connection, sql: str, timeout_ms: int = 3000) -> CompileResult:
    started = time.perf_counter()
    safety = validate_table_access(sql)
    if safety.fatal:
        msg = "; ".join(safety.errors)
        log.warning("[sql_compiler] safety_blocked error=%s", msg)
        return CompileResult(ok=False, error=msg)

    clean = normalize_sql(sql)
    try:
        await conn.execute("BEGIN READ ONLY")
        await conn.execute("SET LOCAL search_path = ml_output, ecommerce, marketing, rag, public")
        await conn.execute(f"SET LOCAL statement_timeout = '{int(timeout_ms)}ms'")
        await conn.execute("SET LOCAL lock_timeout = '1000ms'")
        plan = await conn.fetchval("EXPLAIN (FORMAT JSON, COSTS TRUE) " + clean)
        await conn.execute("ROLLBACK")

        cost = None
        try:
            if plan and isinstance(plan, list):
                cost = float(plan[0]["Plan"].get("Total Cost", 0))
        except Exception:
            cost = None

        elapsed = int((time.perf_counter() - started) * 1000)
        log.info("[sql_compiler] explain_ok elapsed_ms=%s estimated_cost=%s", elapsed, cost)
        return CompileResult(ok=True, plan=plan, estimated_cost=cost)
    except Exception as e:
        try:
            await conn.execute("ROLLBACK")
        except Exception:
            pass
        elapsed = int((time.perf_counter() - started) * 1000)
        log.warning("[sql_compiler] explain_failed elapsed_ms=%s error=%s", elapsed, e)
        return CompileResult(ok=False, error=str(e))
