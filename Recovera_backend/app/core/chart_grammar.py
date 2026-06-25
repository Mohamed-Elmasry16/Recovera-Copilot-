"""
chart_grammar.py - deterministic visualization grammar for Revenue Leakage Copilot.

This module converts SQL rows into a small legacy chart_data payload consumed by
this project's current React/Chart.js frontend.  It is deliberately conservative:

- Pick one coherent unit family per chart (money OR rate OR count), never all at once.
- Use time-series line charts only for ordered date/month/quarter data.
- Use bar charts for rankings and categorical comparisons.
- Use doughnut charts only for explicit part-to-whole composition questions.
- Use table-only for KPI/detail/ambiguous/correlation results when a single chart
  would mislead.

The full SQL table remains available to the UI; chart_data is only a readable
visual summary.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

log = logging.getLogger(__name__)

# Current frontend expects legacy Chart.js-like keys.  Keep colors because the
# existing UI already uses these fields.  The grammar itself does not choose
# decorative colors beyond stable dataset defaults.
_CHART_COLORS = [
    {"border": "#1D6FFF", "bg": "rgba(29, 111, 255, 0.15)"},
    {"border": "#14B86A", "bg": "rgba(20, 184, 106, 0.15)"},
    {"border": "#8B5CF6", "bg": "rgba(139, 92, 246, 0.15)"},
    {"border": "#F59E0B", "bg": "rgba(245, 158, 11, 0.15)"},
    {"border": "#EF4444", "bg": "rgba(239, 68, 68, 0.15)"},
]

_TIME_HINTS = (
    "month", "month_label", "week", "day", "date", "year", "quarter", "period",
    "order_month", "order_quarter", "session_month",
)

_ID_COLS = {
    "id", "order_id", "customer_id", "product_id", "seller_id", "campaign_id",
    "mql_id", "session_id", "interaction_id", "shipping_id", "refund_id", "doc_id",
}

_HELPER_COLS = {
    "rank", "rn", "row_number", "ranking_type", "sort_key", "bucket_order",
}

_LABEL_PRIORITY = [
    "region", "customer_city", "city", "governorate",
    "month_label", "month", "quarter", "year",
    "seller_name", "seller_id",
    "campaign_name", "channel", "campaign_type",
    "scenario", "leakage_scenario", "leakage_reason",
    "product_category", "category",
    "payment_status", "payment_type",
    "carrier", "shipping_status",
    "refund_reason", "rating", "sentiment",
    "duration_bucket", "traffic_source", "device", "action_type",
    "customer_segment", "segment", "churn_risk",
]

# Metric aliases in priority order by analytical intent.
_METRIC_ALIASES: dict[str, list[str]] = {
    "money": [
        "gross_profit", "gross_loss", "net_profit", "total_profit",
        "leakage_revenue", "revenue_at_risk", "total_revenue", "revenue",
        "refund_amount", "budget", "payment_value", "declared_monthly_revenue", "revenue_per_session",
    ],
    "rate": [
        "leakage_rate_pct", "conversion_rate_pct", "roi_pct", "avg_profit_margin_pct",
        "profit_margin_pct", "avg_profit_margin", "avg_anomaly_score", "return_rate",
        "avg_discount_pct", "churn_rate_pct",
    ],
    "count": [
        "leakage_orders", "total_orders", "order_count", "refund_count", "duplicate_refund_count",
        "total_sessions", "converted_orders", "converted_customers", "total_web_events",
        "seller_paid_twice_orders", "fee_error_orders", "reviewed_orders", "customer_count",
    ],
    "duration": ["avg_session_duration_min", "duration_minutes", "session_duration_min", "avg_delay_days"],
}

_UNIT_BY_KEYWORD = {
    "revenue": "money", "amount": "money", "profit": "money", "budget": "money", "cost": "money", "value": "money",
    "rate": "rate", "pct": "rate", "margin": "rate", "roi": "rate", "score": "rate", "discount": "rate",
    "count": "count", "orders": "count", "sessions": "count", "customers": "count", "events": "count", "cases": "count",
    "duration": "duration", "delay": "duration", "minutes": "duration", "days": "duration",
}

_TITLE_BY_INTENT = {
    "dual_ranking": "Dual ranking",
    "regional_analysis": "Regional ranking",
    "seller_risk": "Seller risk ranking",
    "trend_analysis": "Monthly trend",
    "web_analytics": "Web analytics",
    "campaign_analysis": "Campaign performance",
    "leakage_detection": "Leakage scenarios",
    "payment_leakage": "Payment leakage",
    "shipping_leakage": "Shipping leakage",
    "refund_analysis": "Refund analysis",
    "product_analysis": "Product/category analysis",
    "review_analysis": "Review impact",
    "aggregation": "KPI summary",
}

_ARABIC_TITLE_BY_INTENT = {
    "dual_ranking": "ترتيب مزدوج",
    "regional_analysis": "ترتيب المناطق",
    "seller_risk": "مخاطر البائعين",
    "trend_analysis": "الاتجاه الشهري",
    "web_analytics": "تحليل الموقع",
    "campaign_analysis": "أداء الحملات",
    "leakage_detection": "سيناريوهات التسرب",
    "payment_leakage": "تسرب المدفوعات",
    "shipping_leakage": "تسرب الشحن",
    "refund_analysis": "تحليل المرتجعات",
    "product_analysis": "تحليل المنتجات",
    "review_analysis": "تأثير التقييمات",
    "aggregation": "ملخص المؤشرات",
}

_SHARE_WORDS = (
    "share", "mix", "distribution", "breakdown", "percentage of", "composition",
    "توزيع", "نسبة كل", "حصة", "تقسيم", "مزيج",
)

_CORRELATION_WORDS = (
    "correlation", "relationship", "impact of", "affect", "effect of", "versus", "vs",
    "علاقة", "ارتباط", "تأثير", "يؤثر", "مقابل",
)


@dataclass(frozen=True)
class ChartChoice:
    chart_type: str
    label_col: str
    value_cols: list[str]
    unit_family: str
    title: str
    reason: str


def _coerce_numeric(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
        return float(value)
    if isinstance(value, Decimal):
        try:
            return float(value)
        except (ValueError, InvalidOperation):
            return None
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "")
        if cleaned.endswith("%"):
            cleaned = cleaned[:-1]
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _is_arabic(text: str) -> bool:
    return any("\u0600" <= ch <= "\u06FF" for ch in text or "")


def _norm_map(rows: list[dict]) -> dict[str, str]:
    return {str(c).lower(): str(c) for c in rows[0].keys()} if rows else {}


def _column_exists(lower_cols: dict[str, str], names: list[str] | tuple[str, ...]) -> Optional[str]:
    for name in names:
        if name and name.lower() in lower_cols:
            return lower_cols[name.lower()]
    return None


def _is_numeric_col(rows: list[dict], col: str, sample_size: int = 10) -> bool:
    return any(_coerce_numeric(row.get(col)) is not None for row in rows[:sample_size])


def _metric_unit(col: str) -> str:
    c = col.lower()
    for keyword, family in _UNIT_BY_KEYWORD.items():
        if keyword in c:
            return family
    return "numeric"


def _metric_label(col: str) -> str:
    labels = {
        "total_revenue": "Total revenue",
        "leakage_revenue": "Leakage revenue",
        "revenue_at_risk": "Revenue at risk",
        "total_profit": "Total profit",
        "net_profit": "Net profit",
        "gross_profit": "Gross profit",
        "gross_loss": "Gross loss",
        "loss_value": "Loss",
        "profit_value": "Profit",
        "refund_amount": "Refund amount",
        "budget": "Budget",
        "payment_value": "Payment value",
        "leakage_rate_pct": "Leakage rate",
        "conversion_rate_pct": "Conversion rate",
        "roi_pct": "ROI",
        "avg_profit_margin_pct": "Average profit margin",
        "avg_profit_margin": "Average profit margin",
        "avg_anomaly_score": "Average anomaly score",
        "total_orders": "Total orders",
        "leakage_orders": "Leakage orders",
        "order_count": "Orders",
        "total_sessions": "Sessions",
        "converted_orders": "Converted orders",
        "refund_count": "Refund count",
        "duplicate_refund_count": "Duplicate refunds",
        "avg_delay_days": "Average delay days",
    }
    return labels.get(col.lower(), col.replace("_", " ").strip().title())


def _unit_suffix(unit_family: str, col: str) -> str:
    c = col.lower()
    if unit_family == "rate" and ("pct" in c or "rate" in c or "roi" in c or "margin" in c):
        return "%"
    if "days" in c:
        return " days"
    if "minutes" in c or "duration" in c:
        return " min"
    return ""


def _unit_prefix(unit_family: str) -> str:
    return "EGP " if unit_family == "money" else ""


def _value_format(unit_family: str) -> str:
    if unit_family in {"money", "count"}:
        return "compact"
    return "raw"


def _has_share_intent(user_message: str) -> bool:
    t = (user_message or "").lower()
    return any(w in t for w in _SHARE_WORDS)


def _has_correlation_intent(user_message: str) -> bool:
    t = (user_message or "").lower()
    return any(w in t for w in _CORRELATION_WORDS)


def _is_time_col(col: str) -> bool:
    c = col.lower()
    return any(h == c or h in c for h in _TIME_HINTS)


def _find_any_time_col(lower_cols: dict[str, str]) -> Optional[str]:
    """Scan all columns in the result and return the first time-like column found.

    Used so that trend data is detected even when a categorical column (region,
    city …) appears earlier in the label-priority list and would otherwise be
    chosen as the label, hiding the time axis.
    """
    # Prefer named time columns in a stable order.
    for hint in _TIME_HINTS:
        if hint in lower_cols:
            return lower_cols[hint]
    # Fall back: any column whose name contains a time hint.
    for lower, orig in lower_cols.items():
        if lower not in _HELPER_COLS and lower not in _ID_COLS and _is_time_col(lower):
            return orig
    return None


def _ordered_time_labels(rows: list[dict], label_col: str) -> list[str]:
    # Keep labels as sent by SQL.  The SQL templates already use month_label for
    # human-readable month names where possible.
    return [str(r.get(label_col, "")) for r in rows]


def _pick_label_col(rows: list[dict], lower_cols: dict[str, str], policy: dict[str, Any]) -> Optional[str]:
    requested_label = policy.get("label") if isinstance(policy, dict) else None
    alias_map = {
        "region": ["region", "customer_city", "city"],
        "city": ["region", "customer_city", "city"],
        "month": ["month_label", "month", "session_month"],
        "seller": ["seller_name", "seller_id"],
        "campaign": ["campaign_name", "campaign_id"],
        "payment": ["payment_status", "payment_type"],
        "shipping": ["carrier", "shipping_status"],
        "web": ["month", "month_label", "traffic_source", "device", "duration_bucket"],
    }
    if requested_label:
        candidates = alias_map.get(str(requested_label), [str(requested_label)])
        col = _column_exists(lower_cols, candidates)
        if col:
            return col

    col = _column_exists(lower_cols, _LABEL_PRIORITY)
    if col:
        return col

    for col in rows[0].keys():
        cname = str(col).lower()
        if cname in _HELPER_COLS or cname in _ID_COLS:
            continue
        if _coerce_numeric(rows[0].get(col)) is None:
            return str(col)
    return None


def _available_metrics(rows: list[dict], lower_cols: dict[str, str]) -> dict[str, list[str]]:
    found = {"money": [], "rate": [], "count": [], "duration": [], "numeric": []}

    # First pass by curated aliases to keep selection deterministic.
    for unit, names in _METRIC_ALIASES.items():
        for name in names:
            col = lower_cols.get(name.lower())
            if col and col.lower() not in _HELPER_COLS and _is_numeric_col(rows, col):
                if col not in found[unit]:
                    found[unit].append(col)

    # Second pass for any numeric column not captured by aliases.
    known = {c for values in found.values() for c in values}
    for col in rows[0].keys():
        col = str(col)
        cname = col.lower()
        if cname in _HELPER_COLS or cname in _ID_COLS or col in known:
            continue
        if _is_numeric_col(rows, col):
            found[_metric_unit(col)].append(col)
    return found


def _preferred_unit(plan: dict[str, Any], user_message: str, metrics_by_unit: dict[str, list[str]]) -> str:
    policy = plan.get("chart_policy", {}) if isinstance(plan, dict) else {}
    if isinstance(policy, dict):
        unit = policy.get("unit_family")
        if unit in {"money", "rate", "count", "duration", "numeric"} and metrics_by_unit.get(unit):
            return unit
        requested_metrics = policy.get("metrics") or []
        for m in requested_metrics:
            mu = _metric_unit(str(m))
            if metrics_by_unit.get(mu):
                return mu

    primary_metric = str(plan.get("primary_metric") or "") if isinstance(plan, dict) else ""
    if primary_metric:
        mu = _metric_unit(primary_metric)
        if metrics_by_unit.get(mu):
            return mu

    text = (user_message or "").lower()
    if any(w in text for w in ("rate", "percentage", "pct", "نسبة", "معدل", "roi", "conversion")) and metrics_by_unit.get("rate"):
        return "rate"
    if any(w in text for w in ("orders", "count", "sessions", "عدد", "طلبات", "جلسات")) and metrics_by_unit.get("count"):
        return "count"
    if any(w in text for w in ("delay", "duration", "مدة", "تأخير", "تاخير")) and metrics_by_unit.get("duration"):
        return "duration"

    # Executive default: money if available, otherwise rate, count, duration, numeric.
    for unit in ("money", "rate", "count", "duration", "numeric"):
        if metrics_by_unit.get(unit):
            return unit
    return "numeric"


def _policy_metric_cols(plan: dict[str, Any], lower_cols: dict[str, str], rows: list[dict]) -> list[str]:
    policy = plan.get("chart_policy", {}) if isinstance(plan, dict) else {}
    if not isinstance(policy, dict):
        return []
    out: list[str] = []
    for metric in policy.get("metrics") or []:
        col = lower_cols.get(str(metric).lower())
        if col and _is_numeric_col(rows, col):
            out.append(col)
    return out


def _rank_label(value: Any) -> str:
    raw = str(value or "").strip()
    mapping = {
        "highest_revenue": "Highest revenue",
        "top_profit": "Highest profit",
        "highest_profit": "Highest profit",
        "highest_leakage_revenue": "Highest leakage revenue",
        "top_loss": "Highest loss",
        "highest_loss": "Highest loss",
        "highest_leakage_rate": "Highest leakage rate",
        "highest_leakage_orders": "Highest leakage orders",
        "highest_revenue_at_risk": "Highest revenue at risk",
    }
    return mapping.get(raw, raw.replace("_", " ").title())


_MONTH_LABELS_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_MONTH_LABELS_AR = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]


def _format_period_label(value: Any, label_col: str, user_message: str = "") -> str:
    raw = str(value or "").strip()
    col = str(label_col or "").lower()
    if col in {"month", "month_number", "order_month_number"}:
        try:
            month_num = int(float(raw))
            if 1 <= month_num <= 12:
                labels = _MONTH_LABELS_AR if _is_arabic(user_message) else _MONTH_LABELS_EN
                return labels[month_num - 1]
        except Exception:
            pass
    return raw


def _build_dual_profit_loss_chart(rows: list[dict], plan: dict[str, Any], user_message: str, label_col: str, lower_cols: dict[str, str]) -> Optional[dict[str, Any]]:
    ranking_col = lower_cols.get("ranking_type")
    profit_col = lower_cols.get("gross_profit") or lower_cols.get("profit_value")
    loss_col = lower_cols.get("gross_loss") or lower_cols.get("loss_value")
    if not (ranking_col and profit_col and loss_col):
        return None

    display_rows = rows[:20]
    labels: list[str] = []
    profit_values: list[float] = []
    loss_values: list[float] = []

    for row in display_rows:
        ranking_type = str(row.get(ranking_col) or "")
        label = str(row.get(label_col) or "Unknown")
        labels.append(f"{label} · {_rank_label(ranking_type)}")
        profit = _coerce_numeric(row.get(profit_col)) or 0.0
        loss = _coerce_numeric(row.get(loss_col)) or 0.0
        # Keep the visual message clean: top-profit rows show the profit bar;
        # top-loss rows show the loss bar. The table still contains both metrics.
        if "loss" in ranking_type:
            profit_values.append(0.0)
            loss_values.append(round(abs(loss), 2))
        else:
            profit_values.append(round(abs(profit), 2))
            loss_values.append(0.0)

    title = "ترتيب الأرباح والخسائر" if _is_arabic(user_message) else "Profit vs loss ranking"
    note = (
        "Profit and loss are separated into two series to avoid mixing opposite business meanings on one blue bar."
    )
    if _is_arabic(user_message):
        note = "تم فصل الأرباح والخسائر في سلسلتين مختلفتين حتى لا تظهر القيم بعرض بصري مضلل."

    return {
        "type": "bar",
        "title": title,
        "labels": labels,
        "datasets": [
            {
                "label": "Profit" if not _is_arabic(user_message) else "الأرباح",
                "data": profit_values,
                "unit_family": "money",
                "value_prefix": "EGP ",
                "value_suffix": "",
                "value_format": "compact",
                "tone": "primary",
            },
            {
                "label": "Loss" if not _is_arabic(user_message) else "الخسائر",
                "data": loss_values,
                "unit_family": "money",
                "value_prefix": "EGP ",
                "value_suffix": "",
                "value_format": "compact",
                "tone": "danger",
            },
        ],
        "label_col": label_col,
        "value_cols": [profit_col, loss_col],
        "unit_family": "money",
        "chart_grammar_applied": True,
        "grammar_reason": "dual profit/loss split metric",
        "x_axis_label": label_col.replace("_", " ").title(),
        "y_axis_label": "EGP",
        "chart_note": note,
    }


def _choose_chart(rows: list[dict], plan: dict[str, Any], user_message: str) -> Optional[ChartChoice]:
    lower_cols = _norm_map(rows)
    policy = plan.get("chart_policy", {}) if isinstance(plan, dict) else {}
    policy_type = policy.get("type") if isinstance(policy, dict) else None
    intent = str(plan.get("intent") or "") if isinstance(plan, dict) else ""

    if policy_type in {"none", "table_only", "kpi"}:
        return None
    if _has_correlation_intent(user_message) and intent not in {"web_analytics"}:
        # The current frontend has no reliable scatter rendering.  Prevent a
        # misleading bar chart for relationship questions.
        return None

    # ── Bug-fix: detect time columns BEFORE calling _pick_label_col ───────────
    # _LABEL_PRIORITY puts categorical columns (region, city …) ahead of time
    # columns (month, quarter …).  When a trend query returns both a regional
    # column AND a time column, the priority list picks the regional column and
    # _is_time_col(label_col) returns False → bar chart instead of line chart.
    #
    # Rule: if any time column is present in the result AND the data looks like
    # a trend (intent is trend-like OR ≥ 4 rows), use that column as the label
    # so the chart type decision below can correctly choose "line".
    _TREND_INTENTS = {"trend_analysis", "revenue_analysis", "web_analytics", "campaign_analysis"}
    time_col_in_data = _find_any_time_col(lower_cols)
    if time_col_in_data and (intent in _TREND_INTENTS or len(rows) >= 4):
        label_col: Optional[str] = time_col_in_data
    else:
        label_col = _pick_label_col(rows, lower_cols, policy if isinstance(policy, dict) else {})
    # ── End Bug-fix ─────────────────────────────────────────────────────────

    if not label_col:
        return None

    # Special case: dual ranking rows.  Use one chosen metric per row; do not
    # render rate/count/money together.
    if "ranking_type" in lower_cols:
        ranking_col = lower_cols["ranking_type"]
        rank_metric = str(plan.get("primary_metric") or "leakage_revenue") if isinstance(plan, dict) else "leakage_revenue"
        if rank_metric == "leakage_rate_pct" and lower_cols.get("leakage_rate_pct"):
            return ChartChoice("bar", label_col, [lower_cols["leakage_rate_pct"]], "rate", _title(intent, user_message), "dual-ranking rate metric")
        if rank_metric == "leakage_orders" and lower_cols.get("leakage_orders"):
            return ChartChoice("bar", label_col, [lower_cols["leakage_orders"]], "count", _title(intent, user_message), "dual-ranking count metric")
        if lower_cols.get("leakage_revenue"):
            return ChartChoice("bar", label_col, [lower_cols["leakage_revenue"]], "money", _title(intent, user_message), "dual-ranking financial metric")
        if lower_cols.get("revenue_at_risk"):
            return ChartChoice("bar", label_col, [lower_cols["revenue_at_risk"]], "money", _title(intent, user_message), "dual-ranking revenue-at-risk metric")

    metrics_by_unit = _available_metrics(rows, lower_cols)
    unit = _preferred_unit(plan, user_message, metrics_by_unit)

    requested = _policy_metric_cols(plan, lower_cols, rows)
    requested_same_unit = [c for c in requested if _metric_unit(c) == unit or unit == "numeric"]
    value_cols = requested_same_unit or metrics_by_unit.get(unit, [])
    if not value_cols:
        for candidate_unit in ("money", "rate", "count", "duration", "numeric"):
            if metrics_by_unit.get(candidate_unit):
                unit = candidate_unit
                value_cols = metrics_by_unit[candidate_unit]
                break
    if not value_cols:
        return None

    # Only allow multiple series when they share a coherent unit family and it
    # genuinely helps, e.g. sessions vs converted orders or revenue vs profit.
    max_series = 2 if unit in {"money", "count"} and intent in {"web_analytics", "trend_analysis", "campaign_analysis"} else 1
    value_cols = value_cols[:max_series]

    is_time = _is_time_col(label_col)
    n = len(rows)
    if is_time:
        chart_type = "line"
    elif len(value_cols) == 1 and 2 <= n <= 3:
        # ── Bug-fix: pie/doughnut for small category sets ────────────────────
        # Previously required _has_share_intent which only fires when the user
        # literally says "distribution", "share", etc.  Ordinary questions such
        # as "show me revenue by payment type" (2-3 rows) never matched, so the
        # chart fell through to bar.  2-3 categories are always better as a
        # doughnut; the table below still shows all numeric columns.
        chart_type = "doughnut"
        # ── End Bug-fix ──────────────────────────────────────────────────────
    elif _has_share_intent(user_message) and len(value_cols) == 1 and 4 <= n <= 8:
        # For 4-8 categories, still use doughnut when the user explicitly asks
        # about distribution / share / mix / composition.
        chart_type = "doughnut"
    else:
        chart_type = "bar"

    if policy_type in {"bar", "line", "doughnut"}:
        # Policy can force bar/line/doughnut, but time data remains line unless
        # the user explicitly asked distribution/share.
        if not is_time or policy_type != "bar":
            chart_type = policy_type

    return ChartChoice(chart_type, label_col, value_cols, unit, _title(intent, user_message), f"{unit} metric family")


def _title(intent: str, user_message: str) -> str:
    if _is_arabic(user_message):
        return _ARABIC_TITLE_BY_INTENT.get(intent, "نتائج الاستعلام")
    return _TITLE_BY_INTENT.get(intent, "Query results")


def _labels_for_rows(rows: list[dict], label_col: str, lower_cols: dict[str, str], user_message: str = "") -> list[str]:
    if "ranking_type" not in lower_cols:
        return [_format_period_label(r.get(label_col, ""), label_col, user_message) for r in rows]

    ranking_col = lower_cols["ranking_type"]
    labels: list[str] = []
    for row in rows:
        label = str(row.get(label_col) or "Unknown")
        labels.append(f"{label} · {_rank_label(row.get(ranking_col))}")
    return labels


def _dataset_for_col(rows: list[dict], col: str, unit_family: str, chart_type: str, index: int = 0) -> dict[str, Any]:
    color = _CHART_COLORS[index % len(_CHART_COLORS)]
    data: list[Optional[float]] = []
    for row in rows:
        value = _coerce_numeric(row.get(col))
        data.append(round(value, 2) if value is not None else None)

    ds: dict[str, Any] = {
        "label": _metric_label(col),
        "data": data,
        "borderColor": color["border"],
        "backgroundColor": color["bg"],
        "borderWidth": 1.5,
        "unit_family": unit_family,
        "value_prefix": _unit_prefix(unit_family),
        "value_suffix": _unit_suffix(unit_family, col),
        "value_format": _value_format(unit_family),
    }
    if chart_type == "line":
        ds.update({"tension": 0.35, "fill": True, "pointRadius": 3})
    elif chart_type == "doughnut":
        ds["backgroundColor"] = [_CHART_COLORS[i % len(_CHART_COLORS)]["border"] for i in range(len(data))]
    return ds


def build_chart_data(rows: list[dict], plan: dict[str, Any], user_message: str = "") -> Optional[dict[str, Any]]:
    """Return legacy chart_data dict or None.

    This is the single public entry point for the orchestrator.
    """
    try:
        if not rows or len(rows) < 2:
            return None
        # Never chart very wide raw detail result sets.
        if len(rows[0].keys()) > 20 and str(plan.get("intent") or "") in {"lookup", "general_query"}:
            return None

        choice = _choose_chart(rows, plan or {}, user_message or "")
        if not choice:
            return None

        # Dual profit/loss is a special visual grammar: it needs separate
        # profit and loss series, not one ambiguous scalar.
        lower_all = _norm_map(rows)
        if "ranking_type" in lower_all and ("gross_profit" in lower_all or "profit_value" in lower_all) and ("gross_loss" in lower_all or "loss_value" in lower_all):
            split_chart = _build_dual_profit_loss_chart(rows, plan or {}, user_message or "", choice.label_col, lower_all)
            if split_chart:
                return split_chart

        display_rows = rows[:24] if choice.chart_type == "line" else rows[:15]
        lower_cols = _norm_map(display_rows)
        labels = _labels_for_rows(display_rows, choice.label_col, lower_cols, user_message or "")
        datasets = [
            _dataset_for_col(display_rows, col, choice.unit_family, choice.chart_type, i)
            for i, col in enumerate(choice.value_cols)
        ]

        return {
            "type": choice.chart_type,
            "title": choice.title,
            "labels": labels,
            "datasets": datasets,
            "label_col": choice.label_col,
            "value_cols": choice.value_cols,
            "unit_family": choice.unit_family,
            "chart_grammar_applied": True,
            "grammar_reason": choice.reason,
            "x_axis_label": choice.label_col.replace("_", " ").title(),
            "y_axis_label": _metric_label(choice.value_cols[0]) if choice.value_cols else "Value",
            "chart_note": "Chart shows one coherent metric family; full query table contains the remaining metrics.",
        }
    except Exception as exc:  # defensive: charting should never break the answer
        log.warning("[chart_grammar] failed: %s", exc, exc_info=True)
        return None


__all__ = ["build_chart_data"]