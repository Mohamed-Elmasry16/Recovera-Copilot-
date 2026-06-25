"""
context_compression.py - Context Compression Layer
==================================================
CRITICAL COMPONENT: Compresses retrieved context before sending to LLMs.

Rules:
  - NEVER send full schema
  - NEVER send raw reviews
  - NEVER dump entire documentation
  - Target: 2k-5k tokens maximum per prompt
  - Keep only relevant tables, columns, business rules
  - Summarize review insights into structured bullets
  - Include only useful SQL examples

Strategies:
  1. Table filtering - only tables mentioned in plan or RAG docs
  2. Column filtering - only columns relevant to the query
  3. Business rule summarization - extract only matching rules
  4. Review insight compression - aggregate sentiment themes
  5. SQL example deduplication - keep top-2 most relevant

FIXES:
  - [Bug #7] _compress_reviews(): review_analysis now receives the same full-excerpt
             treatment as sentiment_analysis (10 excerpts × 800 chars). Previously
             it fell into a weaker "is_review_query" branch (5 excerpts × 400 chars),
             starving the analytics agent of review text.
  - [Bug #6] _aggressive_truncate(): now accepts an `intent` parameter. For
             review/sentiment intents the priority order is REVERSED — review_insights
             is protected and sql_examples / schema_summary are truncated first.
             Previously review_insights was always the FIRST thing dropped, causing
             "no data" answers even when 500 reviews had been retrieved.
  - compress(): passes intent through to _aggressive_truncate() so the priority
               decision is intent-aware end-to-end.
"""

import re
import json
import logging
from typing import Optional
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# ================================================================
# COMPRESSED CONTEXT RESULT
# ================================================================

@dataclass
class CompressedContext:
    """Structured compressed context ready for LLM prompt injection."""
    schema_summary: str = ""
    business_rules_summary: str = ""
    review_insights: str = ""
    sql_examples: str = ""
    enum_reference: str = ""
    token_estimate: int = 0
    sources_used: list[str] = field(default_factory=list)

    def to_planner_prompt(self) -> str:
        """Format for planner agent (minimal context)."""
        parts = []
        if self.schema_summary:
            parts.append(f"## RELEVANT SCHEMA\n{self.schema_summary}")
        if self.business_rules_summary:
            parts.append(f"## BUSINESS RULES\n{self.business_rules_summary}")
        if self.enum_reference:
            parts.append(f"## ENUM VALUES\n{self.enum_reference}")
        return "\n\n".join(parts)

    def to_sql_writer_prompt(self) -> str:
        """Format for SQL writer agent (full context). Use to_sql_context() instead for SQL generation."""
        parts = []
        if self.schema_summary:
            parts.append(f"## RELEVANT SCHEMA\n{self.schema_summary}")
        if self.business_rules_summary:
            parts.append(f"## BUSINESS RULES & LEAKAGE DEFINITIONS\n{self.business_rules_summary}")
        if self.enum_reference:
            parts.append(f"## VALID ENUM VALUES\n{self.enum_reference}")
        if self.sql_examples:
            parts.append(f"## REFERENCE SQL EXAMPLES\n{self.sql_examples}")
        if self.review_insights:
            parts.append(f"## REVIEW INSIGHTS\n{self.review_insights}")
        return "\n\n".join(parts)

    def to_sql_context(self) -> str:
        """
        For the SQL generator ONLY.
        BUG-03 FIX: Identical to to_sql_writer_prompt() but NEVER includes review_insights.
        Review text is for the analytics interpreter, not the SQL writer.
        Injecting 1,500 tokens of review text into GLM-4.5 causes ILIKE regressions.
        """
        parts = []
        if self.schema_summary:
            parts.append(f"## RELEVANT SCHEMA\n{self.schema_summary}")
        if self.business_rules_summary:
            parts.append(f"## BUSINESS RULES & LEAKAGE DEFINITIONS\n{self.business_rules_summary}")
        if self.enum_reference:
            parts.append(f"## VALID ENUM VALUES\n{self.enum_reference}")
        if self.sql_examples:
            parts.append(f"## REFERENCE SQL EXAMPLES\n{self.sql_examples}")
        # review_insights intentionally excluded
        return "\n\n".join(parts)

    def to_analytics_prompt(self, intent: str = "") -> str:
        """
        Format for analytics interpreter (business-focused).
        BUG-NEW-03 FIX: For web_analytics intent, leakage business rules are irrelevant
        and produce hallucinated leakage-rule contamination in session/conversion answers.
        """
        parts = []
        if self.business_rules_summary:
            # For web analytics, leakage threshold rules are not applicable
            if intent not in ("web_analytics",):
                parts.append(f"## LEAKAGE & BUSINESS CONTEXT\n{self.business_rules_summary}")
        if self.review_insights:
            parts.append(f"## CUSTOMER FEEDBACK THEMES\n{self.review_insights}")
        return "\n\n".join(parts)


