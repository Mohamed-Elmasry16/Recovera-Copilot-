"""
sql_validator.py - permissive-but-safe SQL validator v2
=======================================================

The previous validator tried to be security guard, schema checker, business
critic and optimizer at once.  That made complex analytical SQL fail before
PostgreSQL had a chance to compile it.

This version has a narrower contract:
1. Block only unsafe/non-read-only SQL, multiple statements and disallowed tables.
2. Emit business-quality warnings, never fatal, via sql_critic.py.
3. Optionally ask the LLM for an optimized rewrite, but never let the LLM override
   deterministic safety.
4. Leave real column/alias/CTE validation to PostgreSQL EXPLAIN inside the executor.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Optional

from app.agents.sql_critic import critique_sql
from app.agents.sql_safety import normalize_sql, validate_table_access
from app.core.multi_key_router import call_llm

log = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    valid: bool = True
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    fatal: bool = False
    confidence: float = 1.0
    optimized_sql: Optional[str] = None

    def add_issue(self, msg: str, fatal: bool = False):
        self.issues.append(msg)
        self.valid = False
        if fatal:
            self.fatal = True

    def add_warning(self, msg: str):
        self.warnings.append(msg)

    @property
    def ok(self) -> bool:
        return self.valid and not self.fatal

    def to_dict(self) -> dict:
        return {
            "valid": self.ok,
            "issues": self.issues,
            "warnings": self.warnings,
            "fatal": self.fatal,
            "confidence": self.confidence,
            "optimized_sql": self.optimized_sql,
        }


_MAX_SQL_CHARS = 4_000
_VALIDATOR_LLM_ENABLED = os.getenv("VALIDATOR_LLM_ENABLED", "false").lower() in {"1", "true", "yes"}

VALIDATOR_PROMPT = """You are a PostgreSQL analytics SQL critic. Deterministic code already blocked unsafe SQL.
Return ONLY JSON with this shape:
{"valid": true, "issues": [], "warnings": [], "confidence": 0.90, "optimized_sql": null}
Rules:
- Do not mark complex SQL invalid because it uses CTEs, windows, subqueries, UNION ALL, CASE or FILTER.
- Mark invalid only for clear analytical logic errors.
- Business style problems should be warnings.
- optimized_sql may be SELECT or WITH; otherwise null.
"""


def _truncate_sql(sql: str) -> str:
    if len(sql) <= _MAX_SQL_CHARS:
        return sql
    return sql[:3200] + f"\n-- ... [{len(sql)-3800} chars omitted] ...\n" + sql[-600:]


def validate_deterministic(sql: str) -> ValidationResult:
    result = ValidationResult()
    safety = validate_table_access(sql)

    if safety.fatal:
        for err in safety.errors:
            result.add_issue(f"[safety] {err}", fatal=True)
        result.confidence = 0.0
        return result

    result.optimized_sql = safety.normalized_sql or normalize_sql(sql)

    for warn in safety.warnings:
        result.add_warning(warn)

    critic = critique_sql(result.optimized_sql)
    for warn in critic.warnings:
        result.add_warning(warn)

    # Confidence reflects safety certainty, not whether SQL compiles. EXPLAIN is next.
    result.confidence = 0.94 if not result.warnings else 0.88
    return result


class SQLValidatorAgent:
    async def validate_llm(self, sql: str, deterministic_result: ValidationResult) -> ValidationResult:
        if deterministic_result.fatal or not _VALIDATOR_LLM_ENABLED:
            return deterministic_result

        messages = [
            {"role": "system", "content": VALIDATOR_PROMPT},
            {"role": "user", "content": "Critique this SQL:\n```sql\n" + _truncate_sql(sql) + "\n```"},
        ]
        try:
            content, _, _ = await call_llm(
                agent="validator",
                messages=messages,
                temperature=0.0,
                max_tokens=1024,
            )
            parsed = self._parse(content)
            for issue in parsed.get("issues", []) or []:
                # LLM issues are non-fatal unless deterministic safety already failed.
                deterministic_result.add_warning(f"[llm] {issue}")
            for warning in parsed.get("warnings", []) or []:
                deterministic_result.add_warning(f"[llm] {warning}")
            deterministic_result.confidence = min(
                deterministic_result.confidence,
                float(parsed.get("confidence", deterministic_result.confidence) or deterministic_result.confidence),
            )
            opt = parsed.get("optimized_sql")
            if opt and isinstance(opt, str) and re.match(r"^\s*(SELECT|WITH)\b", opt, re.I):
                # Re-run deterministic safety before accepting the rewrite.
                opt_result = validate_deterministic(opt)
                if not opt_result.fatal:
                    deterministic_result.optimized_sql = opt_result.optimized_sql
        except Exception as e:
            log.warning("[validator] LLM validator skipped/failed: %s", e)
            deterministic_result.confidence = min(deterministic_result.confidence, 0.86)
        return deterministic_result

    def _parse(self, text: str) -> dict:
        text = text.strip()
        if "</think>" in text:
            text = text.split("</think>")[-1].strip()
        if text.startswith("```json"):
            text = text[7:]
        elif text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
        try:
            return json.loads(text)
        except Exception:
            start, end = text.find("{"), text.rfind("}")
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start:end + 1])
                except Exception:
                    pass
        return {"valid": True, "issues": [], "warnings": [], "confidence": 0.75, "optimized_sql": None}


validator_agent = SQLValidatorAgent()


async def validate_sql(sql: str, plan: dict | None = None) -> ValidationResult:
    """Public validation API used by the orchestrator."""
    started = time.perf_counter()
    intent = (plan or {}).get("intent", "unknown")
    route = (plan or {}).get("route", "unknown")
    log.info("[sql_validator] start intent=%s route=%s chars=%s", intent, route, len(sql or ""))

    result = validate_deterministic(sql)
    if result.fatal:
        elapsed = int((time.perf_counter() - started) * 1000)
        log.warning(
            "[sql_validator] blocked elapsed_ms=%s issues=%s",
            elapsed,
            "; ".join(result.issues),
        )
        return result

    # Let PostgreSQL EXPLAIN inside QueryExecutor validate columns, aliases, CTE
    # outputs and function signatures.  The optional LLM critic is intentionally
    # disabled by default; enable with VALIDATOR_LLM_ENABLED=true.
    result = await validator_agent.validate_llm(result.optimized_sql or sql, result)
    elapsed = int((time.perf_counter() - started) * 1000)
    log.info(
        "[sql_validator] ok elapsed_ms=%s warnings=%s confidence=%.2f optimized=%s llm_enabled=%s",
        elapsed,
        len(result.warnings),
        result.confidence,
        bool(result.optimized_sql),
        _VALIDATOR_LLM_ENABLED,
    )
    return result
