"""
orchestrator.py - Multi-Agent Pipeline Orchestrator
====================================================
IMPROVEMENTS:
  - `print(result.sql_used)` replaced with `log.debug()` — was leaking to stdout
    in production on every single request.
  - RAG-only direct DB fetch sort order fixed: was `ORDER BY rating DESC`
    (returned positive reviews first for complaint queries). Now uses
    sentiment-aware ORDER BY that prioritises negative/mixed reviews when
    the query keywords suggest a complaint topic.
  - Improved step labels and log messages for easier debugging.
"""

import asyncio
import time
import logging
import math
from typing import Optional
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import mean, stdev
from decimal import Decimal, InvalidOperation

import asyncpg

from app.core.config import settings
from app.core.context_compression import compress_context, CompressedContext
from app.core.memory_layer import memory_store, get_memory
from app.agents.planner_agent import plan_query, QueryPlan
from app.core.question_patterns import is_forecast_question, forecast_horizon_periods
from app.retrieval.retrieval_engine import retrieve_context, RAGContext
from app.agents.sql_generator import generate_sql, fix_sql
from app.agents.sql_validator import validate_sql, ValidationResult
from app.agents.query_executor import execute_sql
from app.agents.analytics_interpreter import interpret_results, interpret_direct
from app.core.chart_grammar import build_chart_data
from app.database.postgres import (
    load_schema_context, load_sql_guard, save_turn, get_next_turn_index,
    log_query, log_retrieval, load_history,
)

log = logging.getLogger(__name__)

# ================================================================
# CONSTANTS
# ================================================================

_REVIEW_INTENTS = frozenset({
    "review_analysis",
    "sentiment_analysis",
    "leakage_root_cause_analysis",
})

# ── BUG B FIX ──────────────────────────────────────────────────────────────
# web_analytics had NO zero-rows handler (unlike _REVIEW_INTENTS).
# When SQL returned 0 rows it fell into the generic `else` branch which called
# interpret_direct() with no actual data → generic "no data" response.
_WEB_ANALYTICS_INTENTS = frozenset({"web_analytics"})

# Fallback SQL used when the time-filtered query returns 0 rows.
# Removes the 12-month restriction and queries ALL available data.
# BUG A FIX: the hardcoded `NOW() - interval '12 months'` filter causes 0 rows
#            when all session data pre-dates the window (e.g. data from 2023-2024
#            but current date is 2026).
_WEB_ANALYTICS_ALLTIME_SQL = """
SELECT
    date_trunc('month', ws.session_start)::date         AS month,
    COUNT(DISTINCT ws.session_id)                        AS total_sessions,
    COUNT(DISTINCT o.order_id)                           AS converted_orders,
    ROUND(
        COUNT(DISTINCT o.order_id)::decimal
        / NULLIF(COUNT(DISTINCT ws.session_id), 0) * 100, 2
    )                                                    AS conversion_rate_pct,
    COALESCE(SUM(o.total_revenue), 0)                    AS total_revenue,
    ROUND(COALESCE(AVG(o.profit_margin), 0), 2)          AS avg_profit_margin
FROM marketing.website_sessions ws
LEFT JOIN ecommerce.orders o
    ON  o.customer_id             = ws.customer_id
    AND o.order_purchase_timestamp >= ws.session_start
    AND o.order_purchase_timestamp <  ws.session_start + interval '7 days'
GROUP BY month
ORDER BY month DESC
LIMIT 24;
"""

# Diagnostic SQL to check actual data availability in website_sessions
_WEB_ANALYTICS_DIAGNOSTIC_SQL = """
SELECT
    COUNT(*)                           AS total_sessions,
    MIN(session_start)::date           AS earliest_session,
    MAX(session_start)::date           AS latest_session,
    COUNT(DISTINCT customer_id)        AS unique_customers,
    COUNT(DISTINCT traffic_source)     AS traffic_sources
FROM marketing.website_sessions;
"""

# Fallback 1: customer_interactions as web session proxy (when website_sessions is empty)
_WEB_ANALYTICS_INTERACTIONS_SQL = """
SELECT
    date_trunc('month', ci.interaction_date)::date              AS month,
    COUNT(DISTINCT ci.interaction_id)                           AS total_web_events,
    COUNT(DISTINCT CASE WHEN ci.action_type IN ('purchase','checkout') THEN ci.customer_id END) AS converted_customers,
    ROUND(
        COUNT(DISTINCT CASE WHEN ci.action_type IN ('purchase','checkout') THEN ci.customer_id END)::decimal
        / NULLIF(COUNT(DISTINCT ci.customer_id), 0) * 100, 2
    )                                                           AS conversion_rate_pct,
    COALESCE(SUM(o.total_revenue), 0)                           AS total_revenue,
    ROUND(COALESCE(AVG(o.profit_margin), 0), 2)                 AS avg_profit_margin
FROM marketing.customer_interactions ci
LEFT JOIN ecommerce.orders o
    ON  o.customer_id              = ci.customer_id
    AND o.order_purchase_timestamp >= ci.interaction_date
    AND o.order_purchase_timestamp <  ci.interaction_date + interval '7 days'
WHERE LOWER(ci.channel) IN ('web','website','site')
GROUP BY month
ORDER BY month DESC
LIMIT 24;
"""

# Diagnostic for customer_interactions
_WEB_ANALYTICS_INTERACTIONS_DIAG_SQL = """
SELECT
    COUNT(*)                          AS total_events,
    COUNT(DISTINCT customer_id)       AS unique_customers,
    MIN(interaction_date)::date       AS earliest_event,
    MAX(interaction_date)::date       AS latest_event,
    COUNT(CASE WHEN action_type IN ('purchase','checkout') THEN 1 END) AS converted_events
FROM marketing.customer_interactions
WHERE LOWER(channel) IN ('web','website','site');
"""

# Fallback 2: campaign attribution (when both sessions and interactions are empty)
_WEB_ANALYTICS_CAMPAIGN_SQL = """
SELECT
    mc.channel                                  AS traffic_channel,
    mc.campaign_name,
    COUNT(DISTINCT ca.order_id)                 AS attributed_orders,
    COALESCE(SUM(o.total_revenue), 0)           AS total_revenue,
    ROUND(COALESCE(AVG(o.profit_margin), 0), 2) AS avg_profit_margin,
    ROUND(
        COUNT(DISTINCT ca.order_id)::decimal
        / NULLIF(SUM(COUNT(DISTINCT ca.order_id)) OVER (), 0) * 100, 2
    )                                           AS pct_of_attributed_orders
FROM marketing.campaign_attribution ca
JOIN marketing.marketing_campaigns mc ON mc.campaign_id = ca.campaign_id
JOIN ecommerce.orders o               ON o.order_id     = ca.order_id
GROUP BY mc.channel, mc.campaign_name
ORDER BY total_revenue DESC
LIMIT 20;
"""

_FALLBACK_REVIEW_LIMIT = 100
_FALLBACK_REVIEW_CHARS = 500

# ================================================================
# WEB SOURCE CACHE  (BUG-11 FIX: avoid 2-4 serial DB round-trips per query)
# ================================================================

@dataclass
class _WebSourceCache:
    sessions_count: int = -1          # -1 = not yet checked
    interactions_count: int = -1
    sessions_date_range: str = ""
    interactions_date_range: str = ""
    expires_at: datetime = field(default_factory=lambda: datetime.min)

_web_source_cache = _WebSourceCache()
_WEB_SOURCE_CACHE_TTL = timedelta(hours=1)


