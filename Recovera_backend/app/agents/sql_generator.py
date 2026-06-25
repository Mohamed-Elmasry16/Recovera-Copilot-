"""
sql_generator.py - SQL Generation Agent (GLM-4.5)
===================================================
Model: z-ai/glm-4.5-air:free via OpenRouter Key 2
Temperature: 0.0
Purpose: Generate optimized, safe PostgreSQL SQL ONLY

Rules:
  - NEVER hallucinate columns, tables, joins, metrics
  - Always add LIMIT
  - Avoid SELECT *
  - Use indexed fields
  - Prefer materialized views
  - Avoid Cartesian joins
  - Use generated date columns when available
  - Obey business rules and schema constraints

"""

import re
import logging
from typing import Optional

from app.core.multi_key_router import call_llm

log = logging.getLogger(__name__)

# ================================================================
# SQL GENERATION PROMPT
# ================================================================

SQL_WRITER_SYSTEM = """You are an expert PostgreSQL SQL writer for a Revenue Leakage Detection System.
Generate ONLY a single SELECT statement. No explanations, no markdown.

════════════════════════════════════════════
CRITICAL RULES (ABSOLUTE)
════════════════════════════════════════════

1. ONLY use existing tables/columns from schema
2. NEVER use INSERT, UPDATE, DELETE, DROP, ALTER
3. NEVER use SELECT *
4. ALWAYS use LIMIT unless pure aggregation with GROUP BY
5. All money values = EGP
6. Use ROUND(value, 2) for numeric outputs

════════════════════════════════════════════
CORE BUSINESS SEMANTICS (IMPORTANT FIX)
════════════════════════════════════════════

LEAKAGE IS NOT ONE METRIC

Interpret leakage based on context:

* leakage_rate_pct → RISK / QUALITY METRIC (percentage of problem)
* leakage_revenue → FINANCIAL IMPACT (money affected)
* leakage_orders → VOLUME (number of affected orders)

NEVER assume leakage means loss unless user explicitly says "money lost" or "financial loss".

════════════════════════════════════════════
LEAKAGE RANKING RULE (FIXED)
════════════════════════════════════════════

If user asks:

* risk / rate / percentage / أخطر / أعلى نسبة

→ use leakage_rate_pct DESC

If user asks:

* loss / losses / money lost / خسائر / خسارة مالية

→ use leakage_revenue DESC

If user asks:

* cases / orders / incidents

→ use leakage_orders DESC

════════════════════════════════════════════
DUAL RANKING RULE (FIXED CRITICAL LOGIC)
════════════════════════════════════════════

For questions asking TWO TOP ENTITIES (e.g. profit + loss):

STEP 1: Profit side
→ ALWAYS: total_revenue DESC

STEP 2: Loss side selection:

If wording is:

* "risk" → leakage_rate_pct
* "loss / money lost / خسائر" → leakage_revenue
* "cases" → leakage_orders

STEP 3: MUST build two independent rankings using ROW_NUMBER()
STEP 4: NEVER mix ranking metrics in same ORDER BY

════════════════════════════════════════════
ANTI-BIAS RULE (IMPORTANT FIX)
════════════════════════════════════════════

* NEVER default leakage to leakage_rate_pct in all cases
* NEVER default leakage_revenue as loss unless context matches financial wording
* Always follow user intent semantics

════════════════════════════════════════════
DATA QUALITY RULES
════════════════════════════════════════════

* Use mv_leakage_dashboard as primary source
* Use base tables only if MV missing columns
* ALWAYS use DISTINCT when joining orders

════════════════════════════════════════════
REVIEW / TEXT RULES
════════════════════════════════════════════

* NEVER use LIKE on review_comment
* ALL text search handled by RAG only

════════════════════════════════════════════
WEB ANALYTICS RULES
════════════════════════════════════════════

* LEFT JOIN always
* COUNT(DISTINCT session_id)
* Avoid SUM after joins unless aggregated before join

════════════════════════════════════════════
DUAL RANKING TEMPLATE (MANDATORY)
════════════════════════════════════════════

WITH base_stats AS (
SELECT
customer_city AS region,
ROUND(SUM(total_revenue),2) AS total_revenue,
COUNT(*) AS total_orders,
COUNT(CASE WHEN anomaly_flag=1 THEN 1 END) AS leakage_orders,
ROUND(SUM(CASE WHEN anomaly_flag=1 THEN total_revenue ELSE 0 END),2) AS leakage_revenue,
ROUND(
COUNT(CASE WHEN anomaly_flag=1 THEN 1 END)::decimal
/ NULLIF(COUNT(*),0)*100,2
) AS leakage_rate_pct
FROM ml_output.mv_leakage_dashboard
GROUP BY customer_city
),
top_profit AS (
SELECT *, 'top_profit' AS ranking_type,
ROW_NUMBER() OVER (ORDER BY total_revenue DESC) AS rank
FROM base_stats
),
top_loss AS (
SELECT *, 'top_loss' AS ranking_type,
ROW_NUMBER() OVER (ORDER BY leakage_revenue DESC) AS rank
FROM base_stats
)
SELECT * FROM top_profit WHERE rank=1
UNION ALL
SELECT * FROM top_loss WHERE rank=1;

════════════════════════════════════════════
OUTPUT RULE
════════════════════════════════════════════
Return ONLY SQL inside ```sql
"""

