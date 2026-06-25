"""
sql_safety.py - deterministic read-only SQL safety primitives.

This module intentionally checks only safety/shape.  It does not enforce
business-style rules; those belong in sql_critic.py.  The final schema compiler
is PostgreSQL EXPLAIN, not a hand-written regex validator.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

import sqlglot
import sqlglot.expressions as exp

from app.core.schema_registry import ALLOWED_SCHEMAS, get_schema_registry

_COMMENT_BLOCK = re.compile(r"/\*.*?\*/", re.S)
_COMMENT_LINE = re.compile(r"--.*?$", re.M)
_TOP_LEVEL_BLOCKED = re.compile(
    r"^\s*(insert|update|delete|drop|alter|create|truncate|copy|call|do|set|reset|grant|revoke|vacuum|analyze|merge)\b",
    re.I,
)
_FORBIDDEN_NODES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.Command,
)


@dataclass
class SafetyResult:
    ok: bool = True
    fatal: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    normalized_sql: Optional[str] = None

    def block(self, message: str) -> None:
        self.ok = False
        self.fatal = True
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)


def strip_sql_comments(sql: str) -> str:
    sql = _COMMENT_BLOCK.sub(" ", sql or "")
    sql = _COMMENT_LINE.sub(" ", sql)
    return sql.strip()


def normalize_sql(sql: str) -> str:
    clean = strip_sql_comments(sql)
    # Remove a single trailing semicolon; multiple statements are checked separately.
    return clean.rstrip().rstrip(";").strip()


def validate_readonly_shape(sql: str) -> SafetyResult:
    result = SafetyResult()
    clean = normalize_sql(sql)
    if not clean:
        result.block("Empty SQL.")
        return result

    if _TOP_LEVEL_BLOCKED.search(clean):
        result.block("Only read-only SELECT/WITH queries are allowed.")
        return result

    try:
        statements = sqlglot.parse(clean, read="postgres")
    except Exception as e:
        result.block(f"SQL parse error: {e}")
        return result

    if len(statements) != 1:
        result.block("Multiple SQL statements are not allowed.")
        return result

    tree = statements[0]
    for node in tree.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            result.block(f"Forbidden SQL operation: {type(node).__name__}")
            return result

    if not isinstance(tree, (exp.Select, exp.Union, exp.Except, exp.Intersect)):
        result.block("Top-level statement must be SELECT, WITH SELECT, or a SELECT set operation.")
        return result

    result.normalized_sql = clean
    return result


def validate_table_access(sql: str) -> SafetyResult:
    """Validate schemas/tables when resolvable.  CTE names are not blocked."""
    result = validate_readonly_shape(sql)
    if result.fatal:
        return result

    registry = get_schema_registry()
    try:
        tree = sqlglot.parse_one(result.normalized_sql or sql, read="postgres")
    except Exception as e:
        result.block(f"SQL parse error: {e}")
        return result

    # CTE aliases are legal table references without a schema.
    cte_names: set[str] = set()
    with_expr = tree.args.get("with") or tree.args.get("with_")
    if with_expr:
        for cte in with_expr.find_all(exp.CTE):
            alias = cte.alias_or_name
            if alias:
                cte_names.add(alias.lower())

    for table in tree.find_all(exp.Table):
        table_name = (table.name or "").lower()
        schema = (table.db or "").lower()
        if not table_name:
            continue
        if not schema:
            if table_name in cte_names:
                continue
            # unqualified base tables are allowed but warned; EXPLAIN will decide.
            if table_name not in registry.known_table_names():
                result.warn(f"[schema] Unqualified or CTE table reference: {table_name}")
            continue
        if schema not in ALLOWED_SCHEMAS:
            result.block(f"Schema not allowed: {schema}")
            return result
        full = f"{schema}.{table_name}"
        if not registry.table_exists(full):
            result.block(f"Unknown table/view: {full}")
            return result

    return result


def is_readonly_sql(sql: str) -> bool:
    return not validate_table_access(sql).fatal


def ensure_outer_limit(sql: str, max_rows: int) -> str:
    """Wrap query so execution never returns more than max_rows+1 rows."""
    clean = normalize_sql(sql)
    return f"SELECT * FROM (\n{clean}\n) AS __safe_query_limit LIMIT {int(max_rows) + 1}"