async def _refresh_web_source_cache(pool) -> None:
    """Run the 2 diagnostic COUNT queries in parallel and cache results for 1 hour."""
    global _web_source_cache
    results = await asyncio.gather(
        execute_sql(pool=pool, sql=_WEB_ANALYTICS_DIAGNOSTIC_SQL, use_cache=False),
        execute_sql(pool=pool, sql=_WEB_ANALYTICS_INTERACTIONS_DIAG_SQL, use_cache=False),
        return_exceptions=True,
    )
    diag_sessions_result, diag_interactions_result = results

    # Parse sessions diagnostic
    if not isinstance(diag_sessions_result, Exception):
        rows, _, err, _ = diag_sessions_result
        if not err and rows:
            d = rows[0]
            _web_source_cache.sessions_count = d.get("total_sessions", 0)
            earliest = d.get("earliest_session", "?")
            latest   = d.get("latest_session",   "?")
            _web_source_cache.sessions_date_range = f"{earliest} → {latest}"
    else:
        _web_source_cache.sessions_count = 0

    # Parse interactions diagnostic
    if not isinstance(diag_interactions_result, Exception):
        rows, _, err, _ = diag_interactions_result
        if not err and rows:
            ci = rows[0]
            _web_source_cache.interactions_count = ci.get("total_events", 0)
            ci_earliest = ci.get("earliest_event", "?")
            ci_latest   = ci.get("latest_event",   "?")
            _web_source_cache.interactions_date_range = f"{ci_earliest} → {ci_latest}"
    else:
        _web_source_cache.interactions_count = 0

    _web_source_cache.expires_at = datetime.now() + _WEB_SOURCE_CACHE_TTL
    log.info(
        f"[web_source_cache] Refreshed: sessions={_web_source_cache.sessions_count}, "
        f"interactions={_web_source_cache.interactions_count}, "
        f"expires={_web_source_cache.expires_at.strftime('%H:%M:%S')}"
    )


# ================================================================
# TREND STATS PRE-COMPUTATION  (BUG-05 FIX)
# ================================================================

# ================================================================
# NUMERIC NORMALIZATION
# ================================================================

def _coerce_numeric(value) -> Optional[float]:
    """
    Convert DB/cache numeric values into floats for analytics helpers.

    asyncpg returns PostgreSQL NUMERIC aggregates as Decimal objects, and the
    query cache serializes unknown JSON values with default=str. The old code
    only accepted int/float, so Decimal and cached numeric strings were treated
    as non-numeric. That made chart_data None for most SUM/AVG/ROUND queries.
    """
    if value is None or isinstance(value, bool):
        return None

    if isinstance(value, (int, float, Decimal)):
        try:
            number = float(value)
        except (TypeError, ValueError, InvalidOperation, OverflowError):
            return None
        return number if math.isfinite(number) else None

    if isinstance(value, str):
        cleaned = value.strip().replace(",", "")
        if not cleaned:
            return None
        if cleaned.endswith("%"):
            cleaned = cleaned[:-1].strip()
        # Ignore obvious date-like/category strings; float() would reject most,
        # but this keeps labels such as "2024-01-01" from being considered.
        if any(sep in cleaned for sep in ("-", "/")) and not cleaned.replace(".", "", 1).lstrip("+-").isdigit():
            return None
        try:
            number = float(cleaned)
        except (TypeError, ValueError, OverflowError):
            return None
        return number if math.isfinite(number) else None

    return None

def _compute_trend_stats(rows: list[dict], time_col: str = "month") -> str:
    """
    BUG-05 FIX: Pre-compute trend statistics from SQL results before sending to MiniMax.
    Gives the LLM facts to narrate instead of raw numbers to compute.
    Eliminates hallucinated MoM percentages and wrong peak/trough identification.
    """
    if not rows or len(rows) < 2:
        return ""

    numeric_cols = [
        k for k, v in rows[0].items()
        if _coerce_numeric(v) is not None and k != time_col
    ]
    if not numeric_cols:
        return ""

    lines = [f"Pre-computed statistics ({len(rows)} periods):"]
    for col in numeric_cols:
        vals = [
            n for n in (_coerce_numeric(r.get(col)) for r in rows)
            if n is not None
        ]
        if not vals:
            continue
        mu = mean(vals)
        sd = stdev(vals) if len(vals) > 1 else 0
        idx_max = vals.index(max(vals))
        idx_min = vals.index(min(vals))
        period_max = rows[idx_max].get(time_col, idx_max)
        period_min = rows[idx_min].get(time_col, idx_min)
        last, prev = vals[-1], vals[-2]
        change_pct = ((last - prev) / abs(prev) * 100) if prev != 0 else 0
        slope_sign = "↑ increasing" if vals[-1] > vals[0] else "↓ decreasing"
        anomalies = [
            str(rows[i].get(time_col, i))
            for i, v in enumerate(vals)
            if abs(v - mu) > 1.5 * sd
        ]
        lines.append(
            f"  {col}: min={min(vals):.2f} ({period_min}), "
            f"max={max(vals):.2f} ({period_max}), "
            f"mean={mu:.2f}, last-vs-prev={change_pct:+.1f}%, "
            f"overall={slope_sign}"
            + (f", anomalous periods: {', '.join(anomalies)}" if anomalies else "")
        )
    return "\n".join(lines)


def _linear_forecast(
    rows: list[dict], time_col: str = "month", periods_ahead: int = 3
) -> dict[str, dict]:
    """
    Real forecast number — not LLM narration of history.

    Fits a simple least-squares line per numeric column over the observed
    monthly periods and extrapolates `periods_ahead` months beyond the last
    one. This is intentionally a plain linear trend (no seasonality model):
    it is cheap, deterministic, reproducible, and good enough to give an
    honest "if the current trend continues" answer instead of having the
    LLM free-associate a number from raw historical rows.

    Rate-like columns (pct/rate/margin) are averaged across the projected
    periods; cumulative columns (revenue, orders, etc.) are summed — e.g. a
    3-month-ahead "next quarter revenue" should be a quarter total, not one
    month's value.

    Returns {} when there's not enough history (<3 points) for a trend line.
    """
    if not rows or len(rows) < 3:
        return {}

    numeric_cols = [
        k for k, v in rows[0].items()
        if _coerce_numeric(v) is not None and k != time_col and not k.endswith("_label")
    ]
    if not numeric_cols:
        return {}

    n = len(rows)
    xs = list(range(n))
    x_mean = mean(xs)
    denom = sum((x - x_mean) ** 2 for x in xs)
    if denom == 0:
        return {}

    forecast: dict[str, dict] = {}
    for col in numeric_cols:
        vals = [_coerce_numeric(r.get(col)) for r in rows]
        if any(v is None for v in vals):
            continue
        y_mean = mean(vals)
        slope = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, vals)) / denom
        intercept = y_mean - slope * x_mean

        future_vals = [
            max(0.0, slope * (n + i) + intercept) for i in range(periods_ahead)
        ]
        is_rate = any(tok in col for tok in ("pct", "rate", "margin"))
        projected = mean(future_vals) if is_rate else sum(future_vals)

        # Decimals tied to the column's own scale (its historical mean), not
        # to each individual value — keeps "projected" and "trend_per_month"
        # consistently formatted for the same column. Fractional-scale
        # metrics like profit margin (~0.18) need 4 decimals or a real
        # -0.005/month trend rounds down to -0.01 (100% relative error).
        decimals = 4 if abs(y_mean) < 1 else 2
        forecast[col] = {
            "projected": round(projected, decimals),
            "trend_per_month": round(slope, decimals),
            "direction": "↑" if slope > 0 else "↓" if slope < 0 else "→",
            "decimals": decimals,
        }
    return forecast