# ================================================================
# VERIFIED SCHEMA FRAGMENTS (from DATABASE_DOCUMENTATION.md)
# ================================================================

# Materialized Views - USE THESE FIRST
MV_SCHEMAS = {
    "ml_output.mv_leakage_dashboard": """
ml_output.mv_leakage_dashboard (~298K rows) - PREFERRED for order-level questions
Columns: order_id, customer_id, customer_name, customer_city, customer_segment,
  order_status, order_purchase_timestamp, order_month, order_quarter, order_year,
  total_revenue, total_profit, profit_margin, avg_discount_pct, shipping_delay_days,
  invoice_available, inventory_mismatch, payment_status, payment_type, payment_value,
  seller_paid_twice, shipping_status, shipping_fee_charged, actual_logistics_cost,
  fee_calculation_error, fee_to_cost_ratio, carrier, rating, sentiment, review_comment,
  ensemble_score, anomaly_flag, risk_tier, leakage_scenarios, leakage_reason
""",
    "ml_output.mv_monthly_leakage": """
ml_output.mv_monthly_leakage (~24 rows) - PREFERRED for monthly/quarterly trends
Columns: month, month_label, total_orders, leakage_orders, leakage_rate_pct,
  total_revenue, revenue_at_risk, total_profit, avg_profit_margin
""",
    "ml_output.mv_seller_risk": """
ml_output.mv_seller_risk (~5K rows) - PREFERRED for seller analysis
Columns: seller_id, seller_name, seller_city, seller_rating, return_rate,
  payment_disputes, total_orders, leakage_orders, leakage_rate_pct,
  total_revenue, avg_anomaly_score
""",
    "ml_output.mv_leakage_by_scenario": """
ml_output.mv_leakage_by_scenario (~20 rows) - PREFERRED for scenario comparison
Columns: scenario, total_orders, revenue_at_risk, avg_anomaly_score, avg_profit_margin
""",
}