_WEB_TEMPLATES: dict[str, str] = {
    "duration": """\
─────────────────────────────────────────────────────────────
TEMPLATE A — web_analytics_dimension = "duration"
Use when: user asks whether longer session time leads to more purchases/profit.
─────────────────────────────────────────────────────────────
```sql
WITH session_durations AS (
    SELECT
        ws.session_id,
        ws.customer_id,
        ws.session_start,
        EXTRACT(EPOCH FROM (ws.session_end - ws.session_start)) / 60
            AS duration_minutes
    FROM marketing.website_sessions ws
    WHERE ws.session_end IS NOT NULL
),
bucketed AS (
    SELECT
        sd.*,
        CASE
            WHEN sd.duration_minutes < 2  THEN '1_bounce (< 2 min)'
            WHEN sd.duration_minutes < 5  THEN '2_short (2-5 min)'
            WHEN sd.duration_minutes < 15 THEN '3_medium (5-15 min)'
            WHEN sd.duration_minutes < 30 THEN '4_engaged (15-30 min)'
            ELSE                               '5_deep (30+ min)'
        END AS duration_bucket
    FROM session_durations sd
)
SELECT
    b.duration_bucket,
    COUNT(DISTINCT b.session_id)                               AS total_sessions,
    COUNT(DISTINCT o.order_id)                                 AS converted_orders,
    ROUND(
        COUNT(DISTINCT o.order_id)::decimal
        / NULLIF(COUNT(DISTINCT b.session_id), 0) * 100, 2
    )                                                          AS conversion_rate_pct,
    ROUND(COALESCE(
        SUM(o.total_revenue) / NULLIF(COUNT(DISTINCT b.session_id), 0)
    , 0), 2)                                                   AS revenue_per_session,
    ROUND(COALESCE(AVG(o.profit_margin), 0), 2)                AS avg_profit_margin
FROM bucketed b
LEFT JOIN ecommerce.orders o
    ON  o.customer_id            = b.customer_id
    AND o.order_purchase_timestamp >= b.session_start
    AND o.order_purchase_timestamp <  b.session_start + interval '7 days'
GROUP BY b.duration_bucket
ORDER BY b.duration_bucket;
```""",

    "monthly_trend": """\
─────────────────────────────────────────────────────────────
TEMPLATE B — web_analytics_dimension = "monthly_trend"  (DEFAULT)
Use when: user asks about general monthly session impact on sales/profit.
─────────────────────────────────────────────────────────────
```sql
SELECT
    date_trunc('month', ws.session_start)::date                AS month,
    COUNT(DISTINCT ws.session_id)                              AS total_sessions,
    COUNT(DISTINCT o.order_id)                                 AS converted_orders,
    ROUND(
        COUNT(DISTINCT o.order_id)::decimal
        / NULLIF(COUNT(DISTINCT ws.session_id), 0) * 100, 2
    )                                                          AS conversion_rate_pct,
    ROUND(COALESCE(
        SUM(o.total_revenue) / NULLIF(COUNT(DISTINCT ws.session_id), 0)
    , 0), 2)                                                   AS revenue_per_session,
    ROUND(COALESCE(AVG(o.profit_margin), 0), 2)                AS avg_profit_margin
FROM marketing.website_sessions ws
LEFT JOIN ecommerce.orders o
    ON  o.customer_id            = ws.customer_id
    AND o.order_purchase_timestamp >= ws.session_start
    AND o.order_purchase_timestamp <  ws.session_start + interval '7 days'
GROUP BY month
ORDER BY month DESC
LIMIT 24;
```""",

    "traffic_source": """\
─────────────────────────────────────────────────────────────
TEMPLATE C — web_analytics_dimension = "traffic_source"
Use when: user asks which traffic source drives more sales/profit.
─────────────────────────────────────────────────────────────
```sql
SELECT
    ws.traffic_source,
    COUNT(DISTINCT ws.session_id)                              AS total_sessions,
    COUNT(DISTINCT o.order_id)                                 AS converted_orders,
    ROUND(
        COUNT(DISTINCT o.order_id)::decimal
        / NULLIF(COUNT(DISTINCT ws.session_id), 0) * 100, 2
    )                                                          AS conversion_rate_pct,
    ROUND(COALESCE(
        SUM(o.total_revenue) / NULLIF(COUNT(DISTINCT ws.session_id), 0)
    , 0), 2)                                                   AS revenue_per_session,
    ROUND(COALESCE(AVG(o.profit_margin), 0), 2)                AS avg_profit_margin
FROM marketing.website_sessions ws
LEFT JOIN ecommerce.orders o
    ON  o.customer_id            = ws.customer_id
    AND o.order_purchase_timestamp >= ws.session_start
    AND o.order_purchase_timestamp <  ws.session_start + interval '7 days'
GROUP BY ws.traffic_source
ORDER BY revenue_per_session DESC
LIMIT 20;
```""",

    "device": """\
─────────────────────────────────────────────────────────────
TEMPLATE D — web_analytics_dimension = "device"
Use when: user asks whether mobile vs desktop users buy more.
─────────────────────────────────────────────────────────────
```sql
SELECT
    ci.device,
    COUNT(DISTINCT ci.interaction_id)                          AS total_events,
    COUNT(DISTINCT CASE WHEN ci.action_type IN ('purchase','checkout') THEN ci.customer_id END) AS converted_customers,
    ROUND(
        COUNT(DISTINCT CASE WHEN ci.action_type IN ('purchase','checkout') THEN ci.customer_id END)::decimal
        / NULLIF(COUNT(DISTINCT ci.customer_id), 0) * 100, 2
    )                                                          AS conversion_rate_pct,
    ROUND(COALESCE(
        SUM(o.total_revenue) / NULLIF(COUNT(DISTINCT ci.interaction_id), 0)
    , 0), 2)                                                   AS revenue_per_event,
    ROUND(COALESCE(AVG(o.profit_margin), 0), 2)                AS avg_profit_margin
FROM marketing.customer_interactions ci
LEFT JOIN ecommerce.orders o
    ON  o.customer_id              = ci.customer_id
    AND o.order_purchase_timestamp >= ci.interaction_date
    AND o.order_purchase_timestamp <  ci.interaction_date + interval '7 days'
WHERE LOWER(ci.channel) IN ('web','website','site')
GROUP BY ci.device
ORDER BY conversion_rate_pct DESC
LIMIT 10;
```""",
}