def _format_forecast_block(forecast: dict, periods_ahead: int) -> str:
    """Render _linear_forecast() output as plain text for the LLM prompt."""
    if not forecast:
        return ""
    horizon = (
        "next quarter (sum of the next 3 months)" if periods_ahead == 3
        else "next year (sum of the next 12 months)" if periods_ahead == 12
        else "next month"
    )
    lines = [
        f"Statistical projection for {horizon}, based on linear-trend "
        f"extrapolation of the historical series above "
        f"(a simple statistical estimate, not a guarantee):"
    ]
    for col, info in forecast.items():
        d = info.get("decimals", 2)
        lines.append(
            f"  {col}: projected≈{info['projected']:,.{d}f} "
            f"(trend {info['direction']} {info['trend_per_month']:+,.{d}f}/month)"
        )
    return "\n".join(lines)

# Keywords that hint the query is about negative/complaint reviews
_COMPLAINT_KEYWORDS = (
    "negative", "سلبي", "شكوى", "يشتكي", "complaint", "complaints",
    "problem", "مشكلة", "مشاكل", "غير مطابق", "mismatch", "not as described",
    "refund", "return", "إرجاع", "استرداد", "غير راضي", "unhappy",
)


# ================================================================
# CHART DATA EXTRACTION
# ================================================================

_TIME_KEYWORDS = {
    "month", "week", "day", "date", "year", "quarter", "period",
    "شهر", "يوم", "تاريخ", "فترة", "أسبوع"
}

_PRIORITY_VALUE_COLS = [
    "total_revenue", "leakage_revenue", "revenue", "amount", "count",
    "orders", "leakage_orders", "leakage_rate_pct", "rate", "avg_value",
    "sessions", "conversions", "conversion_rate",
]

_CHART_COLORS = [
    {"border": "#1D6FFF", "bg": "rgba(29, 111, 255, 0.15)"},
    {"border": "#14B86A", "bg": "rgba(20, 184, 106, 0.15)"},
    {"border": "#F59E0B", "bg": "rgba(245, 158, 11, 0.15)"},
    {"border": "#EF4444", "bg": "rgba(239, 68, 68, 0.15)"},
    {"border": "#06B6D4", "bg": "rgba(6, 182, 212, 0.15)"},
]


def _humanize_rank_label(value: object) -> str:
    """Readable label for deterministic dual-ranking rows."""
    text = str(value or "").strip()
    mapping = {
        "highest_revenue": "Highest Revenue",
        "highest_leakage_revenue": "Highest Leakage Revenue",
        "highest_leakage_rate": "Highest Leakage Rate",
        "highest_leakage_orders": "Highest Leakage Orders",
        "highest_revenue_at_risk": "Highest Revenue At Risk",
    }
    return mapping.get(text, text.replace("_", " ").title())