# Base Tables - only when MV doesn't have needed column
BASE_SCHEMAS = {
    "ecommerce.orders": """
ecommerce.orders (~298K rows) - Core fact table. Use order_month for monthly GROUP BY (GENERATED)
Columns: order_id, customer_id, order_status, order_purchase_timestamp, order_approved_at,
  order_delivered_carrier_date, order_delivered_customer_date, order_estimated_delivery_date,
  shipping_delay_days, invoice_available, avg_discount_pct, total_revenue, total_logistics,
  total_freight, item_count, total_profit, profit_margin, inventory_mismatch, payment_status,
  order_month [GENERATED], order_quarter [GENERATED], order_year [GENERATED]
""",
    "ecommerce.customers": """
ecommerce.customers (~253K rows) - Customer profiles
Columns: customer_id, customer_unique_id, customer_zip_code_prefix, customer_city,
  customer_name, lifetime_value, segment ENUM('Low Value','Mid Value','High Value'),
  churn_risk ENUM('High','Medium','Low'), total_orders, avg_order_value, is_verified
""",
    "ecommerce.payments": """
ecommerce.payments (~297K rows) - Payment records. Multiple rows per order for installments.
Columns: id, order_id, payment_sequential, payment_type, payment_installments, payment_value,
  payment_status, seller_paid_twice
CRITICAL: ALWAYS filter AND payment_sequential = 1 for primary payment
""",
    "ecommerce.order_items": """
ecommerce.order_items (~435K rows) - Line items per order
Columns: id, order_id, order_item_id, product_id, seller_id, price, freight_value,
  item_discount_pct, price_after_discount, logistics_cost
""",
    "ecommerce.sellers": """
ecommerce.sellers (~5K rows) - Seller profiles
Columns: seller_id, seller_zip_code_prefix, seller_city, seller_name, seller_rating,
  total_orders, return_rate, payment_disputes, is_verified, dq_name_cleaned, dq_city_cleaned
""",
    "ecommerce.shipping": """
ecommerce.shipping (~298K rows) - One row per order
Columns: shipping_id, order_id, carrier, tracking_number, shipped_date, delivered_date,
  shipping_status, shipping_fee_charged, actual_logistics_cost, freight_value,
  fee_calculation_error, fee_to_cost_ratio [GENERATED]
Carriers: Aramex EG, Egypt Post, Bosta, Mylerz, Voo, R2S Express
""",
    "ecommerce.refunds": """
ecommerce.refunds (~7.8K rows) - Refund records
Columns: refund_id, order_id, refund_amount, refund_date,
  refund_reason ENUM('incorrect_refund','return_request','early_refund',
  'late_delivery_compensation','item_not_as_described','customer_request'),
  refund_status, duplicate_refund, processed_before_cancel
""",
    "ecommerce.reviews": """
ecommerce.reviews (~262K rows) - WARNING: multiple reviews per order possible
Columns: review_id, order_id, customer_id, rating, review_comment, review_date,
  sentiment ENUM('positive','neutral','negative','mixed','unknown'),
  is_emoji_only, rating_group, has_comment
USE mv_leakage_dashboard (LATERAL join) to avoid row duplication
""",
    "ecommerce.products": """
ecommerce.products (~50K rows) - Product catalog
Columns: product_id, product_category_name, product_name_length,
  product_description_length, product_photos_qty, product_weight_g,
  product_length_cm, product_height_cm, product_width_cm, unit_price_egp
""",
    "ml_output.order_anomaly_scores": """
ml_output.order_anomaly_scores (~298K rows) - ML anomaly scores per order
Columns: order_id, if_score, lof_score, ensemble_score, anomaly_flag (1=leakage),
  risk_tier ENUM('Low','Medium','High','Critical'), anomaly_rank, leakage_scenarios ARRAY,
  leakage_reason, customer_id, order_status, total_revenue, profit_margin,
  avg_discount_pct, shipping_delay_days, payment_status, shipping_status, month
""",
    "ml_output.order_leakage_reasons": """
ml_output.order_leakage_reasons (~15K rows) - Junction: one row per (order, leakage_type)
Columns: order_id, leakage_type (ENUM - 23 values), confidence
PREFERRED for scenario filtering: WHERE leakage_type = 'duplicate_refund'
""",
}

MARKETING_SCHEMAS = {
    "marketing.marketing_campaigns": """
marketing.marketing_campaigns - Campaign definitions
Columns: campaign_id, campaign_name, channel, campaign_type, start_date, end_date, budget
""",
    "marketing.campaign_attribution": """
marketing.campaign_attribution - Order-to-campaign mapping
Columns: id, order_id, campaign_id, attribution_type
""",
    "marketing.website_sessions": """
marketing.website_sessions - Web session logs (may vary in row count by environment)
Columns: session_id, customer_id, session_start, session_end, traffic_source,
  landing_page, bounce, session_duration_min [GENERATED]
JOIN RULE: website_sessions.customer_id = ecommerce.orders.customer_id (direct FK)
           Use LEFT JOIN with 7-day conversion window: o.order_purchase_timestamp >= ws.session_start
           AND o.order_purchase_timestamp < ws.session_start + interval '7 days'
FALLBACK: If this table is empty, use marketing.customer_interactions instead
""",
    "marketing.customer_interactions": """
marketing.customer_interactions (~1M rows) - Customer channel event log (USE when website_sessions is empty)
Columns: interaction_id, customer_id, campaign_id, interaction_date, channel, action_type, device
JOIN RULE: customer_interactions.customer_id = ecommerce.orders.customer_id (direct FK)
WEB ANALYTICS PROXY: WHERE LOWER(channel) IN ('web','website','site') approximates website_sessions
CONVERSION PROXY:    action_type IN ('purchase','checkout') tracks purchase-intent events
""",
    "marketing.leads_qualified": """
marketing.leads_qualified (~24K rows) - Qualified sales leads
Columns: mql_id, first_contact_date, landing_page_id, origin
""",
    "marketing.leads_closed": """
marketing.leads_closed (~7.2K rows) - Closed/won deals from qualified leads
Columns: mql_id FK→leads_qualified, seller_id FK→sellers, won_date, business_segment,
  lead_type, lead_behaviour_profile, has_company, has_gtin, average_stock,
  business_type, declared_product_catalog_size, declared_monthly_revenue
""",
}

