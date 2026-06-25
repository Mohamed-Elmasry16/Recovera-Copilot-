"""
question_patterns.py - deterministic coverage layer for Revenue Leakage Copilot.

This module is intentionally boring and rule-driven.  It does not try to answer
questions; it only classifies common business-question families so the planner,
SQL generator, and chart builder stop relying on random LLM behavior for the
high-frequency cases.

The rule engine returns a probability-like confidence score.  The score is not a
statistical posterior; it is an operational confidence used to decide whether a
safe deterministic plan/template should override the LLM path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
import re


@dataclass(frozen=True)
class QueryProfile:
    family: str = "general_query"
    intent: str = "general_query"
    route: str = "hybrid"
    difficulty: str = "medium"
    confidence: float = 0.0
    reason: str = ""
    template_key: str = ""
    target_source: str = ""
    metrics: tuple[str, ...] = ()
    group_by: tuple[str, ...] = ()
    order_by: tuple[dict[str, str], ...] = ()
    limit: int = 20
    needs_sql: bool = True
    needs_rag: bool = True
    chart_policy: dict[str, Any] = field(default_factory=dict)
    dimension: str = ""
    rank_metric: str = ""

    def to_plan_patch(self) -> dict[str, Any]:
        """Return fields that can be merged onto QueryPlan."""
        return {
            "intent": self.intent,
            "route": self.route,
            "difficulty": self.difficulty,
            "needs_sql": self.needs_sql,
            "needs_rag": self.needs_rag,
            "target_source": self.target_source,
            "metrics": list(self.metrics),
            "group_by": list(self.group_by),
            "order_by": list(self.order_by),
            "limit": self.limit,
            "template_key": self.template_key,
            "chart_policy": self.chart_policy,
            "primary_dimension": self.dimension,
            "primary_metric": self.rank_metric,
            "profile_confidence": self.confidence,
            "reasoning": self.reason,
        }


# ---------------------------------------------------------------------------
# Keyword lexicons.  Keep these explicit and domain-specific.
# ---------------------------------------------------------------------------

_DUAL_WORDS = (
    " and ", " vs ", "versus", "compare", "comparison", "best and worst", "highest and lowest",
    " و", " وأ", "وقارن", "مقارنة", "اعلى و", "أعلى و", "اكتر و", "أكتر و",
)

_REVENUE_WORDS = (
    "revenue", "sales", "earning", "earnings", "profit", "profitable", "total_revenue",
    "إيراد", "ايراد", "إيرادات", "ايرادات", "مبيعات", "ربح", "أرباح", "ارباح", "ربحية",
)

_LOSS_WORDS = (
    "loss", "losses", "money lost", "financial loss", "leakage amount", "leakage revenue",
    "revenue leakage", "خسارة", "خسائر", "خسرت", "تسرب مالي", "فاقد", "مفقود",
)

_RISK_WORDS = (
    "risk", "rate", "percentage", "pct", "leakage rate", "risky", "danger",
    "نسبة", "معدل", "أخطر", "اخطر", "مخاطر", "خطر",
)

_VOLUME_WORDS = (
    "orders", "cases", "incidents", "count", "volume", "number of",
    "طلبات", "حالات", "عدد", "كم طلب",
)

_REGION_WORDS = (
    "region", "area", "city", "governorate", "location", "geography",
    "منطقة", "مناطق", "مدينة", "مدن", "محافظة", "محافظات", "مكان", "اماكن", "أماكن",
)

_SELLER_WORDS = (
    "seller", "sellers", "vendor", "vendors", "merchant", "merchants",
    "بائع", "بائعين", "تاجر", "تجار", "سيلر", "سيلرز",
)

_SCENARIO_WORDS = (
    "scenario", "leakage type", "leakage reason", "reason", "root cause", "cause",
    "نوع التسرب", "سبب", "أسباب", "اسباب", "سيناريو", "سيناريوهات",
)

_MONTH_WORDS = (
    "month", "monthly", "trend", "over time", "time series", "quarter", "quarterly", "year",
    "شهر", "شهري", "الشهور", "ترند", "اتجاه", "ربع", "سنوي", "سنة",
)

# Forecast/prediction wording WITHOUT an explicit month/quarter/year word
# (those are already covered above). Kept as its own list and checked last
# (see end of detect_question_profile) so it never shadows a more specific
# dimension match — e.g. "predict seller risk" still hits Seller Risk, not
# this fallback, because that check runs earlier in the function.
_FORECAST_WORDS = (
    "forecast", "predict", "prediction", "projection", "projected", "project for",
    "outlook", "anticipate", "anticipated", "expected to", "upcoming", "next period",
    "توقع", "توقعات", "تنبؤ", "تنبؤات", "تقدير", "متوقع", "نتوقع", "استشراف",
)

_PAYMENT_WORDS = (
    "payment", "cod", "cash on delivery", "unpaid", "partial payment", "missing payment",
    "seller paid twice", "دفع", "مدفوع", "غير مدفوع", "دفع جزئي", "كاش", "تحصيل", "cod",
)

_SHIPPING_WORDS = (
    "shipping", "delivery", "carrier", "late", "delay", "delayed", "never shipped", "freight",
    "شحن", "توصيل", "تأخير", "تاخير", "متأخر", "شركة الشحن", "كارير", "لم يتم الشحن",
)

_REFUND_WORDS = (
    "refund", "refunds", "return", "duplicate refund", "returned", "refunded",
    "استرداد", "مرتجع", "مرتجعات", "ريفند", "استرجاع", "مكرر",
)

_REVIEW_WORDS = (
    "review", "reviews", "complaint", "complaints", "sentiment", "customers say", "negative reviews",
    "مراجعة", "مراجعات", "تقييم", "تقييمات", "شكوى", "شكاوى", "تعليقات", "رأي العملاء", "اراء العملاء",
)

_CAMPAIGN_WORDS = (
    "campaign", "campaigns", "marketing", "roi", "attribution", "ads", "ad campaign",
    "حملة", "حملات", "تسويق", "إعلانات", "اعلانات", "اعلان", "إعلان", "عائد الحملة",
)

_WEB_WORDS = (
    "website", "web", "session", "sessions", "traffic", "source", "duration", "device", "mobile", "desktop",
    "موقع", "جلسات", "سيشن", "سيشنز", "ترافيك", "زيارات", "موبايل", "ديسكتوب",
)

_PRODUCT_WORDS = (
    "product", "products", "category", "categories", "sku", "item", "items",
    "منتج", "منتجات", "تصنيف", "فئة", "فئات", "كاتيجوري",
)

_CUSTOMER_WORDS = (
    "customer", "customers", "segment", "churn", "ltv", "lifetime value",
    "عميل", "عملاء", "شريحة", "شرائح", "تسرب العملاء", "قيمة العميل",
)

_LOOKUP_WORDS = (
    "order id", "order_id", "customer id", "customer_id", "seller id", "seller_id", "details", "تفاصيل", "رقم الطلب",
)


_PATTERNS = {
    "uuidish": re.compile(r"\b[a-f0-9]{24,36}\b", re.I),
}


def _has(text: str, words: tuple[str, ...]) -> bool:
    return any(w in text for w in words)


def _count_hits(text: str, groups: list[tuple[str, ...]]) -> int:
    return sum(1 for g in groups if _has(text, g))


def _leakage_metric(text: str) -> tuple[str, str]:
    """Return selected leakage metric and human reason."""
    if _has(text, _RISK_WORDS):
        return "leakage_rate_pct", "risk/rate wording detected"
    if _has(text, _VOLUME_WORDS):
        return "leakage_orders", "case/order volume wording detected"
    if _has(text, _LOSS_WORDS):
        return "leakage_revenue", "financial-loss wording detected"
    # Executive default for ambiguous leakage/loss: financial impact.
    return "leakage_revenue", "ambiguous leakage wording; defaulting to financial impact"


def _dimension(text: str) -> tuple[str, str]:
    if _has(text, _REGION_WORDS):
        return "customer_city", "region"
    if _has(text, _SELLER_WORDS):
        return "seller_id", "seller"
    if _has(text, _SCENARIO_WORDS):
        return "scenario", "scenario"
    if _has(text, _PRODUCT_WORDS):
        return "product_category", "product"
    if _has(text, _CUSTOMER_WORDS):
        return "customer_segment", "customer"
    if _has(text, _PAYMENT_WORDS):
        return "payment_type", "payment"
    if _has(text, _SHIPPING_WORDS):
        return "carrier", "shipping"
    return "", ""


def _web_dimension(text: str) -> str:
    if any(k in text for k in ("duration", "time spent", "مدة", "وقت", "يفضل فاتح")):
        return "duration"
    if any(k in text for k in ("device", "mobile", "desktop", "موبايل", "ديسكتوب", "تابلت")):
        return "device"
    if any(k in text for k in ("traffic source", "source", "organic", "paid", "direct", "referral", "مصدر", "ترافيك")):
        return "traffic_source"
    return "monthly_trend"


def detect_question_profile(message: str) -> QueryProfile:
    text = (message or "").strip().lower()
    if not text:
        return QueryProfile(family="empty", intent="non_database", route="non_database", confidence=1.0, needs_sql=False, needs_rag=False)

    dim_col, dim_family = _dimension(text)
    leak_metric, leak_reason = _leakage_metric(text)
    has_revenue = _has(text, _REVENUE_WORDS)
    has_loss = _has(text, _LOSS_WORDS) or _has(text, _RISK_WORDS) or _has(text, _VOLUME_WORDS)
    has_dual = _has(text, _DUAL_WORDS) and has_revenue and has_loss

    # 1) Specific ID lookup.  High precision.
    if _has(text, _LOOKUP_WORDS) or _PATTERNS["uuidish"].search(text):
        return QueryProfile(
            family="lookup", intent="lookup", route="sql_only", difficulty="easy", confidence=0.90,
            template_key="lookup_order_or_entity", target_source="ml_output.mv_leakage_dashboard",
            metrics=("total_revenue", "profit_margin", "risk_tier"), limit=20, needs_rag=False,
            chart_policy={"type": "table_only"}, reason="ID/detail lookup detected.",
        )

    # 2) Review/sentiment text.
    if _has(text, _REVIEW_WORDS):
        financial = _has(text, _REVENUE_WORDS + _LOSS_WORDS + ("impact", "effect", "تأثير", "اثر", "أثر"))
        return QueryProfile(
            family="reviews", intent="review_analysis", route="hybrid" if financial else "rag_only",
            difficulty="complex" if financial else "medium", confidence=0.90,
            template_key="review_financial_impact" if financial else "rag_review_only",
            target_source="ml_output.mv_leakage_dashboard" if financial else "rag.review_embeddings",
            metrics=("total_revenue", "avg_profit_margin", "rating") if financial else (),
            group_by=("sentiment", "rating") if financial else (), needs_sql=financial, needs_rag=True,
            chart_policy={"label": "rating", "metrics": ["total_revenue"], "type": "bar"} if financial else {"type": "none"},
            reason="Review/sentiment wording detected; text semantics are handled by RAG.",
        )

    # 3) Campaign / marketing.
    if _has(text, _CAMPAIGN_WORDS):
        metric = "roi_pct" if any(k in text for k in ("roi", "عائد")) else "total_revenue"
        return QueryProfile(
            family="campaign", intent="campaign_analysis", route="sql_only", difficulty="medium", confidence=0.92,
            template_key="campaign_performance", target_source="marketing.campaign_attribution",
            metrics=("total_revenue", "avg_profit_margin_pct", "order_count", "roi_pct"),
            group_by=("campaign_id", "campaign_name", "channel"), order_by=({"col": metric, "dir": "DESC"},),
            limit=10, needs_rag=False,
            chart_policy={"label": "campaign_name", "metrics": [metric], "unit_family": "money_or_pct", "type": "bar"},
            reason="Campaign/marketing attribution question detected.", rank_metric=metric,
        )

    # 4) Web analytics / traffic / sessions.
    if _has(text, _WEB_WORDS):
        web_dim = _web_dimension(text)
        return QueryProfile(
            family="web_analytics", intent="web_analytics", route="sql_only", difficulty="medium", confidence=0.92,
            template_key=f"web_{web_dim}", target_source="marketing.website_sessions",
            metrics=("total_sessions", "converted_orders", "conversion_rate_pct", "revenue_per_session", "avg_profit_margin"),
            group_by=("month",), order_by=({"col": "month", "dir": "DESC"},), limit=24, needs_rag=False,
            chart_policy={"label": "month", "metrics": ["total_sessions", "converted_orders"], "unit_family": "count", "type": "line"},
            reason="Website/session/traffic question detected.", dimension=web_dim,
        )

    # 5) Dual ranking.  Handles the Arabic screenshot class.
    if has_dual and dim_col:
        return QueryProfile(
            family="dual_ranking", intent="dual_ranking", route="sql_only", difficulty="complex", confidence=0.94,
            template_key=f"dual_{dim_family}_{leak_metric}", target_source="ml_output.mv_leakage_dashboard",
            metrics=("total_revenue", leak_metric), group_by=(dim_col,), order_by=({"col": "total_revenue", "dir": "DESC"}, {"col": leak_metric, "dir": "DESC"}),
            limit=10, needs_rag=False,
            chart_policy={"label": dim_col, "metrics": ["ranked_value"], "unit_family": "single_metric", "type": "bar"},
            reason=f"Dual ranking detected. Revenue side uses total_revenue; leakage side uses {leak_metric} because {leak_reason}.",
            dimension=dim_col, rank_metric=leak_metric,
        )

    # 6) Time trends.
    if _has(text, _MONTH_WORDS):
        # Leakage monthly trend is safest via MV.  Revenue/profit trend can still use monthly MV.
        metric = "revenue_at_risk" if has_loss else "total_revenue"
        return QueryProfile(
            family="trend", intent="trend_analysis", route="sql_only", difficulty="medium", confidence=0.86,
            template_key="monthly_leakage_trend", target_source="ml_output.mv_monthly_leakage",
            metrics=("total_orders", "leakage_orders", "leakage_rate_pct", "total_revenue", "revenue_at_risk", "total_profit"),
            group_by=("month",), order_by=({"col": "month", "dir": "ASC"},), limit=36, needs_rag=False,
            chart_policy={"label": "month_label", "metrics": [metric], "unit_family": "money", "type": "line"},
            reason="Time/month/quarter trend wording detected.", rank_metric=metric,
        )

    # 7) Scenario/root cause numeric ranking.
    if _has(text, _SCENARIO_WORDS) and not (_has(text, _REFUND_WORDS) or _has(text, _PAYMENT_WORDS) or _has(text, _SHIPPING_WORDS)):
        metric = "revenue_at_risk" if leak_metric == "leakage_revenue" else "total_orders"
        return QueryProfile(
            family="scenario", intent="leakage_detection", route="sql_only", difficulty="medium", confidence=0.88,
            template_key="scenario_ranking", target_source="ml_output.mv_leakage_by_scenario",
            metrics=("total_orders", "revenue_at_risk", "avg_anomaly_score", "avg_profit_margin"),
            group_by=("scenario",), order_by=({"col": metric, "dir": "DESC"},), limit=20, needs_rag=False,
            chart_policy={"label": "scenario", "metrics": [metric], "unit_family": "money_or_count", "type": "bar"},
            reason="Leakage scenario/reason ranking detected.", rank_metric=metric,
        )

    # 8) Seller ranking/risk.
    if _has(text, _SELLER_WORDS):
        metric = "leakage_rate_pct" if _has(text, _RISK_WORDS) else "total_revenue" if has_revenue and not has_loss else "leakage_orders"
        return QueryProfile(
            family="seller", intent="seller_risk", route="sql_only", difficulty="medium", confidence=0.88,
            template_key="seller_risk_ranking", target_source="ml_output.mv_seller_risk",
            metrics=("total_orders", "leakage_orders", "leakage_rate_pct", "total_revenue", "avg_anomaly_score"),
            group_by=("seller_id", "seller_name"), order_by=({"col": metric, "dir": "DESC"},), limit=20, needs_rag=False,
            chart_policy={"label": "seller_name", "metrics": [metric], "unit_family": "single_metric", "type": "bar"},
            reason="Seller/vendor risk or ranking detected.", rank_metric=metric,
        )

    # 9) Payment leakage.
    if _has(text, _PAYMENT_WORDS):
        return QueryProfile(
            family="payment", intent="payment_leakage", route="sql_only", difficulty="medium", confidence=0.86,
            template_key="payment_leakage", target_source="ml_output.mv_leakage_dashboard",
            metrics=("total_orders", "leakage_orders", "leakage_revenue", "leakage_rate_pct"),
            group_by=("payment_status", "payment_type"), order_by=({"col": "leakage_revenue", "dir": "DESC"},), limit=20, needs_rag=False,
            chart_policy={"label": "payment_status", "metrics": ["leakage_revenue"], "unit_family": "money", "type": "bar"},
            reason="Payment/COD/unpaid leakage detected.", rank_metric="leakage_revenue",
        )

    # 10) Shipping leakage.
    if _has(text, _SHIPPING_WORDS):
        return QueryProfile(
            family="shipping", intent="shipping_leakage", route="sql_only", difficulty="medium", confidence=0.86,
            template_key="shipping_leakage", target_source="ml_output.mv_leakage_dashboard",
            metrics=("total_orders", "leakage_orders", "avg_delay_days", "leakage_revenue", "leakage_rate_pct"),
            group_by=("carrier", "shipping_status"), order_by=({"col": "leakage_revenue", "dir": "DESC"},), limit=20, needs_rag=False,
            chart_policy={"label": "carrier", "metrics": ["leakage_revenue"], "unit_family": "money", "type": "bar"},
            reason="Shipping/delivery/carrier leakage detected.", rank_metric="leakage_revenue",
        )

    # 11) Refund leakage.
    if _has(text, _REFUND_WORDS):
        return QueryProfile(
            family="refund", intent="refund_analysis", route="sql_only", difficulty="medium", confidence=0.86,
            template_key="refund_analysis", target_source="ecommerce.refunds",
            metrics=("refund_count", "refund_amount", "duplicate_refund_count"),
            group_by=("refund_reason",), order_by=({"col": "refund_amount", "dir": "DESC"},), limit=20, needs_rag=False,
            chart_policy={"label": "refund_reason", "metrics": ["refund_amount"], "unit_family": "money", "type": "bar"},
            reason="Refund/return analysis detected.", rank_metric="refund_amount",
        )

    # 12) Product/category analysis.
    if _has(text, _PRODUCT_WORDS):
        metric = "total_revenue" if has_revenue else "leakage_revenue"
        return QueryProfile(
            family="product", intent="product_analysis", route="sql_only", difficulty="medium", confidence=0.82,
            template_key="product_category_analysis", target_source="ecommerce.order_items",
            metrics=("total_revenue", "total_orders", "leakage_revenue", "avg_discount_pct"),
            group_by=("product_category",), order_by=({"col": metric, "dir": "DESC"},), limit=20, needs_rag=False,
            chart_policy={"label": "product_category", "metrics": [metric], "unit_family": "money", "type": "bar"},
            reason="Product/category analysis detected.", rank_metric=metric,
        )

    # 13) Region/city one-sided ranking.
    if _has(text, _REGION_WORDS):
        metric = "total_revenue" if has_revenue and not has_loss else leak_metric
        return QueryProfile(
            family="region", intent="regional_analysis", route="sql_only", difficulty="medium", confidence=0.84,
            template_key="region_ranking", target_source="ml_output.mv_leakage_dashboard",
            metrics=("total_revenue", "leakage_revenue", "leakage_rate_pct", "leakage_orders"),
            group_by=("customer_city",), order_by=({"col": metric, "dir": "DESC"},), limit=20, needs_rag=False,
            chart_policy={"label": "region", "metrics": [metric], "unit_family": "single_metric", "type": "bar"},
            reason="Region/city ranking detected.", dimension="customer_city", rank_metric=metric,
        )

    # 14) High-level dashboard/KPI.
    if any(k in text for k in ("summary", "overview", "dashboard", "kpi", "total", "ملخص", "نظرة", "اجمالي", "إجمالي")):
        return QueryProfile(
            family="kpi", intent="aggregation", route="sql_only", difficulty="easy", confidence=0.80,
            template_key="dashboard_kpi", target_source="ml_output.mv_leakage_dashboard",
            metrics=("total_orders", "leakage_orders", "leakage_revenue", "leakage_rate_pct", "total_revenue"),
            limit=1, needs_rag=False, chart_policy={"type": "kpi"}, reason="High-level KPI/summary detected.",
        )

    # 15) Forecast/prediction wording with no month/quarter/year keyword
    # (those already matched the trend check above). Routes to the same
    # historical monthly view so the analytics interpreter can extrapolate
    # from real pre-computed trend stats instead of inventing numbers.
    # Checked last — every more specific dimension check above (seller,
    # payment, shipping, refund, product, region, scenario, campaign, web,
    # dual ranking, lookup, review) still wins if also present.
    if _has(text, _FORECAST_WORDS):
        return QueryProfile(
            family="trend", intent="trend_analysis", route="sql_only", difficulty="medium", confidence=0.84,
            template_key="monthly_leakage_trend", target_source="ml_output.mv_monthly_leakage",
            metrics=("total_orders", "leakage_orders", "leakage_rate_pct", "total_revenue", "revenue_at_risk", "total_profit"),
            group_by=("month",), order_by=({"col": "month", "dir": "ASC"},), limit=36, needs_rag=False,
            chart_policy={"label": "month_label", "metrics": ["revenue_at_risk"], "unit_family": "money", "type": "line"},
            reason="Forecast/prediction wording detected — routed to historical monthly trend for extrapolation.",
            rank_metric="revenue_at_risk",
        )

    return QueryProfile(
        family="general_query", intent="general_query", route="hybrid", difficulty="medium", confidence=0.25,
        reason="No high-confidence deterministic pattern matched; use LLM planner/generator.",
    )


def should_use_profile_fast_path(profile: QueryProfile) -> bool:
    """Conservative threshold: only high-confidence families bypass the LLM planner."""
    return profile.confidence >= 0.80 and profile.intent not in {"general_query"}


_NEXT_WORDS = ("next", "upcoming", "coming", "القادم", "القادمة", "المقبل", "المقبلة")


def is_forecast_question(message: str) -> bool:
    """
    True if the question asks for a forward-looking projection rather than a
    plain look back at history (e.g. "forecast next quarter revenue leakage",
    "predict leakage", "what will leakage look like next month").

    Used by the orchestrator to decide whether to compute and surface an
    actual statistical projection (see orchestrator._linear_forecast)
    alongside the historical trend data, instead of just describing the past.
    """
    text = (message or "").strip().lower()
    if not text:
        return False
    if _has(text, _FORECAST_WORDS):
        return True
    return _has(text, _NEXT_WORDS) and _has(text, _MONTH_WORDS)


def forecast_horizon_periods(message: str) -> int:
    """How many monthly periods ahead to project. mv_monthly_leakage is
    monthly, so 'quarter' = 3 months, 'year' = 12 months, default = 1 month."""
    text = (message or "").strip().lower()
    if any(w in text for w in ("quarter", "quarterly", "ربع")):
        return 3
    if any(w in text for w in ("year", "annual", "yearly", "سنة", "سنوي")):
        return 12
    return 1