def _extract_chart_data(rows: list[dict], plan: dict) -> Optional[dict]:
    """
    Auto-detect the best visualization from SQL result rows.

    Important fix for dual-ranking queries:
      - Do NOT use ranking_type as the chart label.
      - Do NOT plot money, rates, and counts on the same axis.
      - Use the real business dimension (region/city/seller/etc.) as labels.
      - For dual ranking, plot one comparable value per row.

    The full result table still contains every metric; chart_data should be a
    readable visual summary, not a dump of every numeric column.
    """
    if not rows or len(rows) < 2:
        return None

    cols = list(rows[0].keys())
    lower_cols = {c.lower(): c for c in cols}
    intent = plan.get("intent", "") if isinstance(plan, dict) else ""

    # ------------------------------------------------------------------
    # Special handling: deterministic dual ranking result shape
    # ------------------------------------------------------------------
    # Expected columns:
    # ranking_type, rank, region, total_revenue, total_orders,
    # leakage_orders, leakage_revenue, leakage_rate_pct
    if "ranking_type" in lower_cols:
        ranking_col = lower_cols["ranking_type"]
        label_candidates = (
            "region", "customer_city", "city", "seller_name", "seller_id",
            "scenario", "product_category", "campaign_name", "carrier",
            "payment_status", "payment_type"
        )
        label_col = next((lower_cols[c] for c in label_candidates if c in lower_cols), None)
        if label_col:
            total_rev_col = lower_cols.get("total_revenue") or lower_cols.get("revenue_at_risk")
            leak_rev_col = lower_cols.get("leakage_revenue") or lower_cols.get("revenue_at_risk")
            leak_rate_col = lower_cols.get("leakage_rate_pct")
            leak_orders_col = lower_cols.get("leakage_orders") or lower_cols.get("total_orders")

            labels: list[str] = []
            values: list[float | None] = []
            display_rows = rows[:20]
            metric_label = "Ranked Value"

            for r in display_rows:
                ranking_type = str(r.get(ranking_col) or "")
                label = str(r.get(label_col) or "Unknown")

                # Keep units comparable per row's rank family. The UI shows this
                # as one scalar visual summary; the table still carries all metrics.
                if "revenue" in ranking_type and total_rev_col:
                    metric_label = "Revenue / Leakage Value (EGP)"
                    v = _coerce_numeric(r.get(total_rev_col if ranking_type == "highest_revenue" else leak_rev_col))
                elif "rate" in ranking_type and leak_rate_col:
                    metric_label = "Leakage Rate (%)"
                    v = _coerce_numeric(r.get(leak_rate_col))
                elif "orders" in ranking_type and leak_orders_col:
                    metric_label = "Orders / Cases"
                    v = _coerce_numeric(r.get(leak_orders_col))
                elif leak_rev_col:
                    metric_label = "Revenue / Leakage Value (EGP)"
                    v = _coerce_numeric(r.get(leak_rev_col))
                else:
                    v = None

                labels.append(f"{label} · {_humanize_rank_label(ranking_type)}")
                values.append(round(v, 2) if v is not None else None)

            return {
                "type": "bar",
                "title": "Dual Ranking",
                "labels": labels,
                "datasets": [
                    {
                        "label": metric_label,
                        "data": values,
                        "borderColor": _CHART_COLORS[0]["border"],
                        "backgroundColor": _CHART_COLORS[0]["bg"],
                        "borderWidth": 1.5,
                    }
                ],
                "label_col": label_col,
                "value_cols": ["ranked_value"],
                "chart_note": "Dual-ranking chart uses one ranked metric per row. Full table retains all metrics.",
            }

    # ------------------------------------------------------------------
    # Planner chart policy: deterministic templates can specify a coherent
    # label/metric family so the frontend does not mix money, rates, and counts.
    # ------------------------------------------------------------------
    chart_policy = plan.get("chart_policy", {}) if isinstance(plan, dict) else {}
    if isinstance(chart_policy, dict) and chart_policy:
        policy_type = chart_policy.get("type")
        if policy_type in {"none", "table_only", "kpi"}:
            return None

        requested_label = chart_policy.get("label")
        requested_metrics = chart_policy.get("metrics") or []

        alias_map = {
            "region": ["region", "customer_city", "city"],
            "month": ["month_label", "month"],
            "seller": ["seller_name", "seller_id"],
        }
        label_candidates = alias_map.get(requested_label, [requested_label] if requested_label else [])
        policy_label_col = next((lower_cols[c.lower()] for c in label_candidates if c and c.lower() in lower_cols), None)
        if policy_label_col:
            metric_cols = [lower_cols[m.lower()] for m in requested_metrics if isinstance(m, str) and m.lower() in lower_cols]
            metric_cols = [m for m in metric_cols if any(_coerce_numeric(r.get(m)) is not None for r in rows[:8])]
            if metric_cols:
                display_rows = rows[:20]
                labels = [str(r.get(policy_label_col, "")) for r in display_rows]
                chart_type = policy_type if policy_type in {"bar", "line", "doughnut"} else ("line" if any(k in policy_label_col.lower() for k in _TIME_KEYWORDS) else "bar")
                datasets = []
                for i, vcol in enumerate(metric_cols[:2]):
                    color = _CHART_COLORS[i % len(_CHART_COLORS)]
                    datasets.append({
                        "label": vcol.replace("_", " ").title(),
                        "data": [round(_coerce_numeric(r.get(vcol)) or 0, 2) for r in display_rows],
                        "borderColor": color["border"],
                        "backgroundColor": color["bg"],
                        "borderWidth": 1.5,
                    })
                return {
                    "type": chart_type,
                    "title": chart_policy.get("title") or "Query Results",
                    "labels": labels,
                    "datasets": datasets,
                    "label_col": policy_label_col,
                    "value_cols": metric_cols[:2],
                    "chart_policy_applied": True,
                }

    # --- find label column (categorical/temporal) ---
    label_col = None
    is_time = False

    # Prefer business dimensions over technical/helper columns.
    label_priority = [
        "region", "customer_city", "city", "seller_name", "seller_id",
        "product_category", "campaign_name", "channel", "scenario",
        "leakage_scenario", "carrier", "payment_type", "month_label", "month",
        "quarter", "year",
    ]
    non_label_cols = {
        "ranking_type", "rank", "rn", "row_number", "id", "order_id",
        "customer_id", "product_id", "campaign_id", "mql_id", "session_id",
    }

    for name in label_priority:
        if name in lower_cols:
            label_col = lower_cols[name]
            cname = name
            is_time = any(kw in cname for kw in _TIME_KEYWORDS)
            break

    if label_col is None:
        for c in cols:
            cname = c.lower()
            if any(kw in cname for kw in _TIME_KEYWORDS):
                label_col = c
                is_time = True
                break

    if label_col is None:
        # Fall back to first non-numeric column, excluding helper columns like ranking_type.
        for c in cols:
            cname = c.lower()
            if cname in non_label_cols:
                continue
            sample = rows[0].get(c)
            if _coerce_numeric(sample) is None:
                label_col = c
                break
    if label_col is None:
        return None

    # --- find numeric value columns ---
    def _is_numeric(col: str) -> bool:
        for row in rows[:8]:
            if _coerce_numeric(row.get(col)) is not None:
                return True
        return False

    # Avoid plotting helper/order columns.
    metric_blacklist = {"rank", "rn", "row_number", "id", "order_year"}

    priority_present = [c for c in _PRIORITY_VALUE_COLS if c in cols and c.lower() not in metric_blacklist and _is_numeric(c)]

    # Avoid mixing incompatible units on one axis. Pick a coherent family.
    money_cols = [c for c in priority_present if any(k in c.lower() for k in ("revenue", "profit", "amount", "value", "cost", "budget"))]
    rate_cols = [c for c in priority_present if any(k in c.lower() for k in ("rate", "pct", "margin"))]
    count_cols = [c for c in priority_present if any(k in c.lower() for k in ("count", "orders", "sessions", "conversions"))]

    # For ordinary charts, prefer the most business-relevant comparable family.
    if money_cols:
        value_cols = money_cols[:2]
    elif rate_cols:
        value_cols = rate_cols[:2]
    elif count_cols:
        value_cols = count_cols[:2]
    else:
        value_cols = []

    # Fill from remaining numeric cols only if we still have no metric.
    if not value_cols:
        for c in cols:
            if c == label_col or c.lower() in metric_blacklist or c.lower() in non_label_cols:
                continue
            if _is_numeric(c):
                value_cols.append(c)
            if len(value_cols) >= 2:
                break

    if not value_cols:
        return None

    # --- cap rows shown in chart ---
    display_rows = rows[:20]
    labels = [str(r.get(label_col, "")) for r in display_rows]

    # --- pick chart type ---
    n_categories = len(display_rows)
    if is_time:
        chart_type = "line"
    elif n_categories <= 6 and len(value_cols) == 1:
        chart_type = "doughnut"
    else:
        chart_type = "bar"

    # --- build datasets ---
    datasets = []
    for i, vcol in enumerate(value_cols):
        color = _CHART_COLORS[i % len(_CHART_COLORS)]
        data_points = []
        for r in display_rows:
            v = _coerce_numeric(r.get(vcol))
            data_points.append(round(v, 2) if v is not None else None)

        ds = {
            "label": vcol.replace("_", " ").title(),
            "data": data_points,
            "borderColor": color["border"],
            "backgroundColor": color["bg"] if chart_type == "line" else [
                _CHART_COLORS[j % len(_CHART_COLORS)]["border"]
                for j in range(len(data_points))
            ] if chart_type == "doughnut" else color["bg"],
        }
        if chart_type == "line":
            ds["tension"] = 0.35
            ds["fill"] = True
            ds["pointRadius"] = 3
        elif chart_type == "bar":
            ds["borderWidth"] = 1.5
        datasets.append(ds)

    title_map = {
        "trend_analysis": "Revenue Trend",
        "aggregation": "Data Summary",
        "leakage_detection": "Leakage Analysis",
        "web_analytics": "Web Analytics",
        "anomaly_detection": "Anomaly Overview",
        "dual_ranking": "Dual Ranking",
    }
    title = title_map.get(intent, "Query Results")

    return {
        "type": chart_type,
        "title": title,
        "labels": labels,
        "datasets": datasets,
        "label_col": label_col,
        "value_cols": value_cols,
    }



# ================================================================
# PIPELINE RESULT
# ================================================================

@dataclass
class PipelineResult:
    """Complete result from the multi-agent pipeline."""
    answer: str = ""
    sql_used: Optional[str] = None
    rows: list[dict] = field(default_factory=list)
    row_count: int = 0
    execution_ms: int = 0
    sql_error: Optional[str] = None
    plan: dict = field(default_factory=dict)
    steps: list[str] = field(default_factory=list)
    intent: str = ""
    route: str = ""
    difficulty: str = ""
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    rag_retrieved: int = 0
    rag_cached: bool = False
    validation_issues: list[str] = field(default_factory=list)
    confidence: float = 0.0
    chart_data: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "answer":           self.answer,
            "sql_used":         self.sql_used,
            "row_count":        self.row_count,
            "execution_ms":     self.execution_ms,
            "sql_error":        self.sql_error,
            "steps":            self.steps,
            "intent":           self.intent,
            "route":            self.route,
            "difficulty":       self.difficulty,
            "total_tokens_in":  self.total_tokens_in,
            "total_tokens_out": self.total_tokens_out,
            "rag_retrieved":    self.rag_retrieved,
            "rag_cached":       self.rag_cached,
            "validation_issues":self.validation_issues,
            "confidence":       self.confidence,
            "chart_data":       self.chart_data,
        }


# ================================================================
# ORCHESTRATOR
# ================================================================

