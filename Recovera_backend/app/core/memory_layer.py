"""
memory_layer.py - Conversation Memory & Context Tracking
===========================================================
Maintains:
  - Conversation history per session
  - Active filters (city, seller, date range, etc.)
  - Active entities (sellers, customers, campaigns being discussed)
  - Previous drilldowns (what the user already explored)
  - Analytical context (trends, comparisons, time periods)

Enables follow-up queries like:
  - "compare that to last month"
  - "show Cairo sellers only"
  - "drill deeper into refunds"
  - "compare electronics sellers"
  - "analyze only delayed orders"
"""

import time
import json
import logging
from typing import Optional
from dataclasses import dataclass, field, asdict

log = logging.getLogger(__name__)

# ================================================================
# MEMORY STRUCTURES
# ================================================================

@dataclass
class ActiveFilter:
    """A filter that the user has implicitly or explicitly applied."""
    column: str
    operator: str
    value: str
    source: str = "user"  # 'user', 'inferred', 'drilldown'
    set_at: float = field(default_factory=time.time)

    def to_sql(self) -> str:
        """Convert filter to SQL WHERE clause fragment."""
        if self.operator.upper() == "IN":
            return f"{self.column} IN {self.value}"
        elif self.operator.upper() == "ANY":
            return f"'{self.value}' = ANY({self.column})"
        elif self.operator.upper() == "BETWEEN":
            return f"{self.column} BETWEEN {self.value}"
        elif self.operator.upper() in ("LIKE", "ILIKE"):
            return f"{self.column} {self.operator} '{self.value}'"
        else:
            return f"{self.column} {self.operator} {self.value}"


@dataclass
class DrilldownContext:
    """Tracks what the user has already explored."""
    topic: str
    metric: str
    dimension: str
    previous_value: str
    depth: int = 1
    timestamp: float = field(default_factory=time.time)


@dataclass
class SessionMemory:
    """Complete memory state for a chat session."""
    session_id: str
    filters: list[ActiveFilter] = field(default_factory=list)
    entities: dict[str, list[str]] = field(default_factory=dict)  # entity_type -> list of IDs/names
    drilldowns: list[DrilldownContext] = field(default_factory=list)
    last_intent: str = ""
    last_tables: list[str] = field(default_factory=list)
    last_metrics: list[str] = field(default_factory=list)
    time_grain: str = ""  # monthly, quarterly, yearly
    language: str = "english"
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)

    def get_entity(self, entity_type: str) -> list[str]:
        return self.entities.get(entity_type, [])

    def add_entity(self, entity_type: str, value: str):
        if entity_type not in self.entities:
            self.entities[entity_type] = []
        if value not in self.entities[entity_type]:
            self.entities[entity_type].append(value)

    def add_filter(self, column: str, operator: str, value: str, source: str = "user"):
        # Remove existing filter on same column
        self.filters = [f for f in self.filters if f.column != column]
        self.filters.append(ActiveFilter(column=column, operator=operator, value=value, source=source))

    def get_active_filters_sql(self) -> list[str]:
        """Get all active filters as SQL fragments."""
        return [f.to_sql() for f in self.filters]

    def get_last_drilldown(self) -> Optional[DrilldownContext]:
        if self.drilldowns:
            return self.drilldowns[-1]
        return None

    def record_drilldown(self, topic: str, metric: str, dimension: str, value: str):
        # Check if continuing same drilldown
        last = self.get_last_drilldown()
        depth = 1
        if last and last.topic == topic:
            depth = last.depth + 1
        self.drilldowns.append(DrilldownContext(
            topic=topic, metric=metric, dimension=dimension,
            previous_value=value, depth=depth
        ))

    def is_followup(self, message: str) -> bool:
        """Detect if message is a follow-up to previous context."""
        followup_indicators = [
            "compare", "compare that", "and last", "and previous",
            "drill", "deeper", "more detail", "show me", "only",
            "filter", "what about", "how about", "and for",
            "also", "add", "remove", "change", "instead",
            "مقارنة", "المقارنة", "اكتر", "تفاصيل", "بس", "فقط",
        ]
        msg_lower = message.lower()
        return any(ind in msg_lower for ind in followup_indicators)

    def build_context_prompt(self) -> str:
        """Build a context section for the planner prompt based on memory."""
        parts = []

        # Active filters
        if self.filters:
            parts.append("ACTIVE FILTERS:")
            for f in self.filters:
                parts.append(f"  - {f.column} {f.operator} {f.value}")

        # Active entities
        if self.entities:
            parts.append("ACTIVE ENTITIES:")
            for etype, values in self.entities.items():
                if values:
                    parts.append(f"  - {etype}: {', '.join(values[:5])}")

        # Recent drilldown
        last = self.get_last_drilldown()
        if last:
            parts.append(f"PREVIOUS ANALYSIS: {last.topic} by {last.dimension} = {last.previous_value}")

        # Time grain preference
        if self.time_grain:
            parts.append(f"PREFERRED TIME GRAIN: {self.time_grain}")

        return "\n".join(parts)


