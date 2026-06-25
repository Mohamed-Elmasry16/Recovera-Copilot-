"""
schema_registry.py - Runtime database contract for Text-to-SQL
================================================================

This module is the single source of truth for schema-aware SQL generation and
validation.  It can load from the live database when a pool is available, and it
falls back to the exported JSON files under data/db_schema for local tests and
cold starts.

Design goals:
- no hardcoded column allowlists inside the SQL validator;
- materialized views are first-class query targets;
- enum values, generated columns, PKs, FKs and indexes are available to prompts;
- schema drift is detected early and logged.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

import asyncpg

log = logging.getLogger(__name__)

ALLOWED_SCHEMAS: set[str] = {"ecommerce", "ml_output", "marketing", "rag"}
PREFERRED_MATERIALIZED_VIEWS: tuple[str, ...] = (
    "ml_output.mv_leakage_dashboard",
    "ml_output.mv_monthly_leakage",
    "ml_output.mv_seller_risk",
    "ml_output.mv_leakage_by_scenario",
)


@dataclass(slots=True)
class ColumnInfo:
    schema: str
    table: str
    name: str
    data_type: str = ""
    udt_name: str = ""
    nullable: bool = True
    generated: bool = False
    default: Optional[str] = None
    comment: Optional[str] = None

    @property
    def full_table(self) -> str:
        return f"{self.schema}.{self.table}".lower()


@dataclass(slots=True)
class TableInfo:
    schema: str
    name: str
    kind: str = "table"  # table | materialized_view | view | cte/unknown
    comment: str = ""
    row_count: Optional[int] = None
    columns: dict[str, ColumnInfo] = field(default_factory=dict)
    primary_keys: set[str] = field(default_factory=set)
    generated_columns: set[str] = field(default_factory=set)
    indexes: list[str] = field(default_factory=list)

    @property
    def full_name(self) -> str:
        return f"{self.schema}.{self.name}".lower()


@dataclass(slots=True)
class ForeignKeyInfo:
    table: str
    column: str
    ref_table: str
    ref_column: str


@dataclass
class SchemaRegistry:
    tables: dict[str, TableInfo] = field(default_factory=dict)
    enums: dict[str, set[str]] = field(default_factory=dict)
    foreign_keys: list[ForeignKeyInfo] = field(default_factory=list)
    loaded_from: str = "empty"

    # ------------------------- basic lookup -------------------------
    def table_exists(self, full_table: str) -> bool:
        return normalize_table_name(full_table) in self.tables

    def get_table(self, full_table: str) -> Optional[TableInfo]:
        return self.tables.get(normalize_table_name(full_table))

    def column_exists(self, full_table: str, column: str) -> bool:
        table = self.get_table(full_table)
        if not table:
            return False
        return column.lower() in table.columns

    def columns_for(self, full_table: str) -> set[str]:
        table = self.get_table(full_table)
        return set(table.columns) if table else set()

    def is_generated(self, full_table: str, column: str) -> bool:
        table = self.get_table(full_table)
        return bool(table and column.lower() in table.generated_columns)

    def enum_values_for_column(self, column_name: str) -> set[str]:
        """Best-effort enum lookup by logical column name."""
        col = column_name.lower()
        direct = {
            "order_status": "order_status_t",
            "payment_status": "payment_status_t",
            "payment_type": "payment_type_t",
            "shipping_status": "shipping_status_t",
            "risk_tier": "risk_tier_t",
            "segment": "customer_segment_t",
            "customer_segment": "customer_segment_t",
            "churn_risk": "churn_risk_t",
            "leakage_type": "leakage_scenario_t",
            "scenario": "leakage_scenario_t",
        }
        enum_name = direct.get(col)
        return self.enums.get(enum_name, set()) if enum_name else set()

    def known_table_names(self) -> set[str]:
        names = set(self.tables)
        names.update(t.name.lower() for t in self.tables.values())
        return names

    def compact_prompt_context(self, tables_needed: Iterable[str] | None = None, max_tables: int = 12) -> str:
        """Generate compact deterministic schema context for prompts."""
        selected: list[TableInfo] = []
        if tables_needed:
            for name in tables_needed:
                tbl = self.get_table(name)
                if tbl:
                    selected.append(tbl)
        if not selected:
            for name in PREFERRED_MATERIALIZED_VIEWS:
                tbl = self.get_table(name)
                if tbl:
                    selected.append(tbl)
            for tbl in self.tables.values():
                if tbl.schema in {"ecommerce", "marketing"} and len(selected) < max_tables:
                    selected.append(tbl)

        lines = ["SCHEMA REGISTRY (source: %s)" % self.loaded_from]
        for tbl in selected[:max_tables]:
            cols = []
            for c in tbl.columns.values():
                marker = " [GENERATED]" if c.generated else ""
                cols.append(f"{c.name}:{c.udt_name or c.data_type}{marker}")
            lines.append(f"- {tbl.full_name} ({tbl.kind}): " + ", ".join(cols[:40]))
            if tbl.comment:
                lines.append(f"  note: {tbl.comment[:220]}")
        return "\n".join(lines)

    def preferred_source_for_intent(self, intent: str) -> list[str]:
        mapping = {
            "simple_lookup": ["ml_output.mv_leakage_dashboard"],
            "aggregation": ["ml_output.mv_leakage_dashboard", "ml_output.mv_monthly_leakage"],
            "trend_analysis": ["ml_output.mv_monthly_leakage", "ml_output.mv_leakage_dashboard"],
            "seller_analysis": ["ml_output.mv_seller_risk"],
            "scenario_analysis": ["ml_output.mv_leakage_by_scenario", "ml_output.mv_leakage_dashboard"],
            "campaign_analysis": ["marketing.campaign_attribution", "marketing.marketing_campaigns", "ecommerce.orders"],
            "web_analytics": ["marketing.website_sessions", "marketing.customer_interactions", "marketing.campaign_attribution"],
            "review_analysis": ["ml_output.mv_leakage_dashboard", "ecommerce.reviews"],
        }
        return [t for t in mapping.get(intent, PREFERRED_MATERIALIZED_VIEWS) if self.table_exists(t)]


def normalize_table_name(name: str) -> str:
    return name.strip().strip('"').lower()


_REGISTRY: SchemaRegistry = SchemaRegistry()


def get_schema_registry() -> SchemaRegistry:
    return _REGISTRY


def set_schema_registry(registry: SchemaRegistry) -> None:
    global _REGISTRY
    _REGISTRY = registry


async def init_schema_registry(pool: asyncpg.Pool | None = None, metadata_dir: str | Path | None = None) -> SchemaRegistry:
    """Load the registry from the live DB, falling back to JSON exports."""
    if pool is not None:
        try:
            registry = await load_from_database(pool)
            set_schema_registry(registry)
            log.info("Schema registry loaded from database: %s tables", len(registry.tables))
            return registry
        except Exception as e:
            log.warning("Schema registry DB load failed; falling back to JSON exports: %s", e)

    registry = load_from_exports(metadata_dir)
    set_schema_registry(registry)
    log.info("Schema registry loaded from exports: %s tables", len(registry.tables))
    return registry


async def load_from_database(pool: asyncpg.Pool) -> SchemaRegistry:
    reg = SchemaRegistry(loaded_from="database")
    async with pool.acquire() as conn:
        # Use pg_catalog instead of information_schema.columns because
        # PostgreSQL/Supabase information_schema does not reliably expose
        # materialized-view columns.  pg_attribute covers ordinary tables,
        # partitioned tables, views, and materialized views.
        cols = await conn.fetch(
            """
            SELECT
                n.nspname AS table_schema,
                c.relname AS table_name,
                a.attname AS column_name,
                format_type(a.atttypid, a.atttypmod) AS data_type,
                t.typname AS udt_name,
                CASE WHEN a.attnotnull THEN 'NO' ELSE 'YES' END AS is_nullable,
                pg_get_expr(ad.adbin, ad.adrelid) AS column_default,
                CASE WHEN a.attgenerated <> '' THEN 'ALWAYS' ELSE 'NEVER' END AS is_generated,
                CASE WHEN a.attgenerated <> '' THEN pg_get_expr(ad.adbin, ad.adrelid) ELSE NULL END AS generation_expression,
                a.attnum AS ordinal_position,
                col_description(c.oid, a.attnum) AS column_comment,
                CASE c.relkind WHEN 'm' THEN 'materialized_view'
                               WHEN 'v' THEN 'view'
                               ELSE 'table' END AS relation_kind,
                obj_description(c.oid, 'pg_class') AS table_comment
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_attribute a ON a.attrelid = c.oid
            JOIN pg_type t ON t.oid = a.atttypid
            LEFT JOIN pg_attrdef ad ON ad.adrelid = c.oid AND ad.adnum = a.attnum
            WHERE n.nspname = ANY($1::text[])
              AND c.relkind IN ('r','p','m','v')
              AND a.attnum > 0
              AND NOT a.attisdropped
            ORDER BY n.nspname, c.relname, a.attnum
            """,
            list(ALLOWED_SCHEMAS),
        )
        for row in cols:
            row_dict = dict(row)
            _add_column(reg, row_dict)
            full = f"{row_dict['table_schema']}.{row_dict['table_name']}".lower()
            tbl = reg.tables.get(full)
            if tbl:
                tbl.kind = row_dict.get("relation_kind") or tbl.kind
                tbl.comment = row_dict.get("table_comment") or tbl.comment

        table_comments = await conn.fetch(
            """
            SELECT n.nspname AS table_schema, c.relname AS table_name,
                   obj_description(c.oid, 'pg_class') AS table_comment,
                   CASE c.relkind WHEN 'm' THEN 'materialized_view'
                                  WHEN 'v' THEN 'view'
                                  ELSE 'table' END AS kind
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = ANY($1::text[])
              AND c.relkind IN ('r','p','m','v')
            """,
            list(ALLOWED_SCHEMAS),
        )
        for row in table_comments:
            full = f"{row['table_schema']}.{row['table_name']}".lower()
            tbl = reg.tables.setdefault(
                full,
                TableInfo(schema=row["table_schema"], name=row["table_name"], kind=row["kind"]),
            )
            tbl.comment = row["table_comment"] or tbl.comment or ""
            tbl.kind = row["kind"]

        missing_preferred = [name for name in PREFERRED_MATERIALIZED_VIEWS if name not in reg.tables]
        if missing_preferred:
            log.warning("Preferred materialized views missing from registry: %s", missing_preferred)
        else:
            log.info("Preferred materialized views registered: %s", ", ".join(PREFERRED_MATERIALIZED_VIEWS))

        enum_rows = await conn.fetch(
            """
            SELECT t.typname AS enum_name, e.enumlabel AS enum_value
            FROM pg_type t
            JOIN pg_enum e ON t.oid = e.enumtypid
            JOIN pg_namespace n ON n.oid = t.typnamespace
            ORDER BY t.typname, e.enumsortorder
            """
        )
        for row in enum_rows:
            reg.enums.setdefault(row["enum_name"], set()).add(row["enum_value"])

        fk_rows = await conn.fetch(
            """
            SELECT tc.table_schema, tc.table_name, kcu.column_name,
                   ccu.table_schema AS ref_schema, ccu.table_name AS ref_table,
                   ccu.column_name AS ref_column
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
            JOIN information_schema.constraint_column_usage ccu
              ON ccu.constraint_name = tc.constraint_name
             AND ccu.table_schema = tc.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
              AND tc.table_schema = ANY($1::text[])
            """,
            list(ALLOWED_SCHEMAS),
        )
        for row in fk_rows:
            reg.foreign_keys.append(ForeignKeyInfo(
                table=f"{row['table_schema']}.{row['table_name']}".lower(),
                column=row["column_name"].lower(),
                ref_table=f"{row['ref_schema']}.{row['ref_table']}".lower(),
                ref_column=row["ref_column"].lower(),
            ))
    return reg


def load_from_exports(metadata_dir: str | Path | None = None) -> SchemaRegistry:
    base = Path(metadata_dir) if metadata_dir else Path(__file__).resolve().parents[2] / "data" / "db_schema"
    reg = SchemaRegistry(loaded_from=str(base))

    for row in _load_json(base / "columns.json", []):
        _add_column(reg, row)

    for row in _load_json(base / "materialized_views.json", []):
        full = f"{row.get('schemaname')}.{row.get('matviewname')}".lower()
        tbl = reg.tables.setdefault(full, TableInfo(schema=row.get("schemaname", ""), name=row.get("matviewname", "")))
        tbl.kind = "materialized_view"
        tbl.comment = row.get("mv_comment") or tbl.comment
        for col_name in row.get("columns", []):
            c = col_name.lower()
            tbl.columns.setdefault(c, ColumnInfo(tbl.schema, tbl.name, col_name))

    for row in _load_json(base / "table_comments.json", []):
        full = f"{row.get('table_schema')}.{row.get('table_name')}".lower()
        if full in reg.tables:
            reg.tables[full].comment = row.get("table_comment") or reg.tables[full].comment

    for row in _load_json(base / "primary_keys.json", []):
        full = f"{row.get('table_schema')}.{row.get('table_name')}".lower()
        if full in reg.tables:
            reg.tables[full].primary_keys.add((row.get("column_name") or "").lower())

    for row in _load_json(base / "indexes.json", []):
        full = f"{row.get('schemaname')}.{row.get('tablename')}".lower()
        if full in reg.tables:
            reg.tables[full].indexes.append(row.get("indexdef") or "")

    for row in _load_json(base / "foreign_keys.json", []):
        reg.foreign_keys.append(ForeignKeyInfo(
            table=f"{row.get('table_schema')}.{row.get('table_name')}".lower(),
            column=(row.get("column_name") or "").lower(),
            ref_table=f"{row.get('ref_schema')}.{row.get('ref_table')}".lower(),
            ref_column=(row.get("ref_column") or "").lower(),
        ))

    for row in _load_json(base / "enums.json", []):
        enum_name = row.get("enum_name")
        values = row.get("enum_values") or []
        if enum_name:
            reg.enums[enum_name] = set(values)

    for row in _load_json(base / "row_counts.json", []):
        full = f"{row.get('schema')}.{row.get('table_name')}".lower()
        if full in reg.tables:
            reg.tables[full].row_count = row.get("estimated_rows")

    return reg


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except Exception as e:
        log.warning("Could not load schema export %s: %s", path, e)
        return default


def _add_column(reg: SchemaRegistry, row: dict[str, Any]) -> None:
    schema = (row.get("table_schema") or row.get("schemaname") or "").lower()
    table = (row.get("table_name") or row.get("tablename") or "").lower()
    col = (row.get("column_name") or "").lower()
    if not schema or not table or not col:
        return
    if schema not in ALLOWED_SCHEMAS:
        return
    full = f"{schema}.{table}"
    tbl = reg.tables.setdefault(full, TableInfo(schema=schema, name=table))
    generated = (row.get("is_generated") or "").upper() == "ALWAYS" or bool(row.get("generation_expression"))
    info = ColumnInfo(
        schema=schema,
        table=table,
        name=col,
        data_type=row.get("data_type") or "",
        udt_name=row.get("udt_name") or "",
        nullable=(row.get("is_nullable") or "YES") == "YES",
        generated=generated,
        default=row.get("column_default"),
        comment=row.get("column_comment"),
    )
    tbl.columns[col] = info
    if generated:
        tbl.generated_columns.add(col)