class Orchestrator:
    """Coordinates the multi-agent pipeline."""

    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    # ------------------------------------------------------------------
    # HELPERS
    # ------------------------------------------------------------------

    @staticmethod
    def _build_review_fallback_context(
        rag_context: RAGContext,
        user_message: str,
        limit: int = _FALLBACK_REVIEW_LIMIT,
    ) -> str:
        """
        Format rag_context.review_chunks into a structured analytics prompt block.
        Used when SQL returns 0 rows for review/sentiment intents so the
        analytics agent receives the full set of semantically retrieved reviews
        instead of the (often empty) compressed context.
        """
        chunks = rag_context.review_chunks[:limit]
        if not chunks:
            return ""

        total = rag_context.retrieved_count
        lines = [
            f"## Customer Reviews Retrieved via Semantic Search "
            f"(showing {len(chunks)} of {total} total):\n"
        ]
        for i, chunk in enumerate(chunks, 1):
            content = (chunk.get("content") or "").strip()
            if not content:
                continue
            title = chunk.get("title", f"Review #{i}")
            score = chunk.get("score", 0.0)
            lines.append(
                f"[{i}] **{title}** (relevance={score:.3f})\n"
                f"{content[:_FALLBACK_REVIEW_CHARS]}"
            )

        return "\n\n".join(lines) if len(lines) > 1 else ""

    async def _web_analytics_adaptive_retry(self) -> tuple[list[dict], str, str]:
        """
        ENHANCED ADAPTIVE RETRY — 3-stage cascade fallback.
        Returns (rows, diagnostic_note, actual_sql_used).

        BUG-11 FIX: Source availability is cached for 1 hour — the 2 diagnostic
        COUNT queries now run in parallel at cache-refresh time, not serially per request.
        Saves 2-4 DB round-trips on every web analytics retry call.

        BUG-NEW-04 FIX: Returns the SQL that actually ran so the orchestrator can
        set result.sql_used accurately for audit logging.
        """
        global _web_source_cache

        # Refresh cache if expired (runs both diag queries in parallel)
        if datetime.now() >= _web_source_cache.expires_at:
            await _refresh_web_source_cache(self.pool)

        sessions_total = _web_source_cache.sessions_count
        diagnostic_note = ""

        # ── STAGE 1: website_sessions ───────────────────────────────────────────
        if sessions_total > 0:
            date_range = _web_source_cache.sessions_date_range
            diagnostic_note = (
                f"[سياق الفترة الزمنية]: البيانات المعروضة تغطي الفترة {date_range} "
                f"— وهي كامل البيانات المتاحة في قاعدة البيانات ({sessions_total:,} جلسة). "
                f"قدِّم التحليل على أساس هذه الفترة كمرجع أساسي وحقيقي للأداء."
            )
            log.info(f"[orchestrator] web_analytics: {sessions_total} sessions (cached), removing time filter")
            rows, _, err, _ = await execute_sql(
                pool=self.pool,
                sql=_WEB_ANALYTICS_ALLTIME_SQL,
                use_cache=True,
            )
            if not err and rows:
                log.info(f"[orchestrator] web_analytics stage1: {len(rows)} rows from website_sessions")
                return rows, diagnostic_note, _WEB_ANALYTICS_ALLTIME_SQL.strip()
            else:
                log.warning("[orchestrator] web_analytics: website_sessions count>0 but query returned nothing")

        # ── STAGE 2: customer_interactions ─────────────────────────────────────
        ci_total = _web_source_cache.interactions_count
        if sessions_total <= 0 or True:  # always check ci if sessions failed
            if ci_total > 0:
                ci_date_range = _web_source_cache.interactions_date_range
                diagnostic_note = (
                    f"[سياق البيانات]: تعذّر العثور على بيانات جلسات الموقع المباشرة. "
                    f"يعرض التحليل التالي بيانات تفاعل العملاء عبر قناة الويب "
                    f"({ci_total:,} حدث، {ci_date_range}) كبديل دقيق."
                )
                log.info("[orchestrator] web_analytics stage2: trying customer_interactions")
                rows, _, err, _ = await execute_sql(
                    pool=self.pool,
                    sql=_WEB_ANALYTICS_INTERACTIONS_SQL,
                    use_cache=True,
                )
                if not err and rows:
                    log.info(f"[orchestrator] web_analytics stage2: {len(rows)} rows from customer_interactions")
                    return rows, diagnostic_note, _WEB_ANALYTICS_INTERACTIONS_SQL.strip()
            else:
                log.warning("[orchestrator] web_analytics: customer_interactions also empty — trying stage 3")

        # ── STAGE 3: campaign_attribution ──────────────────────────────────────
        log.info("[orchestrator] web_analytics stage3: trying campaign_attribution")
        rows, _, err, _ = await execute_sql(
            pool=self.pool,
            sql=_WEB_ANALYTICS_CAMPAIGN_SQL,
            use_cache=True,
        )
        if not err and rows:
            diagnostic_note = (
                "[سياق البيانات]: لا تتوفر بيانات جلسات ويب مباشرة. "
                "يعرض التحليل التالي تأثير قنوات التسويق على الإيرادات والأرباح "
                "من خلال جدول نسب الحملات الإعلانية."
            )
            log.info(f"[orchestrator] web_analytics stage3: {len(rows)} rows from campaign_attribution")
            return rows, diagnostic_note, _WEB_ANALYTICS_CAMPAIGN_SQL.strip()

        # ── STAGE 4: all sources empty ──────────────────────────────────────────
        log.warning("[orchestrator] web_analytics: all data sources are empty")
        empty_note = (
            "جميع مصادر بيانات التحليل الرقمي فارغة في قاعدة البيانات الحالية "
            "(website_sessions، customer_interactions، campaign_attribution). "
            "يُرجى التحقق من تشغيل مسارات ETL لهذه الجداول وإعادة المحاولة."
        )
        return [], empty_note, ""

    @staticmethod
    def _sentiment_aware_sort(user_message: str) -> str:
        """
        IMPROVEMENT: Returns the correct ORDER BY clause for the RAG-only
        direct DB fetch based on the query's apparent sentiment direction.

        Previously always used `rating DESC` which returned positive (5-star)
        reviews first — wrong for complaint/negative queries.

        Now:
          - Complaint/negative keywords  → negative sentiment first (rating ASC)
          - Positive/praise keywords     → positive sentiment first (rating DESC)
          - No clear signal              → balanced: negative then mixed then positive
        """
        lower = user_message.lower()
        if any(k in lower for k in _COMPLAINT_KEYWORDS):
            # Negative first: low rating, then by date
            return (
                "ORDER BY "
                "CASE sentiment WHEN 'negative' THEN 1 WHEN 'mixed' THEN 2 WHEN 'neutral' THEN 3 ELSE 4 END ASC, "
                "rating ASC, "
                "review_date DESC"
            )
        if any(k in lower for k in ("positive", "إيجابي", "يثنوا", "happy", "راضي", "satisfied")):
            return (
                "ORDER BY "
                "CASE sentiment WHEN 'positive' THEN 1 WHEN 'mixed' THEN 2 WHEN 'neutral' THEN 3 ELSE 4 END ASC, "
                "rating DESC, "
                "review_date DESC"
            )
        # Balanced: negative/mixed prioritised for leakage platform context
        return (
            "ORDER BY "
            "CASE sentiment WHEN 'negative' THEN 1 WHEN 'mixed' THEN 2 WHEN 'neutral' THEN 3 ELSE 4 END ASC, "
            "review_date DESC"
        )

    # ------------------------------------------------------------------
    # MAIN PIPELINE
    # ------------------------------------------------------------------

    async def process(self, session_id: str, user_message: str) -> PipelineResult:
        """Process a user message through the full pipeline."""
        result = PipelineResult()
        result.steps.append(f"1. Received: {user_message[:80]}")

        # --- STEP 1: Load session memory ---
        mem            = get_memory(session_id)
        memory_context = mem.build_context_prompt()
        is_followup    = mem.is_followup(user_message)
        result.steps.append(f"2. Memory loaded (followup={is_followup})")

        # --- STEP 2: Planner Agent ---
        try:
            compressed_schema_hint = ""
            try:
                schema_ctx = await load_schema_context()
                mv_hints   = schema_ctx.get("mv_hints", {})
                compressed_schema_hint = "\n".join(
                    f"- {k}: {v}" for k, v in list(mv_hints.items())[:4]
                )
            except Exception:
                pass

            plan, in_tok, out_tok = await plan_query(
                user_message=user_message,
                memory_context=memory_context,
                compressed_schema=compressed_schema_hint,
            )
            result.total_tokens_in  += in_tok
            result.total_tokens_out += out_tok
            result.plan       = plan.to_dict()
            result.intent     = plan.intent
            result.route      = plan.route
            result.difficulty = plan.difficulty
            result.steps.append(
                f"3. Planner: intent={plan.intent}, route={plan.route}, "
                f"difficulty={plan.difficulty}"
            )

        except Exception as e:
            log.error(f"[orchestrator] Planner failed: {e}", exc_info=True)
            plan = QueryPlan(
                intent="general_query",
                route="hybrid",
                needs_sql=True,
                needs_rag=True,
                target_source="ml_output.mv_leakage_dashboard",
            )
            result.steps.append(f"3. Planner fallback: {e}")

        # Apply memory context
        if is_followup:
            plan_dict = memory_store.apply_memory_to_plan(
                session_id, plan.to_dict(), user_message
            )
            plan.target_source  = plan_dict.get("target_source",  plan.target_source)
            plan.tables_needed  = plan_dict.get("tables_needed",  plan.tables_needed)
            plan.filters        = plan_dict.get("filters",        plan.filters)

        # --- STEP 3: RAG Retrieval ---
        rag_context = RAGContext()
        if plan.needs_rag:
            try:
                rag_start        = time.time()
                retrieval_intent = plan.intent or "simple_lookup"
                if retrieval_intent in _REVIEW_INTENTS:
                    log.info(
                        f"[orchestrator] Review intent — using review_embeddings "
                        f"for '{retrieval_intent}'"
                    )
                rag_context = await retrieve_context(
                    pool=self.pool,
                    query_text=user_message,
                    intent=retrieval_intent,
                    top_k=8,
                )
                rag_ms             = int((time.time() - rag_start) * 1000)
                result.rag_retrieved = rag_context.retrieved_count
                result.rag_cached    = rag_context.from_cache
                result.steps.append(
                    f"4. RAG: {rag_context.retrieved_count} docs retrieved ({rag_ms}ms)"
                )
            except Exception as e:
                log.warning(f"[orchestrator] RAG retrieval failed: {e}")
                result.steps.append(f"4. RAG failed: {e}")

        # --- STEP 4: Context Compression ---
        compressed = CompressedContext()
        try:
            compressed = compress_context(
                plan=plan.to_dict(),
                rag_docs=[
                    *[{**d, "source_type": "schema_doc"}   for d in rag_context.schema_docs],
                    *[{**d, "source_type": "business_rule"} for d in rag_context.business_rules],
                    *[{**d, "source_type": "sql_template"}  for d in rag_context.sql_templates],
                    *[{**d, "source_type": "kpi_glossary"}  for d in rag_context.kpi_definitions],
                    *[{**d, "source_type": "review"}        for d in rag_context.review_chunks],
                ],
                intent=plan.intent,
                user_message=user_message,
            )
            result.steps.append(f"5. Context compressed: ~{compressed.token_estimate} tokens")
        except Exception as e:
            log.warning(f"[orchestrator] Context compression failed: {e}")
            result.steps.append(f"5. Context compression skipped: {e}")

        # ============================================================
        # RAG-ONLY ROUTING
        # ============================================================
        if plan.route == "rag_only":
            try:
                async with self.pool.acquire() as conn:
                    # IMPROVEMENT: sentiment-aware sort order
                    sort_clause = self._sentiment_aware_sort(user_message)

                    msg_lower = user_message.lower()
                    if "positive" in msg_lower or "إيجابي" in msg_lower or "يثنوا" in msg_lower:
                        sentiment_filter: Optional[str] = "positive"
                    elif any(k in msg_lower for k in ("negative", "سلبي", "يشتكي", "شكوى")):
                        sentiment_filter = "negative"
                    else:
                        sentiment_filter = None

                    if sentiment_filter:
                        rows = await conn.fetch(
                            f"""
                            SELECT review_comment, rating, sentiment, order_id
                            FROM ecommerce.reviews
                            WHERE sentiment = $1
                              AND review_comment IS NOT NULL
                              AND length(review_comment) > 20
                            {sort_clause}
                            LIMIT 500
                            """,
                            sentiment_filter,
                        )
                    else:
                        rows = await conn.fetch(
                            f"""
                            SELECT review_comment, rating, sentiment, order_id
                            FROM ecommerce.reviews
                            WHERE review_comment IS NOT NULL
                              AND length(review_comment) > 20
                            {sort_clause}
                            LIMIT 500
                            """
                        )

                    review_count = len(rows)
                    log.info(
                        f"[orchestrator] RAG-only: {review_count} reviews "
                        f"(sentiment_filter={sentiment_filter!r}, "
                        f"sort_hint={'complaint' if any(k in msg_lower for k in _COMPLAINT_KEYWORDS) else 'balanced'})"
                    )

                    if review_count == 0:
                        review_context = "No reviews with substantial text found in the database."
                    else:
                        sample = rows[:200]
                        review_texts = []
                        for r in sample:
                            icon = (
                                "👍" if r["sentiment"] == "positive"
                                else "👎" if r["sentiment"] == "negative"
                                else "😐"
                            )
                            review_texts.append(
                                f"{icon} Rating: {r['rating']} — {r['review_comment'][:300]}"
                            )
                        review_context = (
                            f"## Customer Reviews "
                            f"(showing {len(sample)} of {review_count} total, "
                            f"sorted by relevance to query):\n\n"
                            + "\n\n".join(review_texts)
                        )

                    # Prefer semantic RAG results when richer
                    rag_fallback = self._build_review_fallback_context(rag_context, user_message)
                    if rag_fallback and len(rag_fallback) > len(review_context):
                        review_context = rag_fallback
                        log.info(
                            "[orchestrator] RAG-only: semantic search results richer than "
                            "DB fetch — using semantic context"
                        )

                    direct_compressed = CompressedContext(
                        review_insights=review_context,
                        token_estimate=len(review_context) // 4,
                    )

                    answer, in_tok, out_tok = await interpret_direct(
                        user_message=user_message,
                        plan=plan.to_dict(),
                        compressed_context=direct_compressed.to_analytics_prompt(),
                    )
                    result.total_tokens_in  += in_tok
                    result.total_tokens_out += out_tok
                    result.answer = answer
                    result.steps.append(
                        f"6. RAG-only: {review_count} DB reviews + "
                        f"{len(rag_context.review_chunks)} semantic reviews → interpreted"
                    )
                    await self._persist(session_id, user_message, result)
                    return result

            except Exception as e:
                log.error(f"[orchestrator] RAG-only fetch failed: {e}", exc_info=True)
                result.answer = "حدث خطأ أثناء جلب المراجعات. يرجى المحاولة مرة أخرى."
                result.steps.append(f"6. RAG-only error: {e}")
                await self._persist(session_id, user_message, result)
                return result

        # ============================================================
        # NON-DATABASE ROUTING
        # ============================================================
        if plan.route == "non_database":
            answer, in_tok, out_tok = await interpret_direct(
                user_message=user_message,
                plan=plan.to_dict(),
                compressed_context=compressed.to_analytics_prompt(),
            )
            result.total_tokens_in  += in_tok
            result.total_tokens_out += out_tok
            result.answer = answer
            result.steps.append("6. Direct answer (non_database)")
            await self._persist(session_id, user_message, result)
            return result

        # --- STEP 5: SQL Generation ---
        sql = None
        if plan.needs_sql:
            try:
                # BUG-02 FIX: sql_examples removed — they already live inside compressed_context
                # via _compress_sql_examples(). Sending them separately caused the model to see
                # identical examples twice (500-1000 chars apart), degrading output quality.
                # BUG-03 FIX: use to_sql_context() not to_sql_writer_prompt() — excludes
                # review_insights which caused ILIKE regressions in GLM-4.5.
                sql, in_tok, out_tok = await generate_sql(
                    user_message=user_message,
                    plan=plan.to_dict(),
                    compressed_context=compressed.to_sql_context(),
                )
                result.total_tokens_in  += in_tok
                result.total_tokens_out += out_tok
                result.sql_used  = sql
                result.steps.append(f"6. SQL generated: {bool(sql)}")

            except Exception as e:
                log.error(f"[orchestrator] SQL generation failed: {e}")
                result.sql_error = str(e)
                result.steps.append(f"6. SQL generation failed: {e}")

        # --- STEP 6: SQL Validation ---
        if sql and plan.needs_sql:
            try:
                # BUG-10 FIX: Pass plan so LLM validation is skipped for simple queries
                val_result = await validate_sql(sql, plan=plan.to_dict())
                result.validation_issues = val_result.issues + val_result.warnings
                result.confidence        = val_result.confidence
                result.steps.append(
                    f"7. Validation: ok={val_result.ok}, fatal={val_result.fatal}, "
                    f"issues={len(val_result.issues)}"
                )

                # Validator optimizer: swap in LLM-rewritten SQL when available
                if not val_result.fatal and val_result.optimized_sql:
                    log.info("[orchestrator] Using validator-optimized SQL")
                    sql             = val_result.optimized_sql
                    result.sql_used = sql
                    result.steps.append("7b. SQL optimized by validator")

                if val_result.fatal:
                    result.sql_error = f"Validation failed: {'; '.join(val_result.issues)}"
                    try:
                        # BUG-03 FIX: to_sql_context() (no review text)
                        # BUG-04 FIX: pass plan so fix targets correct table/metrics
                        fixed_sql, in_tok, out_tok = await fix_sql(
                            original_sql=sql,
                            error_message="",
                            validation_issues=val_result.issues,
                            compressed_context=compressed.to_sql_context(),
                            plan=plan.to_dict(),
                        )
                        result.total_tokens_in  += in_tok
                        result.total_tokens_out += out_tok
                        if fixed_sql:
                            val2 = await validate_sql(fixed_sql, plan=plan.to_dict())
                            if not val2.fatal:
                                sql              = fixed_sql
                                result.sql_used  = sql
                                result.validation_issues = val2.issues
                                result.steps.append("7c. SQL fixed and re-validated")
                            else:
                                result.steps.append("7c. Fix failed re-validation")
                    except Exception as fix_e:
                        result.steps.append(f"7c. Fix attempt failed: {fix_e}")

            except Exception as e:
                log.warning(f"[orchestrator] Validation error: {e}")
                result.steps.append(f"7. Validation error: {e}")

        # --- STEP 7: SQL Execution ---
        rows    = []
        exec_ms = 0
        sql_error       = None
        was_truncated   = False

        if sql and plan.needs_sql and not result.sql_error:
            try:
                rows, exec_ms, sql_error, was_truncated = await execute_sql(
                    pool=self.pool,
                    sql=sql,
                    use_cache=True,
                )
                result.rows        = rows
                result.row_count   = len(rows)
                result.execution_ms = exec_ms
                result.sql_error   = sql_error
                result.steps.append(f"8. Executed: {len(rows)} rows in {exec_ms}ms")

                if sql_error:
                    try:
                        # BUG-03 FIX: to_sql_context(); BUG-04 FIX: plan passed
                        fixed_sql, in_tok, out_tok = await fix_sql(
                            original_sql=sql,
                            error_message=sql_error,
                            validation_issues=[],
                            compressed_context=compressed.to_sql_context(),
                            plan=plan.to_dict(),
                        )
                        result.total_tokens_in  += in_tok
                        result.total_tokens_out += out_tok
                        if fixed_sql:
                            rows2, exec_ms2, err2, _ = await execute_sql(
                                pool=self.pool, sql=fixed_sql, use_cache=False,
                            )
                            if not err2:
                                result.sql_used    = fixed_sql
                                result.rows        = rows2
                                result.row_count   = len(rows2)
                                result.execution_ms = exec_ms2
                                result.sql_error   = None
                                result.steps.append(f"8b. Fixed SQL: {len(rows2)} rows")
                    except Exception as fix_e:
                        result.steps.append(f"8b. Execution fix failed: {fix_e}")

            except Exception as e:
                result.sql_error = str(e)
                result.steps.append(f"8. Execution error: {e}")

        # --- STEP 8: Analytics Interpretation ---
        try:
            if plan.needs_sql and result.rows:
                # BUG-05 FIX: Pre-compute trend statistics for numerical intents so
                # MiniMax M2.5 narrates facts instead of computing raw numbers itself.
                augmented_question = user_message
                if plan.intent in ("trend_analysis", "aggregation", "web_analytics") and result.rows:
                    trend_stats = _compute_trend_stats(result.rows)
                    if trend_stats:
                        augmented_question = f"{user_message}\n\n[Pre-computed statistics]\n{trend_stats}"

                # FORECAST FIX: "forecast/predict next quarter…" questions were
                # only ever given historical descriptive stats above, so the
                # LLM had nothing forward-looking to narrate and just restated
                # the past. Compute an actual linear-trend projection in code
                # (deterministic, free, no extra LLM call) and hand the LLM a
                # real number to explain instead. Only fires for genuinely
                # forecast-flavored trend questions — plain "show me the
                # monthly trend" questions are untouched.
                if plan.intent == "trend_analysis" and is_forecast_question(user_message):
                    horizon = forecast_horizon_periods(user_message)
                    forecast = _linear_forecast(result.rows, periods_ahead=horizon)
                    forecast_block = _format_forecast_block(forecast, horizon)
                    if forecast_block:
                        augmented_question = f"{augmented_question}\n\n[{forecast_block}]"
                        result.steps.append(
                            f"8b. Forecast: linear projection computed ({horizon}-month horizon)"
                        )

                # BUG-NEW-03 FIX: pass intent so web_analytics doesn't get leakage rules
                answer, in_tok, out_tok = await interpret_results(
                    user_message=augmented_question,
                    sql_results=result.rows,
                    plan=plan.to_dict(),
                    compressed_context=compressed.to_analytics_prompt(intent=plan.intent),
                    row_count=result.row_count,
                    execution_ms=result.execution_ms,
                )
                result.steps.append("9. Analytics: SQL results interpreted")

            elif plan.needs_sql and not result.rows and plan.intent in _REVIEW_INTENTS:
                # SQL returned 0 rows for a review intent — use RAG review chunks
                rag_fallback = self._build_review_fallback_context(rag_context, user_message)
                if rag_fallback:
                    fallback_ctx = rag_fallback
                    log.info(
                        f"[orchestrator] SQL=0 rows for '{plan.intent}' — "
                        f"falling back to {len(rag_context.review_chunks)} RAG review chunks"
                    )
                    result.steps.append(
                        f"9. SQL=0 fallback: {len(rag_context.review_chunks)} RAG reviews"
                    )
                else:
                    fallback_ctx = compressed.to_analytics_prompt(intent=plan.intent)
                    log.warning("[orchestrator] SQL=0 and no RAG reviews — using compressed context")
                    result.steps.append("9. SQL=0 fallback: compressed context (no RAG reviews)")

                answer, in_tok, out_tok = await interpret_direct(
                    user_message=user_message,
                    plan=plan.to_dict(),
                    compressed_context=fallback_ctx,
                )
                result.steps.append("9. Analytics: review fallback interpreted")

            elif plan.needs_sql and not result.rows and plan.intent in _WEB_ANALYTICS_INTENTS:
                # ── BUG A+B FIX ───────────────────────────────────────────────────────
                # web_analytics returned 0 rows.  Most likely cause: the 12-month time
                # filter excludes all available data (data is historical, e.g. 2023-2024
                # but current year is 2026).  Adaptive retry: remove the time filter and
                # re-run, then pass real rows to interpret_results instead of the empty
                # interpret_direct path that was producing generic "no data" answers.
                log.info("[orchestrator] web_analytics SQL=0 rows — starting adaptive retry")
                result.steps.append("9a. web_analytics: 0 rows — running adaptive retry")

                adaptive_rows, diagnostic_note, actual_sql = await self._web_analytics_adaptive_retry()

                if adaptive_rows:
                    result.rows      = adaptive_rows
                    result.row_count = len(adaptive_rows)
                    # BUG-NEW-04 FIX: use the SQL that actually produced the rows, not
                    # always the all-time sessions SQL (which may not have run at all).
                    result.sql_used  = actual_sql

                    analysis_question = user_message
                    if diagnostic_note:
                        analysis_question = f"{user_message}\n\n{diagnostic_note}"

                    # BUG-05: pre-compute trend stats for web analytics retry rows too
                    trend_stats = _compute_trend_stats(adaptive_rows)
                    if trend_stats:
                        analysis_question = f"{analysis_question}\n\n[Pre-computed statistics]\n{trend_stats}"

                    # BUG-NEW-03: pass intent to exclude leakage rules from web analytics context
                    answer, in_tok, out_tok = await interpret_results(
                        user_message=analysis_question,
                        sql_results=adaptive_rows,
                        plan=plan.to_dict(),
                        compressed_context=compressed.to_analytics_prompt(intent=plan.intent),
                        row_count=len(adaptive_rows),
                        execution_ms=result.execution_ms,
                    )
                    result.steps.append(
                        f"9b. web_analytics adaptive: {len(adaptive_rows)} rows (all-time) interpreted"
                    )
                    log.info(
                        f"[orchestrator] web_analytics adaptive retry succeeded: "
                        f"{len(adaptive_rows)} rows"
                    )
                else:
                    # Table is truly empty — give a clear diagnostic answer
                    diag_ctx = (
                        diagnostic_note
                        or "⚠️ جدول website_sessions لا يحتوي على بيانات. "
                           "يُرجى التحقق من مسار بيانات جلسات الموقع (ETL pipeline)."
                    )
                    answer, in_tok, out_tok = await interpret_direct(
                        user_message=user_message,
                        plan=plan.to_dict(),
                        compressed_context=diag_ctx,
                    )
                    result.steps.append("9b. web_analytics: table empty — diagnostic answer")
                    log.warning("[orchestrator] web_analytics: no data found even without time filter")

            else:
                # RAG-only or non-review with no SQL results
                answer, in_tok, out_tok = await interpret_direct(
                    user_message=user_message,
                    plan=plan.to_dict(),
                    compressed_context=compressed.to_analytics_prompt(intent=plan.intent),
                )
                result.steps.append("9. Analytics: direct interpretation")

            result.total_tokens_in  += in_tok
            result.total_tokens_out += out_tok
            result.answer = answer

        except Exception as e:
            log.error(f"[orchestrator] Analytics interpretation failed: {e}", exc_info=True)
            result.answer = (
                "تعذّر توليد الإجابة. يرجى المحاولة مرة أخرى أو إعادة صياغة السؤال."
            )
            result.steps.append(f"9. Analytics failed: {e}")

        # --- STEP 9b: Extract Chart Data ---
        if result.rows:
            try:
                result.chart_data = build_chart_data(result.rows, plan.to_dict(), user_message)
                if result.chart_data:
                    result.steps.append(f"9c. Chart: {result.chart_data['type']} ({len(result.chart_data['labels'])} pts)")
            except Exception as chart_e:
                log.warning(f"[orchestrator] Chart extraction failed: {chart_e}")

        # --- STEP 9: Update Memory ---
        try:
            memory_store.update(session_id, plan.to_dict(), plan.intent)
            if plan.intent in ("anomaly_investigation", "trend_analysis") and result.rows:
                topic     = plan.intent
                metric    = plan.metrics[0]    if plan.metrics    else "unknown"
                dimension = plan.group_by[0]   if plan.group_by   else "none"
                value     = str(result.rows[0].get(dimension, "unknown")) if result.rows else "unknown"
                memory_store.record_drilldown(session_id, topic, metric, dimension, value)
            result.steps.append("10. Memory updated")
        except Exception as e:
            log.warning(f"[orchestrator] Memory update failed: {e}")

        # --- STEP 10: Persist conversation ---
        await self._persist(session_id, user_message, result)
        result.steps.append("11. Conversation persisted")

        # IMPROVEMENT: replaced print() with log.debug() — no more stdout leakage
        log.debug("[orchestrator] final SQL: %s", result.sql_used)

        return result

    async def _persist(self, session_id: str, user_message: str, result: PipelineResult):
        """Persist conversation turn and logs."""
        try:
            idx = await get_next_turn_index(session_id)
            await save_turn(session_id, idx,     "user",      user_message)
            await save_turn(session_id, idx + 1, "assistant", result.answer)

            plan_dict = result.plan if isinstance(result.plan, dict) else {}

            await log_query(
                session_id=session_id,
                turn_id=idx,
                user_question=user_message,
                generated_sql=result.sql_used,
                tables_used=[plan_dict.get("target_source", "")] if plan_dict else [],
                execution_ms=result.execution_ms,
                row_count=result.row_count,
                error_message=result.sql_error,
                input_tokens=result.total_tokens_in,
                output_tokens=result.total_tokens_out,
                model="groq+glm+deepseek+minimax",
                intent=result.intent,
            )

            await log_retrieval(
                query_text=user_message,
                intent=result.intent,
                generated_sql=result.sql_used,
                sql_valid=result.sql_error is None,
                sql_executed=result.sql_used is not None,
                exec_error=result.sql_error,
                latency_ms=result.execution_ms,
            )

        except Exception as e:
            log.error(f"[orchestrator] Persist error: {e}")


# Global instance
_orchestrator: Optional[Orchestrator] = None


def get_orchestrator(pool: asyncpg.Pool) -> Orchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = Orchestrator(pool)
    return _orchestrator