# ================================================================
# BUSINESS RULES (Leakage Definitions)
# ================================================================

LEAKAGE_RULES = {
    "high_discount_negative_profit": "avg_discount_pct > 0.50 = high discount leakage (decimal 0.0-1.0, NOT percentage)",
    "delayed_delivery": "shipping_delay_days > 14 = delayed delivery leakage",
    "duplicate_refund": "duplicate_refund = TRUE indicates duplicate refund leakage",
    "delivered_no_payment": "payment_status = 'missing' on delivered orders = delivered without payment",
    "wrong_shipping_fee": "fee_to_cost_ratio > 2.5 = wrong shipping fee leakage",
    "refund_before_cancel": "processed_before_cancel = TRUE = refund issued before order cancelled",
    "seller_paid_twice": "seller_paid_twice = TRUE = seller paid twice leakage",
    "inventory_mismatch": "inventory_mismatch = TRUE = inventory mismatch leakage",
    "never_shipped": "shipping_status = 'never_shipped' on approved payments = payment_approved_never_shipped",
    "negative_profit": "profit_margin < 0 = negative profit leakage",
    "no_invoice": "invoice_available = FALSE on completed orders = no_invoice_on_completion",
}

# Web Analytics Rules (for web_analytics intent)
WEB_ANALYTICS_RULES = {
    "session_conversion": "A session 'converts' when an order is placed within 7 days of session_start, joining on customer_id",
    "conversion_rate": "conversion_rate = COUNT(DISTINCT converted orders) / COUNT(DISTINCT sessions) * 100",
    "empty_sessions_fallback": "If website_sessions is empty, use customer_interactions WHERE channel='web' as proxy",
    "campaign_revenue": "For campaign impact: JOIN campaign_attribution ON order_id, then JOIN orders for revenue/profit",
    "traffic_source_impact": "GROUP BY ws.traffic_source to see which channels drive the most revenue",
}

CRITICAL_VALUE_RULES = """
CRITICAL VALUE RULES:
- avg_discount_pct = decimal 0.0-1.0 (NOT percentage) -> filter: > 0.50 for >50%
- profit_margin = decimal ratio (NOT percentage) -> filter: < 0 for loss
- ensemble_score = 0-1 float: Low<0.40 | Medium 0.40-0.65 | High 0.65-0.80 | Critical>0.80
- anomaly_flag = 1 means leakage, 0 means clean
- leakage_scenarios = TEXT[] -> filter: 'scenario' = ANY(leakage_scenarios)
- order_month = GENERATED DATE -> use in GROUP BY directly, no DATE_TRUNC needed
- fee_to_cost_ratio = GENERATED -> > 2.5 suggests wrong_shipping_fee leakage
"""

ENUM_REFERENCE = """
VALID ENUM VALUES:
order_status: delivered, canceled, shipped, processing, invoiced, unavailable
payment_status: approved, partial, unpaid, missing
payment_type: credit_card, debit_card, voucher, cash_on_delivery, unknown
shipping_status: delivered, in_transit, cancelled, failed, never_shipped, pending
risk_tier: Low, Medium, High, Critical
sentiment: positive, neutral, negative, mixed, unknown
segment / customer_segment: Low Value, Mid Value, High Value
churn_risk: High, Medium, Low
"""