class SQLGenerator:
    """SQL generation agent using GLM-4.5 via OpenRouter."""

    def _deterministic_profile_sql(self, user_message: str, plan: dict) -> Optional[str]:
        """Deterministic SQL templates for high-confidence question families.

        These templates cover the common analytics families so we do not need to
        discover failures by manually testing hundreds of possible phrasings.
        LLM generation remains the fallback for genuinely novel questions.
        """
        template = (plan or {}).get("template_key") or ""
        metric = (plan or {}).get("primary_metric") or ""
        dimension = (plan or {}).get("primary_dimension") or ""
        limit = int((plan or {}).get("limit") or 20)
        limit = max(1, min(limit, 100))
        text = (user_message or "")

        # Avoid duplicating the existing specialized web injection path.  For web
        # queries the injected template is selected through web_analytics_dimension.
        if template.startswith("web_"):
            return None

        if template.startswith("dual_"):
            loss_metric = metric if metric in {"leakage_revenue", "leakage_rate_pct", "leakage_orders"} else "leakage_revenue"
            loss_label = "highest_leakage_rate" if loss_metric == "leakage_rate_pct" else "highest_leakage_orders" if loss_metric == "leakage_orders" else "highest_leakage_revenue"

            if dimension in {"customer_city", "region", ""}:
                return f"""
WITH base_stats AS (
    SELECT
        customer_city AS region,
        ROUND(SUM(total_revenue), 2) AS total_revenue,
        COUNT(*) AS total_orders,
        COUNT(*) FILTER (WHERE anomaly_flag = 1) AS leakage_orders,
        ROUND(SUM(CASE WHEN anomaly_flag = 1 THEN total_revenue ELSE 0 END), 2) AS leakage_revenue,
        ROUND(COUNT(*) FILTER (WHERE anomaly_flag = 1)::decimal / NULLIF(COUNT(*), 0) * 100, 2) AS leakage_rate_pct
    FROM ml_output.mv_leakage_dashboard
    WHERE customer_city IS NOT NULL
    GROUP BY customer_city
),
top_revenue AS (
    SELECT 'highest_revenue' AS ranking_type, ROW_NUMBER() OVER (ORDER BY total_revenue DESC) AS rank,
           region, total_revenue, total_orders, leakage_orders, leakage_revenue, leakage_rate_pct
    FROM base_stats
),
top_leakage AS (
    SELECT '{loss_label}' AS ranking_type, ROW_NUMBER() OVER (ORDER BY {loss_metric} DESC) AS rank,
           region, total_revenue, total_orders, leakage_orders, leakage_revenue, leakage_rate_pct
    FROM base_stats
)
SELECT * FROM top_revenue WHERE rank <= 5
UNION ALL
SELECT * FROM top_leakage WHERE rank <= 5
ORDER BY ranking_type, rank
LIMIT 10;
""".strip()

            if dimension in {"seller_id", "seller", "seller_name"}:
                return f"""
WITH base_stats AS (
    SELECT
        seller_id,
        COALESCE(seller_name, seller_id) AS seller_name,
        total_revenue,
        total_orders,
        leakage_orders,
        leakage_rate_pct,
        ROUND(total_revenue * leakage_rate_pct / 100, 2) AS leakage_revenue,
        avg_anomaly_score
    FROM ml_output.mv_seller_risk
),
top_revenue AS (
    SELECT 'highest_revenue' AS ranking_type, ROW_NUMBER() OVER (ORDER BY total_revenue DESC) AS rank, *
    FROM base_stats
),
top_leakage AS (
    SELECT '{loss_label}' AS ranking_type, ROW_NUMBER() OVER (ORDER BY {loss_metric} DESC) AS rank, *
    FROM base_stats
)
SELECT * FROM top_revenue WHERE rank <= 5
UNION ALL
SELECT * FROM top_leakage WHERE rank <= 5
ORDER BY ranking_type, rank
LIMIT 10;
""".strip()

            if dimension in {"scenario"}:
                order_metric = "revenue_at_risk" if loss_metric == "leakage_revenue" else "total_orders"
                return f"""
WITH base_stats AS (
    SELECT
        scenario,
        total_orders,
        ROUND(revenue_at_risk, 2) AS revenue_at_risk,
        ROUND(avg_anomaly_score, 4) AS avg_anomaly_score,
        ROUND(avg_profit_margin, 4) AS avg_profit_margin
    FROM ml_output.mv_leakage_by_scenario
),
top_revenue AS (
    SELECT 'highest_revenue_at_risk' AS ranking_type, ROW_NUMBER() OVER (ORDER BY revenue_at_risk DESC) AS rank, *
    FROM base_stats
),
top_leakage AS (
    SELECT '{loss_label}' AS ranking_type, ROW_NUMBER() OVER (ORDER BY {order_metric} DESC) AS rank, *
    FROM base_stats
)
SELECT * FROM top_revenue WHERE rank <= 5
UNION ALL
SELECT * FROM top_leakage WHERE rank <= 5
ORDER BY ranking_type, rank
LIMIT 10;
""".strip()

        if template == "dashboard_kpi":
            return """
SELECT
    COUNT(*) AS total_orders,
    COUNT(*) FILTER (WHERE anomaly_flag = 1) AS leakage_orders,
    ROUND(COUNT(*) FILTER (WHERE anomaly_flag = 1)::decimal / NULLIF(COUNT(*), 0) * 100, 2) AS leakage_rate_pct,
    ROUND(SUM(total_revenue), 2) AS total_revenue,
    ROUND(SUM(CASE WHEN anomaly_flag = 1 THEN total_revenue ELSE 0 END), 2) AS leakage_revenue,
    ROUND(SUM(total_profit), 2) AS total_profit,
    ROUND(AVG(profit_margin), 4) AS avg_profit_margin
FROM ml_output.mv_leakage_dashboard;
""".strip()

        if template == "monthly_leakage_trend":
            order_metric = metric if metric in {"total_revenue", "revenue_at_risk", "leakage_rate_pct", "total_profit"} else "month"
            return f"""
SELECT
    month,
    month_label,
    total_orders,
    leakage_orders,
    leakage_rate_pct,
    ROUND(total_revenue, 2) AS total_revenue,
    ROUND(revenue_at_risk, 2) AS revenue_at_risk,
    ROUND(total_profit, 2) AS total_profit,
    ROUND(avg_profit_margin, 4) AS avg_profit_margin
FROM ml_output.mv_monthly_leakage
ORDER BY month ASC
LIMIT {limit};
""".strip()

        if template == "scenario_ranking":
            order_metric = metric if metric in {"total_orders", "revenue_at_risk", "avg_anomaly_score", "avg_profit_margin"} else "revenue_at_risk"
            return f"""
SELECT
    scenario,
    total_orders,
    ROUND(revenue_at_risk, 2) AS revenue_at_risk,
    ROUND(avg_anomaly_score, 4) AS avg_anomaly_score,
    ROUND(avg_profit_margin, 4) AS avg_profit_margin
FROM ml_output.mv_leakage_by_scenario
ORDER BY {order_metric} DESC
LIMIT {limit};
""".strip()

        if template == "seller_risk_ranking":
            order_metric = metric if metric in {"leakage_rate_pct", "leakage_orders", "total_revenue", "avg_anomaly_score", "total_orders"} else "leakage_rate_pct"
            return f"""
SELECT
    seller_id,
    seller_name,
    seller_city,
    seller_rating,
    return_rate,
    payment_disputes,
    total_orders,
    leakage_orders,
    leakage_rate_pct,
    ROUND(total_revenue, 2) AS total_revenue,
    ROUND(avg_anomaly_score, 4) AS avg_anomaly_score
FROM ml_output.mv_seller_risk
ORDER BY {order_metric} DESC NULLS LAST
LIMIT {limit};
""".strip()

        if template == "region_ranking":
            order_metric = metric if metric in {"total_revenue", "leakage_revenue", "leakage_rate_pct", "leakage_orders", "total_orders"} else "leakage_revenue"
            return f"""
SELECT
    customer_city AS region,
    COUNT(*) AS total_orders,
    COUNT(*) FILTER (WHERE anomaly_flag = 1) AS leakage_orders,
    ROUND(SUM(total_revenue), 2) AS total_revenue,
    ROUND(SUM(CASE WHEN anomaly_flag = 1 THEN total_revenue ELSE 0 END), 2) AS leakage_revenue,
    ROUND(COUNT(*) FILTER (WHERE anomaly_flag = 1)::decimal / NULLIF(COUNT(*), 0) * 100, 2) AS leakage_rate_pct,
    ROUND(AVG(profit_margin), 4) AS avg_profit_margin
FROM ml_output.mv_leakage_dashboard
WHERE customer_city IS NOT NULL
GROUP BY customer_city
ORDER BY {order_metric} DESC NULLS LAST
LIMIT {limit};
""".strip()

        if template == "payment_leakage":
            return f"""
SELECT
    payment_status,
    payment_type,
    COUNT(*) AS total_orders,
    COUNT(*) FILTER (WHERE anomaly_flag = 1) AS leakage_orders,
    ROUND(SUM(CASE WHEN anomaly_flag = 1 THEN total_revenue ELSE 0 END), 2) AS leakage_revenue,
    ROUND(COUNT(*) FILTER (WHERE anomaly_flag = 1)::decimal / NULLIF(COUNT(*), 0) * 100, 2) AS leakage_rate_pct,
    COUNT(*) FILTER (WHERE seller_paid_twice = TRUE) AS seller_paid_twice_orders
FROM ml_output.mv_leakage_dashboard
GROUP BY payment_status, payment_type
ORDER BY leakage_revenue DESC NULLS LAST
LIMIT {limit};
""".strip()

        if template == "shipping_leakage":
            return f"""
SELECT
    carrier,
    shipping_status,
    COUNT(*) AS total_orders,
    COUNT(*) FILTER (WHERE anomaly_flag = 1) AS leakage_orders,
    ROUND(AVG(shipping_delay_days), 2) AS avg_delay_days,
    ROUND(SUM(CASE WHEN anomaly_flag = 1 THEN total_revenue ELSE 0 END), 2) AS leakage_revenue,
    ROUND(COUNT(*) FILTER (WHERE anomaly_flag = 1)::decimal / NULLIF(COUNT(*), 0) * 100, 2) AS leakage_rate_pct,
    COUNT(*) FILTER (WHERE fee_calculation_error = TRUE) AS fee_error_orders
FROM ml_output.mv_leakage_dashboard
GROUP BY carrier, shipping_status
ORDER BY leakage_revenue DESC NULLS LAST
LIMIT {limit};
""".strip()

        if template == "refund_analysis":
            return f"""
SELECT
    r.refund_reason,
    COUNT(*) AS refund_count,
    ROUND(SUM(r.refund_amount), 2) AS refund_amount,
    COUNT(*) FILTER (WHERE r.duplicate_refund = TRUE) AS duplicate_refund_count,
    COUNT(*) FILTER (WHERE r.processed_before_cancel = TRUE) AS processed_before_cancel_count,
    ROUND(AVG(o.profit_margin), 4) AS avg_profit_margin
FROM ecommerce.refunds r
LEFT JOIN ecommerce.orders o ON o.order_id = r.order_id
GROUP BY r.refund_reason
ORDER BY refund_amount DESC NULLS LAST
LIMIT {limit};
""".strip()

        if template == "product_category_analysis":
            order_metric = metric if metric in {"total_revenue", "leakage_revenue", "total_orders", "avg_discount_pct"} else "total_revenue"
            return f"""
SELECT
    p.product_category,
    COUNT(DISTINCT oi.order_id) AS total_orders,
    ROUND(SUM(oi.price_after_discount), 2) AS total_revenue,
    ROUND(SUM(CASE WHEN oas.anomaly_flag = 1 THEN oi.price_after_discount ELSE 0 END), 2) AS leakage_revenue,
    ROUND(AVG(oi.item_discount_pct), 4) AS avg_discount_pct,
    ROUND(AVG(o.profit_margin), 4) AS avg_profit_margin
FROM ecommerce.order_items oi
JOIN ecommerce.products p ON p.product_id = oi.product_id
JOIN ecommerce.orders o ON o.order_id = oi.order_id
LEFT JOIN ml_output.order_anomaly_scores oas ON oas.order_id = oi.order_id
GROUP BY p.product_category
ORDER BY {order_metric} DESC NULLS LAST
LIMIT {limit};
""".strip()

        if template == "campaign_performance":
            # Include ROI when campaign budget is populated. NULLIF avoids division by zero.
            order_metric = metric if metric in {"total_revenue", "avg_profit_margin_pct", "order_count", "roi_pct"} else "total_revenue"
            return f"""
SELECT
    mc.campaign_id,
    mc.campaign_name,
    mc.channel,
    mc.campaign_type,
    COUNT(DISTINCT ca.order_id) AS order_count,
    ROUND(SUM(o.total_revenue), 2) AS total_revenue,
    ROUND(AVG(o.profit_margin) * 100, 2) AS avg_profit_margin_pct,
    ROUND(mc.budget, 2) AS budget,
    ROUND((SUM(o.total_revenue) - mc.budget) / NULLIF(mc.budget, 0) * 100, 2) AS roi_pct
FROM marketing.campaign_attribution ca
JOIN marketing.marketing_campaigns mc ON mc.campaign_id = ca.campaign_id
JOIN ecommerce.orders o ON o.order_id = ca.order_id
GROUP BY mc.campaign_id, mc.campaign_name, mc.channel, mc.campaign_type, mc.budget
ORDER BY {order_metric} DESC NULLS LAST
LIMIT {limit};
""".strip()

        if template == "review_financial_impact":
            return f"""
SELECT
    sentiment,
    rating,
    COUNT(*) AS reviewed_orders,
    COUNT(*) FILTER (WHERE anomaly_flag = 1) AS leakage_orders,
    ROUND(SUM(total_revenue), 2) AS total_revenue,
    ROUND(SUM(CASE WHEN anomaly_flag = 1 THEN total_revenue ELSE 0 END), 2) AS leakage_revenue,
    ROUND(AVG(profit_margin), 4) AS avg_profit_margin
FROM ml_output.mv_leakage_dashboard
WHERE rating IS NOT NULL OR sentiment IS NOT NULL
GROUP BY sentiment, rating
ORDER BY leakage_revenue DESC NULLS LAST
LIMIT {limit};
""".strip()

        if template == "lookup_order_or_entity":
            m = re.search(r"\b[a-fA-F0-9]{24,36}\b", text)
            if not m:
                return None
            value = m.group(0).replace("'", "''")
            return f"""
SELECT
    order_id,
    customer_id,
    customer_name,
    customer_city,
    order_status,
    order_purchase_timestamp,
    total_revenue,
    total_profit,
    profit_margin,
    payment_status,
    payment_type,
    shipping_status,
    carrier,
    rating,
    sentiment,
    anomaly_flag,
    risk_tier,
    leakage_scenarios,
    leakage_reason
FROM ml_output.mv_leakage_dashboard
WHERE order_id = '{value}' OR customer_id = '{value}'
ORDER BY order_purchase_timestamp DESC NULLS LAST
LIMIT {limit};
""".strip()

        return None


    def _deterministic_dual_ranking_sql(self, user_message: str, plan: dict) -> Optional[str]:
        """Return a deterministic template for dual-ranking questions.

        LLMs often collapse a dual-ranking request into a single ORDER BY.  For
        questions like "highest revenue area and highest loss area" this template
        is safer than asking the model again: two independent ROW_NUMBER rankings,
        then UNION ALL.
        """
        if (plan or {}).get("intent") != "dual_ranking":
            return None

        text = (user_message or "").lower()
        group_by = " ".join(str(x).lower() for x in (plan or {}).get("group_by", []) or [])

        # Region/city wording in English and Arabic.  This is the most common
        # dual-ranking request in this project and matches the screenshot issue.
        region_words = ("region", "area", "city", "governorate", "منطقة", "مناطق", "مدينة", "مدن", "محافظة", "محافظات")
        if any(w in text for w in region_words) or "customer_city" in group_by:
            profit_words = ("profit", "profits", "profitable", "margin", "ربح", "أرباح", "ارباح", "ربحية")
            loss_words = ("loss", "losses", "خسارة", "خسائر", "خسرت")
            if any(w in text for w in profit_words) and any(w in text for w in loss_words):
                return f"""
WITH base_stats AS (
    SELECT
        customer_city AS region,
        ROUND(SUM(total_revenue), 2) AS total_revenue,
        COUNT(*) AS total_orders,
        COUNT(*) FILTER (WHERE total_profit < 0) AS loss_orders,
        ROUND(SUM(total_profit), 2) AS net_profit,
        ROUND(SUM(CASE WHEN total_profit > 0 THEN total_profit ELSE 0 END), 2) AS gross_profit,
        ROUND(ABS(SUM(CASE WHEN total_profit < 0 THEN total_profit ELSE 0 END)), 2) AS gross_loss,
        ROUND(AVG(profit_margin) * 100, 2) AS avg_profit_margin_pct
    FROM ml_output.mv_leakage_dashboard
    WHERE customer_city IS NOT NULL
    GROUP BY customer_city
),
top_profit AS (
    SELECT
        'highest_profit' AS ranking_type,
        ROW_NUMBER() OVER (ORDER BY gross_profit DESC) AS rank,
        region, total_revenue, total_orders, loss_orders, net_profit, gross_profit, gross_loss, avg_profit_margin_pct
    FROM base_stats
),
top_loss AS (
    SELECT
        'highest_loss' AS ranking_type,
        ROW_NUMBER() OVER (ORDER BY gross_loss DESC) AS rank,
        region, total_revenue, total_orders, loss_orders, net_profit, gross_profit, gross_loss, avg_profit_margin_pct
    FROM base_stats
)
SELECT * FROM top_profit WHERE rank <= 5
UNION ALL
SELECT * FROM top_loss WHERE rank <= 5
ORDER BY ranking_type, rank
LIMIT 10;
""".strip()

            # Financial loss wording should rank by leakage_revenue; risk wording
            # should rank by leakage_rate_pct.  Arabic "خسائر" is financial.
            risk_words = ("risk", "rate", "percentage", "أخطر", "نسبة", "مخاطر")
            loss_metric = "leakage_rate_pct" if any(w in text for w in risk_words) else "leakage_revenue"
            loss_label = "highest_leakage_rate" if loss_metric == "leakage_rate_pct" else "highest_leakage_revenue"
            return f"""
WITH base_stats AS (
    SELECT
        customer_city AS region,
        ROUND(SUM(total_revenue), 2) AS total_revenue,
        COUNT(*) AS total_orders,
        COUNT(*) FILTER (WHERE anomaly_flag = 1) AS leakage_orders,
        ROUND(SUM(CASE WHEN anomaly_flag = 1 THEN total_revenue ELSE 0 END), 2) AS leakage_revenue,
        ROUND(
            COUNT(*) FILTER (WHERE anomaly_flag = 1)::decimal
            / NULLIF(COUNT(*), 0) * 100, 2
        ) AS leakage_rate_pct
    FROM ml_output.mv_leakage_dashboard
    WHERE customer_city IS NOT NULL
    GROUP BY customer_city
),
top_revenue AS (
    SELECT
        'highest_revenue' AS ranking_type,
        ROW_NUMBER() OVER (ORDER BY total_revenue DESC) AS rank,
        region,
        total_revenue,
        total_orders,
        leakage_orders,
        leakage_revenue,
        leakage_rate_pct
    FROM base_stats
),
top_leakage AS (
    SELECT
        '{loss_label}' AS ranking_type,
        ROW_NUMBER() OVER (ORDER BY {loss_metric} DESC) AS rank,
        region,
        total_revenue,
        total_orders,
        leakage_orders,
        leakage_revenue,
        leakage_rate_pct
    FROM base_stats
)
SELECT * FROM top_revenue WHERE rank <= 5
UNION ALL
SELECT * FROM top_leakage WHERE rank <= 5
ORDER BY ranking_type, rank
LIMIT 10;
""".strip()

        return None

    async def generate(
        self,
        user_message: str,
        plan: dict,
        compressed_context: str,
    ) -> tuple[Optional[str], int, int]:
        """
        Generate SQL from user message and plan.
        Returns (sql, input_tokens, output_tokens).

        BUG-01 FIX: Inject only the one matching web analytics template (−875 tokens for non-web).
        BUG-02 FIX: sql_examples param removed — examples come exclusively from compressed_context.
        """
        deterministic_sql = self._deterministic_profile_sql(user_message, plan)
        if deterministic_sql:
            log.info(f"[sql_generator] Using deterministic profile template: {plan.get('template_key', '-')}")
            return deterministic_sql, 0, 0

        deterministic_sql = self._deterministic_dual_ranking_sql(user_message, plan)
        if deterministic_sql:
            log.info("[sql_generator] Using deterministic dual-ranking template")
            return deterministic_sql, 0, 0

        # BUG-01: Inject the single matching web analytics template into user prompt only.
        # For non-web queries this adds 0 tokens. For web queries it adds ~220 tokens (one template).
        web_dim = plan.get("web_analytics_dimension", "")
        template_block = ""
        if web_dim and web_dim in _WEB_TEMPLATES:
            template_block = (
                f"\n\n## USE THIS TEMPLATE (dimension={web_dim}):\n"
                f"{_WEB_TEMPLATES[web_dim]}\n"
                f"Adapt it to the plan. Do not switch templates."
            )

        # CAMPAIGN ANALYSIS: inject the mandatory campaign profitability template.
        # This prevents GLM-4.5 from hallucinating a direct marketing_campaigns → orders join
        # (which doesn't exist). The template is injected only for campaign_analysis intent.
        if plan.get("intent") == "campaign_analysis" and not template_block:
            template_block = (
                "\n\n## USE THIS TEMPLATE (intent=campaign_analysis):\n"
                "```sql\n"
                "SELECT\n"
                "    mc.campaign_id,\n"
                "    mc.campaign_name,\n"
                "    mc.channel,\n"
                "    SUM(o.total_revenue)                        AS total_revenue,\n"
                "    ROUND(AVG(o.profit_margin) * 100, 2)        AS avg_profit_margin_pct,\n"
                "    COUNT(DISTINCT o.order_id)                  AS order_count\n"
                "FROM marketing.campaign_attribution ca\n"
                "JOIN marketing.marketing_campaigns mc ON ca.campaign_id = mc.campaign_id\n"
                "JOIN ecommerce.orders o               ON ca.order_id    = o.order_id\n"
                "GROUP BY mc.campaign_id, mc.campaign_name, mc.channel\n"
                "ORDER BY total_revenue DESC\n"
                "LIMIT 10;\n"
                "```\n"
                "Adapt ORDER BY and WHERE filters as needed. Do NOT change the JOIN structure."
            )

        # Build prompt
        system_msg = SQL_WRITER_SYSTEM
        if compressed_context:
            system_msg += f"\n\n{compressed_context}"

        web_dim_line = f"\n- Web analytics dimension: {web_dim}" if web_dim else ""

        user_msg = f"""Question: {user_message}

Plan:
- Target: {plan.get('target_source', '')}
- Metrics: {', '.join(plan.get('metrics', []))}
- Time grain: {plan.get('time_grain', '')}
- Filters: {plan.get('filters', [])}
- Group by: {plan.get('group_by', [])}
- Order by: {plan.get('order_by', [])}
- Limit: {plan.get('limit', 20)}{web_dim_line}{template_block}

Write the SQL following the plan exactly."""

        messages = [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ]

        content, in_tok, out_tok = await call_llm(
            agent="sql_generator",
            messages=messages,
            temperature=0.0,
            max_tokens=1024,
        )

        sql = self._extract_sql(content)

        # FIX: If SQL extraction failed, retry with an explicit instruction.
        # Uses system_msg and user_msg from this scope (generate), not fix().
        if not sql:
            log.warning("[sql_generator] generate(): first attempt returned no SQL, retrying...")
            retry_messages = [
                {
                    "role": "system",
                    "content": system_msg + "\n\nIMPORTANT: You MUST return ONLY a valid SQL SELECT statement inside ```sql ... ``` blocks.",
                },
                {
                    "role": "user",
                    "content": user_msg + "\n\nReturn ONLY the SQL query, nothing else.",
                },
            ]
            content2, in_tok2, out_tok2 = await call_llm(
                agent="sql_generator",
                messages=retry_messages,
                temperature=0.0,
                max_tokens=1024,
            )
            sql = self._extract_sql(content2)
            in_tok += in_tok2
            out_tok += out_tok2

        return sql, in_tok, out_tok

    async def fix(
        self,
        original_sql: str,
        error_message: str,
        validation_issues: list[str],
        compressed_context: str = "",
        plan: dict | None = None,
    ) -> tuple[Optional[str], int, int]:
        """
        Fix SQL after validation or execution errors.

        BUG-04 FIX: Receives the original plan so the model fixes toward the correct
        target/metrics/group-by instead of guessing from the broken SQL alone.
        """
        # BUG-04: Inject plan constraints so the model corrects toward ground truth
        plan_context = ""
        if plan:
            plan_context = (
                f"\nRequired target: {plan.get('target_source', 'unknown')}"
                f"\nRequired metrics: {', '.join(plan.get('metrics', []))}"
                f"\nRequired group by: {', '.join(plan.get('group_by', []))}"
                f"\nRequired limit: {plan.get('limit', 20)}"
                f"\nRequired filters: {plan.get('filters', [])}"
            )

        fix_prompt = f"""Fix this SQL query.{plan_context}

Original SQL:
```sql
{original_sql}
```

"""
        if error_message:
            fix_prompt += f"Execution Error: {error_message}\n\n"
        if validation_issues:
            fix_prompt += "Validation Issues:\n" + "\n".join(f"- {i}" for i in validation_issues) + "\n\n"

        fix_prompt += "Return ONLY the fixed SQL inside ```sql ... ``` block."

        if compressed_context:
            fix_prompt += f"\n\nSchema context:\n{compressed_context}"

        messages = [
            {"role": "system", "content": SQL_WRITER_SYSTEM},
            {"role": "user", "content": fix_prompt},
        ]

        content, in_tok, out_tok = await call_llm(
            agent="sql_generator",
            messages=messages,
            temperature=0.0,
            max_tokens=1024,
        )

        sql = self._extract_sql(content)

        # FIX [Bug #8]: retry now uses SQL_WRITER_SYSTEM and fix_prompt,
        # both of which are defined in this scope — no NameError possible.
        if not sql:
            log.warning("[sql_generator] fix(): first attempt returned no SQL, retrying...")
            retry_messages = [
                {
                    "role": "system",
                    "content": SQL_WRITER_SYSTEM + "\n\nIMPORTANT: You MUST return ONLY a valid SQL SELECT statement inside ```sql ... ``` blocks.",
                },
                {
                    "role": "user",
                    "content": fix_prompt + "\n\nReturn ONLY the SQL query, nothing else.",
                },
            ]
            content2, in_tok2, out_tok2 = await call_llm(
                agent="sql_generator",
                messages=retry_messages,
                temperature=0.0,
                max_tokens=1024,
            )
            sql = self._extract_sql(content2)
            in_tok += in_tok2
            out_tok += out_tok2

        return sql, in_tok, out_tok

    def _extract_sql(self, text: str) -> Optional[str]:
        """Extract SQL from markdown code block."""
        # Try ```sql block
        match = re.search(r"```sql\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
        if match:
            sql = match.group(1).strip()
            return self._sanitize_sql(sql)

        # Try ``` block without sql tag
        match = re.search(r"```\s*((?:SELECT|WITH).*?)```", text, re.DOTALL | re.IGNORECASE)
        if match:
            sql = match.group(1).strip()
            return self._sanitize_sql(sql)

        # Try bare SELECT or WITH
        match = re.search(r"((?:SELECT|WITH)\s+.*)", text, re.DOTALL | re.IGNORECASE)
        if match:
            sql = match.group(1).strip()
            return self._sanitize_sql(sql)

        return None

    def _sanitize_sql(self, sql: str) -> Optional[str]:
        """Basic safety checks on extracted SQL."""
        if not sql:
            return None

        # Must start with SELECT or WITH (for CTEs)
        if not re.match(r"^\s*(SELECT|WITH)", sql, re.IGNORECASE):
            return None

        # Block dangerous statements
        dangerous = re.compile(
            r"^\s*(INSERT|UPDATE|DELETE|DROP|TRUNCATE|ALTER|CREATE|GRANT|REVOKE)",
            re.IGNORECASE,
        )
        if dangerous.search(sql):
            return None

        # Warn if text-pattern review search detected (Bug #5 regression guard)
        if re.search(r"review_comment\s+ILIKE", sql, re.IGNORECASE):
            log.warning(
                "[sql_generator] Generated SQL uses review_comment ILIKE — "
                "this violates Bug #5 fix. SQL may return 0 rows. "
                "The planner should have routed via refunds/order_leakage_reasons."
            )

        # [Bug #10] Warn if a web analytics query uses plain SUM without division
        # (fan-out risk from the JOIN on customer_id)
        if re.search(r"website_sessions", sql, re.IGNORECASE):
            if re.search(r"SUM\s*\(\s*o\.total_revenue\s*\)", sql, re.IGNORECASE):
                if not re.search(r"NULLIF\s*\(COUNT", sql, re.IGNORECASE):
                    log.warning(
                        "[sql_generator] Web analytics SQL uses SUM(total_revenue) without "
                        "dividing by session count — possible fan-out from customer_id JOIN. "
                        "Consider: SUM(revenue) / NULLIF(COUNT(DISTINCT session_id), 0)"
                    )

        return sql


# Global instance
sql_generator = SQLGenerator()


async def generate_sql(
    user_message: str,
    plan: dict,
    compressed_context: str,
) -> tuple[Optional[str], int, int]:
    """Public API for SQL generation. BUG-02: sql_examples removed."""
    return await sql_generator.generate(user_message, plan, compressed_context)


async def fix_sql(
    original_sql: str,
    error_message: str,
    validation_issues: list[str],
    compressed_context: str = "",
    plan: dict | None = None,
) -> tuple[Optional[str], int, int]:
    """Public API for SQL fixing. BUG-04: plan added."""
    return await sql_generator.fix(original_sql, error_message, validation_issues, compressed_context, plan)