# ================================================================
# MEMORY STORE (In-Memory with DB Persistence)
# ================================================================

class MemoryStore:
    """Manages session memory with in-memory cache and DB persistence."""

    def __init__(self):
        self._sessions: dict[str, SessionMemory] = {}
        self._max_sessions = 1000  # LRU eviction

    def get_or_create(self, session_id: str) -> SessionMemory:
        """Get existing session or create new one."""
        if session_id not in self._sessions:
            self._sessions[session_id] = SessionMemory(session_id=session_id)
            # Evict oldest if over limit
            if len(self._sessions) > self._max_sessions:
                oldest = min(self._sessions.items(), key=lambda x: x[1].last_activity)
                del self._sessions[oldest[0]]
        return self._sessions[session_id]

    def update(self, session_id: str, plan: dict, intent: str):
        """Update memory after a query is processed."""
        mem = self.get_or_create(session_id)
        mem.last_activity = time.time()
        mem.last_intent = intent

        # Track tables
        tables = plan.get("tables_needed", [])
        if plan.get("target_source"):
            tables.append(plan.get("target_source"))
        mem.last_tables = list(set(tables))

        # Track metrics
        mem.last_metrics = plan.get("metrics", [])

        # Track time grain
        if plan.get("time_grain"):
            mem.time_grain = plan.get("time_grain")

        # Track language
        if plan.get("question_language"):
            mem.language = plan.get("question_language")

        # Extract entities from filters
        for f in plan.get("filters", []):
            col = f.get("col", "")
            val = f.get("val", "")
            if col and val:
                mem.add_filter(col, f.get("op", "="), val, "inferred")
                # Also track as entity
                entity_type = col.split(".")[-1] if "." in col else col
                mem.add_entity(entity_type, str(val))

    def record_drilldown(self, session_id: str, topic: str, metric: str, dimension: str, value: str):
        """Record a drill-down action."""
        mem = self.get_or_create(session_id)
        mem.record_drilldown(topic, metric, dimension, value)

    def clear_session(self, session_id: str):
        """Clear a session's memory."""
        if session_id in self._sessions:
            del self._sessions[session_id]

    def get_memory(self, session_id: str) -> Optional[SessionMemory]:
        return self._sessions.get(session_id)

    def apply_memory_to_plan(self, session_id: str, plan: dict, message: str) -> dict:
        """
        Enrich a planner output with session memory.
        Adds implicit filters, entities, and context from previous turns.
        """
        mem = self.get_or_create(session_id)

        if not mem.is_followup(message):
            return plan

        # Merge active filters into plan
        active_filters = mem.get_active_filters_sql()
        if active_filters:
            existing = plan.get("filters", [])
            # Don't duplicate
            existing_cols = {f.get("col", "") for f in existing}
            for sql_filter in active_filters:
                # Parse simple filters back
                parts = sql_filter.split(None, 2)
                if len(parts) >= 3:
                    col, op, val = parts[0], parts[1], parts[2]
                    if col not in existing_cols:
                        existing.append({"col": col, "op": op, "val": val})
            plan["filters"] = existing

        # If no tables specified but we have history, use last tables
        if not plan.get("tables_needed") and mem.last_tables:
            plan["tables_needed"] = mem.last_tables

        # If no time grain but we have preference, apply it
        if not plan.get("time_grain") and mem.time_grain:
            plan["time_grain"] = mem.time_grain

        return plan


# Global instance
memory_store = MemoryStore()


def get_memory(session_id: str) -> SessionMemory:
    """Public API to get session memory."""
    return memory_store.get_or_create(session_id)