# ================================================================
# INTENT SETS — used for priority decisions
# ================================================================

# Intents where review_insights is the PRIMARY payload
_REVIEW_INTENTS = frozenset({
    "review_analysis",
    "sentiment_analysis",
    "leakage_root_cause_analysis",
})


# ================================================================
# COMPRESSION ENGINE
# ================================================================

class ContextCompressor:
    """
    Intelligent context compression for LLM prompts.
    Reduces full schema + RAG results to 2k-5k token targets.
    """

    # Token estimates (rough approximation: 1 token ~ 4 chars)
    CHARS_PER_TOKEN = 4
    TARGET_MAX_TOKENS = 4000
    TARGET_MAX_CHARS = TARGET_MAX_TOKENS * CHARS_PER_TOKEN  # ~16000 chars

    def __init__(self):
        self.all_schemas = {**MV_SCHEMAS, **BASE_SCHEMAS, **MARKETING_SCHEMAS}

    def compress(
        self,
        plan: dict,
        rag_docs: list[dict],
        intent: str,
        user_message: str,
    ) -> CompressedContext:
        """
        Main compression entry point.
        Takes planner output + RAG docs and produces compressed context.
        """
        result = CompressedContext()

        # 1. Determine relevant tables from plan
        target_source = plan.get("target_source", "")
        tables_needed = plan.get("tables_needed", [])
        if not tables_needed and target_source:
            tables_needed = [target_source]

        # 2. Compress schema to relevant tables only
        result.schema_summary = self._compress_schema(tables_needed, rag_docs, user_message)

        # 3. Compress business rules to relevant only
        result.business_rules_summary = self._compress_business_rules(rag_docs, user_message, intent)

        # 4. Build enum reference for relevant enums only
        result.enum_reference = self._compress_enums(tables_needed, user_message)

        # 5. Compress SQL examples (top 2 only)
        result.sql_examples = self._compress_sql_examples(rag_docs)

        # 6. Compress review insights (summarize, don't include raw)
        result.review_insights = self._compress_reviews(rag_docs, user_message, intent)

        # 7. Track sources
        result.sources_used = [d.get("source_type", "unknown") for d in rag_docs]

        # 8. Estimate tokens
        total_text = (
            result.schema_summary +
            result.business_rules_summary +
            result.enum_reference +
            result.sql_examples +
            result.review_insights
        )
        result.token_estimate = len(total_text) // self.CHARS_PER_TOKEN

        # 9. If over target, aggressively truncate — pass intent so priority is correct
        if result.token_estimate > self.TARGET_MAX_TOKENS:
            result = self._aggressive_truncate(result, intent=intent)

        return result

    def slim_schema(self, table_key: str, plan: dict) -> str:
        """
        BUG-06 FIX: Return only schema lines for columns actually needed by this plan.
        For mv_leakage_dashboard (33 columns), this can cut 25+ irrelevant column
        definitions that become hallucination bait.
        """
        full_schema = self.all_schemas.get(table_key, "")
        if not full_schema:
            return ""

        # Collect all column names referenced by the plan
        needed: set[str] = set()
        needed.update(plan.get("select_columns", []))
        needed.update(plan.get("metrics", []))
        needed.update(plan.get("group_by", []))
        needed.update(c["col"] for c in plan.get("filters", []) if "col" in c)
        needed.update(c["col"] for c in plan.get("order_by", []) if "col" in c)

        if not needed:
            return full_schema  # no column info in plan → return full schema as safety net

        # Keep the header line + lines containing a needed column name
        lines = full_schema.split("\n")
        result_lines = []
        header_kept = False
        for line in lines:
            if not header_kept:
                result_lines.append(line)
                header_kept = True
                continue
            line_lower = line.lower()
            if any(col.lower() in line_lower for col in needed):
                result_lines.append(line)

        return "\n".join(result_lines) if result_lines else full_schema

    def _compress_schema(self, tables_needed: list[str], rag_docs: list[dict], user_message: str) -> str:
        """Include only schemas for tables that are relevant."""
        selected = []

        # Always include target table if known
        for table in tables_needed:
            table_lower = table.lower()
            if table_lower in self.all_schemas:
                selected.append(self.all_schemas[table_lower])
            elif "." in table_lower:
                # Try without schema prefix
                short = table_lower.split(".")[-1]
                for key, schema in self.all_schemas.items():
                    if key.endswith(f".{short}") or key == short:
                        selected.append(schema)
                        break

        # BUG-08 FIX: Removed speculative scan of RAG document content.
        # That block injected schema for tables merely *mentioned* in business-rule docs,
        # causing phantom `payment_sequential = 1` additions to unrelated queries.
        # plan.tables_needed (from the planner) is authoritative.

        # If still nothing selected, include the most commonly used MV
        if not selected:
            selected.append(MV_SCHEMAS["ml_output.mv_leakage_dashboard"])

        # Add critical value rules
        selected.append(CRITICAL_VALUE_RULES)

        return "\n".join(selected)

    def _compress_business_rules(self, rag_docs: list[dict], user_message: str, intent: str) -> str:
        """Include only business rules relevant to the query."""
        rules = []
        msg_lower = user_message.lower()

        # Match leakage rules to query keywords
        rule_keywords = {
            "high_discount_negative_profit": ["discount", "high discount", "negative profit", "margin"],
            "delayed_delivery": ["delay", "shipping", "late", "delivery"],
            "duplicate_refund": ["duplicate", "refund", "double refund"],
            "delivered_no_payment": ["payment", "unpaid", "missing payment", "delivered"],
            "wrong_shipping_fee": ["shipping fee", "fee", "logistics cost"],
            "refund_before_cancel": ["refund before", "cancel", "processed before"],
            "seller_paid_twice": ["paid twice", "seller paid", "duplicate payment"],
            "inventory_mismatch": ["inventory", "mismatch", "stock"],
            "never_shipped": ["never shipped", "not shipped"],
            "negative_profit": ["negative profit", "loss"],
            "no_invoice": ["invoice", "no invoice"],
        }

        for rule_name, keywords in rule_keywords.items():
            if any(kw in msg_lower for kw in keywords):
                if rule_name in LEAKAGE_RULES:
                    rules.append(f"- {LEAKAGE_RULES[rule_name]}")

        # Include rules from RAG docs
        for doc in rag_docs:
            if doc.get("source_type") in ("leakage_scenario", "business_rule", "leakage_reason"):
                title = doc.get("title", "")
                content = doc.get("content", "")[:300]
                if content and content not in [r for r in rules]:
                    rules.append(f"- {title}: {content}")

        # If anomaly investigation, include ALL rules
        if "anomal" in msg_lower or "leakage" in msg_lower or "fraud" in msg_lower:
            for rule_name, rule_text in LEAKAGE_RULES.items():
                if rule_text not in rules:
                    rules.append(f"- {rule_text}")

        if not rules:
            return ""

        return "LEAKAGE RULES:\n" + "\n".join(rules[:10])  # Cap at 10 rules

    def _compress_enums(self, tables_needed: list[str], user_message: str) -> str:
        """
        BUG-07 FIX: Include only enums relevant to the tables being queried.
        Previously returned the full ENUM_REFERENCE for every query (130 tokens wasted).
        """
        TABLE_TO_ENUMS: dict[str, list[str]] = {
            "ecommerce.orders":                    ["order_status", "payment_status"],
            "ecommerce.customers":                 ["segment", "churn_risk"],
            "ecommerce.payments":                  ["payment_type", "payment_status"],
            "ecommerce.shipping":                  ["shipping_status"],
            "ecommerce.reviews":                   ["sentiment"],
            "ecommerce.refunds":                   ["refund_reason"],
            "ml_output.mv_leakage_dashboard":      ["order_status", "payment_status",
                                                    "shipping_status", "sentiment",
                                                    "risk_tier", "customer_segment"],
            "ml_output.order_anomaly_scores":      ["risk_tier"],
            "ml_output.order_leakage_reasons":     ["leakage_type"],
            "marketing.website_sessions":          [],
            "marketing.customer_interactions":     ["channel"],
        }

        relevant_enum_names: set[str] = set()
        for table in tables_needed:
            relevant_enum_names.update(TABLE_TO_ENUMS.get(table.lower(), []))

        if not relevant_enum_names:
            return ENUM_REFERENCE  # safe fallback: no table hints → return all

        # Filter ENUM_REFERENCE to only the relevant enum lines
        lines = ENUM_REFERENCE.strip().split("\n")
        kept = [lines[0]]  # keep the "VALID ENUM VALUES:" header
        for line in lines[1:]:
            for enum_name in relevant_enum_names:
                if line.strip().startswith(enum_name):
                    kept.append(line)
                    break
        return "\n".join(kept)

    def _compress_sql_examples(self, rag_docs: list[dict]) -> str:
        """Include at most 2 SQL templates, most relevant first."""
        examples = []
        for doc in rag_docs:
            if doc.get("source_type") == "sql_template":
                title = doc.get("title", "SQL Template")
                content = doc.get("content", "")
                # Extract SQL block if present
                sql_match = re.search(r"```sql\s*(.*?)```", content, re.DOTALL)
                if sql_match:
                    examples.append(f"-- {title}\n{sql_match.group(1).strip()}")
                elif "SELECT" in content.upper():
                    examples.append(f"-- {title}\n{content[:500]}")
                if len(examples) >= 2:
                    break
        return "\n\n".join(examples) if examples else ""

    def _compress_reviews(self, rag_docs: list[dict], user_message: str, intent: str) -> str:
        """
        For review/sentiment analysis: include full review excerpts from RAG vector search.
        For leakage root cause: also include full excerpts.
        For other queries: only structured insights.

        FIX [Bug #7]: review_analysis now receives the same full-excerpt treatment as
        sentiment_analysis — 10 excerpts × 800 chars. Previously it fell into the
        weaker `is_review_query` branch (5 excerpts × 400 chars), giving the analytics
        agent far too little data to synthesize a meaningful answer.
        """
        review_docs = [d for d in rag_docs if d.get("source_type") == "review"]
        if not review_docs:
            return ""

        # FIX [Bug #7]: Added "review_analysis" alongside "sentiment_analysis" and
        # "leakage_root_cause_analysis" so it receives full excerpts, not truncated ones.
        if intent in ("sentiment_analysis", "review_analysis", "leakage_root_cause_analysis"):
            excerpts = []
            for doc in review_docs[:10]:
                content = doc.get("content", "").strip()
                if content and len(content) > 20:
                    title = doc.get("title", "Customer Review")
                    excerpts.append(f"**{title}**\n{content[:800]}")
            if excerpts:
                return (
                    "## REVIEW EXCERPTS FROM VECTOR SEARCH "
                    "(semantically similar reviews):\n\n" +
                    "\n\n".join(excerpts)
                )
            else:
                return "## No substantial review text found."

        # For non-review queries, only structured themes
        msg_lower = user_message.lower()
        is_review_query = any(
            k in msg_lower
            for k in ["review", "تقييم", "مراجعة", "شكوى", "complaint", "negative"]
        )

        if is_review_query:
            excerpts = []
            for doc in review_docs[:5]:
                title = doc.get("title", "")
                content = doc.get("content", "")[:400]
                if content:
                    excerpts.append(f"- {title}: {content}")
            if excerpts:
                return (
                    "REVIEW EXCERPTS FROM VECTOR SEARCH (semantically similar reviews):\n" +
                    "\n".join(excerpts)
                )

        # Default aggregated themes
        themes = []
        for doc in review_docs[:3]:
            content = doc.get("content", "")
            if "sentiment" in content.lower():
                themes.append("- Sentiment distribution data available")
            if "rating" in content.lower():
                themes.append("- Rating patterns analyzed")
            if "delay" in content.lower() or "late" in content.lower():
                themes.append("- Shipping delay complaints detected")
            if "refund" in content.lower():
                themes.append("- Refund-related feedback present")
            if "damaged" in content.lower() or "quality" in content.lower():
                themes.append("- Product quality concerns identified")

        if not themes:
            return ""

        return "REVIEW THEMES (aggregated, no raw text):\n" + "\n".join(themes)

    def _aggressive_truncate(
        self,
        ctx: CompressedContext,
        intent: str = "",
    ) -> CompressedContext:
        """
        If context exceeds target, aggressively truncate less important sections.

        FIX [Bug #6]: The original implementation always dropped review_insights first,
        regardless of intent. For review/sentiment analysis, review_insights IS the
        primary payload — dropping it caused "no data" answers even when 500 semantically
        relevant reviews had been retrieved.

        New behavior:
          - For review/sentiment intents (_REVIEW_INTENTS): PROTECT review_insights.
            Truncate sql_examples → schema_summary → enum_reference instead.
          - For all other intents: keep the original priority order
            (drop review_insights → truncate sql_examples → truncate schema_summary).
        """
        log.warning(
            f"[context_compression] Context too large ({ctx.token_estimate} tokens), "
            f"truncating... (intent={intent!r})"
        )

        is_review_intent = intent in _REVIEW_INTENTS

        if is_review_intent:
            # ----------------------------------------------------------------
            # REVIEW INTENT: protect review_insights — it's the primary payload
            # ----------------------------------------------------------------

            # 1. Drop SQL examples entirely (not needed for RAG-based answers)
            ctx.sql_examples = ""

            # 2. Truncate enum reference (helpful but not critical)
            if ctx.enum_reference:
                ctx.enum_reference = ctx.enum_reference[:300]

            # 3. Truncate schema summary (reviews don't need full schema)
            if ctx.schema_summary:
                lines = ctx.schema_summary.split("\n")
                truncated = []
                table_count = 0
                for line in lines:
                    if line.strip().startswith("ml_output.mv_") or line.strip().startswith("ecommerce."):
                        table_count += 1
                    if table_count <= 1:
                        truncated.append(line)
                    elif line.strip().startswith("CRITICAL VALUE"):
                        truncated.append(line)
                ctx.schema_summary = "\n".join(truncated)

            # 4. Truncate review_insights only as a last resort
            total = (
                ctx.schema_summary + ctx.business_rules_summary +
                ctx.enum_reference + ctx.sql_examples + ctx.review_insights
            )
            if len(total) // self.CHARS_PER_TOKEN > self.TARGET_MAX_TOKENS:
                # Still over budget — truncate reviews to 6000 chars (~1500 tokens)
                ctx.review_insights = ctx.review_insights[:6000]
                log.warning(
                    "[context_compression] review_insights truncated to 6000 chars "
                    "as last resort for review intent."
                )

        else:
            # ----------------------------------------------------------------
            # NON-REVIEW INTENT: original priority order
            # ----------------------------------------------------------------

            # 1. Drop review insights entirely (low priority for non-review queries)
            ctx.review_insights = ""

            # 2. Truncate SQL examples
            if ctx.sql_examples:
                ctx.sql_examples = ctx.sql_examples[:500]

            # 3. Truncate schema (keep only first 2 tables)
            if ctx.schema_summary:
                lines = ctx.schema_summary.split("\n")
                truncated = []
                table_count = 0
                for line in lines:
                    if line.strip().startswith("ml_output.mv_") or line.strip().startswith("ecommerce."):
                        table_count += 1
                    if table_count <= 2:
                        truncated.append(line)
                    elif line.strip().startswith("CRITICAL VALUE"):
                        truncated.append(line)
                ctx.schema_summary = "\n".join(truncated)

        # Recalculate token estimate
        total = (
            ctx.schema_summary + ctx.business_rules_summary +
            ctx.enum_reference + ctx.sql_examples + ctx.review_insights
        )
        ctx.token_estimate = len(total) // self.CHARS_PER_TOKEN

        return ctx


# Global instance
_compressor = ContextCompressor()


def compress_context(plan: dict, rag_docs: list[dict], intent: str, user_message: str) -> CompressedContext:
    """Public API for context compression."""
    return _compressor.compress(plan, rag_docs, intent, user_message)