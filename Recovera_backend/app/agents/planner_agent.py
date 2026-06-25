"""
planner_agent.py - Planner Agent (Groq Llama 3.3 70B)
======================================================
Responsibilities:
  - Understand user intent
  - Classify query type (SQL_ONLY, RAG_ONLY, HYBRID, NON_DATABASE)
  - Determine complexity (easy, medium, complex)
  - Decide routing (SQL, RAG, or both)
  - Select relevant tables
  - Select embedding sources
  - Determine metrics
  - Determine time granularity
  - Output structured JSON plan

Model: Groq Llama 3.3 70B (via Groq API directly)
Temperature: 0.1
Output: Structured JSON

"""

import json
import logging
from typing import Optional, Tuple
from dataclasses import dataclass, field

from app.core.intent_classifier import classify_intent
from app.core.question_patterns import detect_question_profile, should_use_profile_fast_path
from app.database.postgres import get_pool

import httpx

from app.core.config import settings

log = logging.getLogger(__name__)

# ================================================================
# PLAN STRUCTURE
# ================================================================

@dataclass
class QueryPlan:
    """Structured output from the planner agent."""
    intent: str = ""
    route: str = "hybrid"           # sql_only | rag_only | hybrid | non_database
    difficulty: str = "medium"      # easy | medium | complex
    needs_sql: bool = True
    needs_rag: bool = True
    tables_needed: list[str] = field(default_factory=list)
    embedding_sources: list[str] = field(default_factory=list)
    metrics: list[str] = field(default_factory=list)
    time_grain: str = ""
    question_language: str = "english"
    target_source: str = ""
    reasoning: str = ""
    filters: list[dict] = field(default_factory=list)
    group_by: list[str] = field(default_factory=list)
    order_by: list[dict] = field(default_factory=list)
    limit: int = 20
    select_columns: list[str] = field(default_factory=list)
    web_analytics_dimension: str = ""  # duration | monthly_trend | traffic_source | device
    template_key: str = ""
    primary_dimension: str = ""
    primary_metric: str = ""
    chart_policy: dict = field(default_factory=dict)
    profile_confidence: float = 0.0

    def to_dict(self) -> dict:
        return {
            "intent": self.intent,
            "route": self.route,
            "difficulty": self.difficulty,
            "needs_sql": self.needs_sql,
            "needs_rag": self.needs_rag,
            "tables_needed": self.tables_needed,
            "embedding_sources": self.embedding_sources,
            "metrics": self.metrics,
            "time_grain": self.time_grain,
            "question_language": self.question_language,
            "target_source": self.target_source,
            "reasoning": self.reasoning,
            "filters": self.filters,
            "group_by": self.group_by,
            "order_by": self.order_by,
            "limit": self.limit,
            "select_columns": self.select_columns,
            "web_analytics_dimension": self.web_analytics_dimension,
            "template_key": self.template_key,
            "primary_dimension": self.primary_dimension,
            "primary_metric": self.primary_metric,
            "chart_policy": self.chart_policy,
            "profile_confidence": self.profile_confidence,
        }


# ================================================================
# PLANNER PROMPT
# ================================================================

PLANNER_SYSTEM_PROMPT = """You are an enterprise analytics query planner for a Revenue Leakage Detection System.
Your job is to analyze user questions and produce a structured JSON execution plan.

DATABASE OVERVIEW:
- PostgreSQL database with ecommerce transaction data (~298K orders)
- Materialized Views (USE THESE FIRST): mv_leakage_dashboard, mv_monthly_leakage, mv_seller_risk, mv_leakage_by_scenario
- Base tables: orders, customers, payments, order_items, sellers, shipping, refunds, reviews, products
- Marketing tables: marketing_campaigns, campaign_attribution, website_sessions
- ML tables: order_anomaly_scores, order_leakage_reasons
- RAG tables: documents with typed embeddings (schema, business_rule, metric, review)

ROUTING RULES:
- SQL_ONLY:      KPIs, aggregations, trends, rankings, simple lookups with no review text needed
- RAG_ONLY:      Pure review text analysis, sentiment reading, what customers SAY — no financial numbers needed
- HYBRID:        When BOTH review semantics AND financial impact are requested together
                 (e.g. "analyze reviews about X AND see how much revenue impact")
- NON_DATABASE:  Greetings, help requests, system questions, definitions

DUAL RANKING QUERIES — CRITICAL ROUTING RULE

For questions asking SIMULTANEOUSLY about two different rankings in the same dimension
(e.g. "أكتر منطقة جابت أرباح وأكتر منطقة حققت خسائر",
"most profitable city and city with highest losses",
"best seller and worst seller",
"top region by revenue and top region by leakage",
"highest revenue city and highest loss city"):

* route = sql_only

* difficulty = "complex"

* SQL MUST use separate ranking CTEs with independent ROW_NUMBER() functions.

* NEVER use a single ORDER BY across multiple business metrics.

* Use UNION ALL to combine ranking outputs.

PROFIT RANKING METRIC

If user asks:

* highest revenue
* most profitable
* top earning
* أعلى أرباح
* أعلى إيرادات
* الأكثر ربحية

Then:

total_revenue DESC

LEAKAGE / LOSS METRIC SELECTION

1. RISK-ORIENTED WORDING

Examples:

* highest leakage rate
* highest risk
* riskiest city
* worst leakage percentage
* أعلى نسبة تسرب
* أخطر منطقة
* أعلى معدل تسرب

Then:

leakage_rate_pct DESC

2. FINANCIAL LOSS WORDING

Examples:

* highest losses
* biggest loss
* most money lost
* highest leakage amount
* largest revenue leakage
* أعلى خسائر
* أكبر خسارة مالية
* أكتر منطقة خسرت
* أعلى تسرب مالي

Then:

leakage_revenue DESC

3. VOLUME WORDING

Examples:

* most leakage cases
* most leakage orders
* highest number of leakage incidents
* أكثر حالات تسرب
* أكثر طلبات متسربة

Then:

leakage_orders DESC

DUAL RANKING OUTPUT REQUIREMENTS

* Planner MUST identify BOTH ranking metrics explicitly.
* Include selected metrics in metrics[].
* Explain metric selection in reasoning.

Example:

User:
"أعلى منطقة تحقيق للأرباح وأعلى منطقة تحقيق للخسائر"

Metrics:

[
"total_revenue",
"leakage_revenue"
]

Reasoning:

"Dual ranking query. Revenue ranking uses total_revenue. Loss ranking uses leakage_revenue because the user requested financial losses."

tables_needed = ["ml_output.mv_leakage_dashboard"]

LEAKAGE METRIC INTERPRETATION — CRITICAL

Leakage does NOT always mean leakage_rate_pct.

Select the leakage metric according to user intent.

Risk Assessment:

leakage_rate_pct

Financial Impact:

leakage_revenue

Operational Volume:

leakage_orders

Interpretation Guide

If user asks:

* risk
* risky
* danger
* probability
* rate
* percentage
* أخطر
* نسبة
* معدل

Use:

leakage_rate_pct

If user asks:

* loss
* losses
* money lost
* financial impact
* cost
* revenue leakage
* خسارة
* خسائر
* تسرب مالي

Use:

leakage_revenue

If user asks:

* incidents
* cases
* orders
* occurrences
* حالات
* طلبات متسربة

Use:

leakage_orders

AMBIGUITY RULE

When wording is ambiguous:

Prefer leakage_revenue over leakage_rate_pct.

Executive users usually interpret:

"الخسائر"

as financial loss rather than leakage percentage.


LEAKAGE METRIC SELECTION PRIORITY:
  * For risk/rate questions → leakage_rate_pct
  * For financial loss questions → leakage_revenue 
  * For volume questions → leakage_orders
  * When ambiguous → prefer leakage_revenue (executives interpret losses as financial)

CAMPAIGN ANALYSIS ROUTING — CRITICAL:
For ANY question about "best/top/most profitable campaign", "campaign ROI", "campaign performance",
"which campaign generated most revenue/orders", "compare campaigns", "campaign profitability",
"أفضل حملة", "أكتر حملة ربحًا", "أداء الحملات", "مقارنة الحملات", "أكتر marketing campaign حققت أرباح":
  * ALWAYS route = sql_only, needs_sql = true, needs_rag = false
  * intent = "campaign_analysis"
  * tables_needed = ["marketing.campaign_attribution", "marketing.marketing_campaigns", "ecommerce.orders"]
  * metrics = ["total_revenue", "avg_profit_margin", "order_count"]
  * group_by = ["campaign_id", "campaign_name"]
  * order_by = [{"col": "total_revenue", "dir": "DESC"}]
  * limit = 10
  * difficulty = "medium"
  * MANDATORY JOIN: campaign_attribution → marketing_campaigns ON campaign_id
                    campaign_attribution → orders ON order_id

WEB ANALYTICS ROUTING — CRITICAL:
For ANY question about "website sessions", "web sessions", "جلسات الموقع", "session impact",
"sessions and sales", "sessions and profit", "تأثير الsessions", "website traffic + revenue",
"customer interactions", "تفاعل العملاء", "campaign impact", "تأثير الحملات":
  * ALWAYS route = sql_only, needs_sql = true, needs_rag = false
  * PRIMARY target_source = "marketing.website_sessions"
  * FALLBACK 1: if sessions empty → target_source = "marketing.customer_interactions"
  * FALLBACK 2: if interactions empty → target_source = "marketing.campaign_attribution"
  * tables_needed = ["marketing.website_sessions", "marketing.customer_interactions",
                     "marketing.campaign_attribution", "marketing.marketing_campaigns", "ecommerce.orders"]
  * metrics = ["total_sessions", "total_revenue", "avg_profit_margin", "conversion_rate"]
  * time_grain = "monthly"
  * group_by = ["month"]
  * limit = 24
  * difficulty = "medium"
  * intent = "web_analytics"

MARKETING DATA SOURCES (most → least preferred for session/traffic analysis):
  1. marketing.website_sessions — direct session logs (session_id, customer_id, session_start, traffic_source)
  2. marketing.customer_interactions — channel events; WHERE channel='web' proxies web sessions
  3. marketing.campaign_attribution — maps orders to campaigns; good for channel ROI analysis
  4. marketing.marketing_campaigns — campaign definitions (channel, budget, dates)

REVIEW & SENTIMENT ANALYSIS — CRITICAL ROUTING RULES:
For "negative reviews", "customer complaints", "what do customers say", "review analysis",
"تحليل المراجعات", "تقييمات سلبية", "الزباين بتشتكي", "reviews", "complaints":
  * If user ONLY asks what reviews say → route = rag_only, needs_sql = false
  * If user asks reviews AND financial/revenue impact → route = hybrid, needs_sql = true
  * Embedding sources: ALWAYS include "review_embeddings" for cosine similarity on review text

CRITICAL — REVIEW SQL RULES (for hybrid route only):
  !! NEVER generate SQL using review_comment ILIKE or text search on review_comment !!
  !! Review text search is handled by RAG vector similarity, NOT by SQL !!

  For "not as described" / "غير مطابق للوصف" / "product mismatch" queries:
    * SQL target: ecommerce.refunds WHERE refund_reason = 'item_not_as_described'
    * Also check: ml_output.order_leakage_reasons WHERE leakage_type = 'item_not_as_described'
    * Join with ml_output.mv_leakage_dashboard for revenue/profit context
    * Tables needed: ["ecommerce.refunds", "ml_output.order_leakage_reasons", "ml_output.mv_leakage_dashboard"]

  For "negative reviews impact on revenue":
    * SQL target: ml_output.mv_leakage_dashboard WHERE sentiment = 'negative' AND rating <= 2
    * Aggregate: COUNT(*), SUM(total_revenue), AVG(profit_margin) grouped by rating or sentiment
    * Tables needed: ["ml_output.mv_leakage_dashboard"]

  For "shipping complaints":
    * SQL target: ecommerce.refunds WHERE refund_reason = 'late_delivery_compensation'
    * Or: ml_output.mv_leakage_dashboard WHERE shipping_delay_days > 14 AND sentiment = 'negative'

TABLE SELECTION GUIDE:
- Order details / anomalies → ml_output.mv_leakage_dashboard
- Monthly/quarterly trends  → ml_output.mv_monthly_leakage
- Seller risk/ranking       → ml_output.mv_seller_risk
- Scenario comparison       → ml_output.mv_leakage_by_scenario
- Refund details            → ecommerce.refunds
- Customer segments         → ecommerce.customers
- Shipping analysis         → ecommerce.shipping
- Product analysis          → ecommerce.products + ecommerce.order_items
- Campaign attribution      → marketing.campaign_attribution + marketing.marketing_campaigns
- Web analytics / sessions  → marketing.website_sessions JOIN ecommerce.orders ON customer_id
                              FALLBACK: marketing.customer_interactions WHERE channel='web'
                              FALLBACK: marketing.campaign_attribution + marketing.marketing_campaigns
- REVIEW IMPACT SQL         → ecommerce.refunds (refund_reason) + ml_output.order_leakage_reasons + mv_leakage_dashboard

LEAKAGE ROUTING — CRITICAL:
For any question about specific leakage scenarios, anomalies, fraud, or payment issues:
  * Use the materialized views (mv_leakage_dashboard, mv_leakage_by_scenario) as primary sources
  * Numeric thresholds are stored in the DB — do NOT inject thresholds as SQL literal filters
    unless the user explicitly specifies a value

DIFFICULTY:
- easy:    Simple lookups, single-table aggregations, direct filters
- medium:  Multi-table joins, time-series, comparisons
- complex: Root cause analysis, hybrid RAG+SQL, multi-dimensional analysis

EMBEDDING SOURCES (for RAG retrieval):
- schema_embeddings:   Table schemas, join graphs, enum references
- business_embeddings: Leakage scenarios, business rules, detection logic
- metrics_embeddings:  KPI definitions, anomaly interpretations
- review_embeddings:   Customer reviews, semantic text similarity

OUTPUT FORMAT — Return ONLY valid JSON:
{
  "intent": "descriptive intent name — use review_analysis for review/sentiment questions",
  "route": "sql_only|rag_only|hybrid|non_database",
  "difficulty": "easy|medium|complex",
  "needs_sql": true|false,
  "needs_rag": true|false,
  "tables_needed": ["schema.table1", "schema.table2"],
  "embedding_sources": ["review_embeddings", "schema_embeddings"],
  "metrics": ["metric_name1"],
  "time_grain": "daily|weekly|monthly|quarterly|yearly",
  "question_language": "english|arabic",
  "target_source": "primary table or view",
  "reasoning": "one sentence explaining the plan",
  "filters": [{"col": "column", "op": "=|>|<|>=|<=|ANY|IN", "val": "value"}],
  "group_by": ["column1"],
  "order_by": [{"col": "column", "dir": "ASC|DESC"}],
  "limit": 20,
  "select_columns": ["col1", "col2"]
}"""

# ================================================================
# GROQ CLIENT
# ================================================================

class GroqPlanner:
    """Planner agent using Groq Llama 3.3 70B."""

    def __init__(self):
        self.api_key = settings.GROQ_API_KEY
        self.model = settings.GROQ_MODEL
        self.base_url = settings.GROQ_BASE_URL
        self.timeout = 30.0

    def _clean_messages(self, messages: list[dict]) -> list[dict]:
        """Clean messages for Llama 3.x strict validation."""
        cleaned = []
        for msg in messages:
            content = msg.get("content", "").strip()
            role = msg.get("role", "")
            if not content or not role:
                continue
            if cleaned and cleaned[-1]["role"] == role:
                cleaned[-1]["content"] += "\n" + content
            else:
                cleaned.append({"role": role, "content": content})
        while cleaned and cleaned[-1]["role"] != "user":
            cleaned.pop()
        if not cleaned:
            cleaned.append({"role": "user", "content": "Hello"})
        return cleaned

    async def plan(
        self,
        user_message: str,
        memory_context: str = "",
        compressed_schema: str = "",
    ) -> tuple[QueryPlan, int, int]:
        """
        Generate a query plan from user message.
        Returns (plan, input_tokens, output_tokens).
        """
        if not self.api_key:
            raise RuntimeError("GROQ_API_KEY not configured")

        # BUG-09 FIX: Keep system message truly static (enables Groq prompt caching).
        # Dynamic per-request context (schema hints, session memory) moves to user message.
        system_content = PLANNER_SYSTEM_PROMPT

        user_content = user_message
        if compressed_schema:
            user_content = f"[Schema hints]\n{compressed_schema}\n\n[Question]\n{user_message}"
        if memory_context:
            user_content = f"[Session context]\n{memory_context}\n\n{user_content}"

        messages = [
            {"role": "system", "content": system_content},
            {"role": "user",   "content": user_content},
        ]

        cleaned = self._clean_messages(messages)

        payload = {
            "model": self.model,
            "messages": cleaned,
            "temperature": 0.1,
            "max_tokens": 1024,
            "response_format": {"type": "json_object"},
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(self.base_url, headers=headers, json=payload)

            if resp.status_code == 400:
                raise RuntimeError(f"Groq 400 Bad Request: {resp.text}")
            if resp.status_code == 429:
                raise RuntimeError("Groq rate limit - retrying...")
            if resp.status_code == 401:
                raise RuntimeError("Invalid Groq API key")

            resp.raise_for_status()
            data = resp.json()

        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        in_tok = usage.get("prompt_tokens", len(str(messages)) // 4)
        out_tok = usage.get("completion_tokens", len(content) // 4)

        plan = self._parse_plan(content)
        return plan, in_tok, out_tok

    def _parse_plan(self, raw: str) -> QueryPlan:
        """Parse JSON plan from LLM output with robust error handling."""
        plan = QueryPlan()

        cleaned = raw
        if "<think>" in cleaned:
            cleaned = cleaned.split("</think>")[-1]
        cleaned = cleaned.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        if cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start >= 0 and end > start:
                try:
                    data = json.loads(cleaned[start:end+1])
                except json.JSONDecodeError:
                    log.warning(f"Failed to parse planner JSON: {raw[:200]}")
                    return plan
            else:
                log.warning(f"No JSON found in planner output: {raw[:200]}")
                return plan

        plan.intent = data.get("intent", "general_query")
        plan.route = data.get("route", "hybrid")
        plan.difficulty = data.get("difficulty", "medium")
        plan.needs_sql = data.get("needs_sql", True)
        plan.needs_rag = data.get("needs_rag", True)
        plan.tables_needed = data.get("tables_needed", [])
        plan.embedding_sources = data.get("embedding_sources", [])
        plan.metrics = data.get("metrics", [])
        plan.time_grain = data.get("time_grain", "")
        plan.question_language = data.get("question_language", "english")
        plan.target_source = data.get("target_source", "")
        plan.reasoning = data.get("reasoning", "")
        plan.filters = data.get("filters", [])
        plan.group_by = data.get("group_by", [])
        plan.order_by = data.get("order_by", [])
        plan.limit = data.get("limit", 20)
        plan.select_columns = data.get("select_columns", [])
        plan.web_analytics_dimension = data.get("web_analytics_dimension", plan.web_analytics_dimension)
        plan.template_key = data.get("template_key", plan.template_key)
        plan.primary_dimension = data.get("primary_dimension", plan.primary_dimension)
        plan.primary_metric = data.get("primary_metric", plan.primary_metric)
        plan.chart_policy = data.get("chart_policy", plan.chart_policy or {})
        try:
            plan.profile_confidence = float(data.get("profile_confidence", plan.profile_confidence or 0.0))
        except Exception:
            plan.profile_confidence = 0.0

        # Normalize route
        if plan.route not in ("sql_only", "rag_only", "hybrid", "non_database"):
            plan.route = "hybrid"
        if plan.difficulty not in ("easy", "medium", "complex"):
            plan.difficulty = "medium"

        # Sync needs_ flags with route
        if plan.route == "sql_only":
            plan.needs_sql = True
            plan.needs_rag = False
        elif plan.route == "rag_only":
            plan.needs_sql = False
            plan.needs_rag = True
        elif plan.route == "non_database":
            plan.needs_sql = False
            plan.needs_rag = False

        # Ensure schema prefix
        if plan.target_source and "." not in plan.target_source:
            plan.target_source = f"ml_output.{plan.target_source}"

        return plan

    async def health_check(self) -> dict:
        try:
            headers = {"Authorization": f"Bearer {self.api_key}"}
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    "https://api.groq.com/openai/v1/models",
                    headers=headers,
                )
                return {"ok": resp.status_code == 200, "model": self.model}
        except Exception as e:
            return {"ok": False, "error": str(e)}


# Global instance
planner = GroqPlanner()


# ================================================================
# REVIEW-INTENT GUARDRAIL
# ================================================================

# Keywords that signal the user ALSO wants financial/impact data (→ hybrid)
_FINANCIAL_KEYWORDS = (
    "impact", "revenue", "cost", "loss", "profit", "money",
    "إيرادات", "خسارة", "ربح", "مال", "تكلفة", "تأثير",
    "مشاكل قد ايه", "سببت مشاكل", "كم خسرنا", "كم كلفنا",
    "how much", "financial", "affected revenue",
)

def _query_needs_financial(user_message: str) -> bool:
    """Return True if the user also wants financial/revenue impact analysis."""
    lower = user_message.lower()
    return any(kw in lower for kw in _FINANCIAL_KEYWORDS)


def _apply_review_guardrails(plan: QueryPlan, user_message: str) -> QueryPlan:
    """
    Post-process the plan for review/sentiment intents:
      1. Always inject review_embeddings into embedding_sources.
      2. For hybrid route: ensure correct tables (refunds + leakage_reasons) are present.
      3. For rag_only route: clear SQL flags.
    This resolves Bug #2 — the conflict between Groq (HYBRID) and hardcoded (RAG_ONLY).
    """
    if plan.intent not in ("review_analysis", "sentiment_analysis", "leakage_root_cause_analysis"):
        return plan

    # Always pull review embeddings
    if "review_embeddings" not in plan.embedding_sources:
        plan.embedding_sources.append("review_embeddings")

    if plan.route == "hybrid" and plan.needs_sql:
        # Ensure SQL targets structured fields, not review_comment text
        # Inject correct tables if not already present
        review_sql_tables = [
            "ecommerce.refunds",
            "ml_output.order_leakage_reasons",
            "ml_output.mv_leakage_dashboard",
        ]
        for t in review_sql_tables:
            if t not in plan.tables_needed:
                plan.tables_needed.append(t)

        # Set primary target to refunds for "not as described" queries
        lower = user_message.lower()
        if any(k in lower for k in ["مطابق", "وصف", "described", "description", "mismatch"]):
            plan.target_source = "ecommerce.refunds"
            plan.filters = [
                {"col": "refund_reason", "op": "=", "val": "item_not_as_described"}
            ]

    elif plan.route == "rag_only":
        # Pure text analysis: ensure SQL is disabled
        plan.needs_sql = False

    return plan


# ================================================================
# WEB ANALYTICS DIMENSION DETECTION
# ================================================================

_WEB_DIM_DURATION = ("وقت", "مدة", "يفضل فاتح", "duration", "time spent", "longer", "session_end")
_WEB_DIM_DEVICE   = ("موبايل", "ديسكتوب", "device", "mobile", "desktop", "tablet")
_WEB_DIM_SOURCE   = ("مصدر", "traffic source", "جوجل", "organic", "paid", "direct", "referral")


def _detect_web_dimension(msg: str) -> str:
    """Keyword-based O(1) detection of web analytics dimension."""
    lower = msg.lower()
    if any(k in lower for k in _WEB_DIM_DURATION): return "duration"
    if any(k in lower for k in _WEB_DIM_DEVICE):   return "device"
    if any(k in lower for k in _WEB_DIM_SOURCE):   return "traffic_source"
    return "monthly_trend"




def _plan_from_profile(profile, language: str) -> QueryPlan:
    """Convert deterministic QueryProfile to QueryPlan."""
    patch = profile.to_plan_patch()
    plan = QueryPlan(
        intent=patch.get("intent", profile.intent),
        route=patch.get("route", profile.route),
        difficulty=patch.get("difficulty", profile.difficulty),
        needs_sql=patch.get("needs_sql", profile.needs_sql),
        needs_rag=patch.get("needs_rag", profile.needs_rag),
        tables_needed=[],
        embedding_sources=["schema_embeddings"] if profile.needs_sql else [],
        metrics=patch.get("metrics", []),
        time_grain="monthly" if profile.family in {"trend", "web_analytics"} else "",
        question_language=language,
        target_source=patch.get("target_source", profile.target_source),
        reasoning=patch.get("reasoning", profile.reason),
        filters=[],
        group_by=patch.get("group_by", []),
        order_by=patch.get("order_by", []),
        limit=patch.get("limit", profile.limit),
        select_columns=[],
        web_analytics_dimension=profile.dimension if profile.family == "web_analytics" else "",
        template_key=patch.get("template_key", profile.template_key),
        primary_dimension=patch.get("primary_dimension", profile.dimension),
        primary_metric=patch.get("primary_metric", profile.rank_metric),
        chart_policy=patch.get("chart_policy", profile.chart_policy or {}),
        profile_confidence=patch.get("profile_confidence", profile.confidence),
    )

    # Fill table dependencies for templates.
    table_map = {
        "dashboard_kpi": ["ml_output.mv_leakage_dashboard"],
        "monthly_leakage_trend": ["ml_output.mv_monthly_leakage"],
        "scenario_ranking": ["ml_output.mv_leakage_by_scenario"],
        "seller_risk_ranking": ["ml_output.mv_seller_risk"],
        "region_ranking": ["ml_output.mv_leakage_dashboard"],
        "payment_leakage": ["ml_output.mv_leakage_dashboard"],
        "shipping_leakage": ["ml_output.mv_leakage_dashboard"],
        "refund_analysis": ["ecommerce.refunds", "ecommerce.orders"],
        "product_category_analysis": ["ecommerce.order_items", "ecommerce.products", "ecommerce.orders", "ml_output.order_anomaly_scores"],
        "campaign_performance": ["marketing.campaign_attribution", "marketing.marketing_campaigns", "ecommerce.orders"],
        "lookup_order_or_entity": ["ml_output.mv_leakage_dashboard"],
        "review_financial_impact": ["ml_output.mv_leakage_dashboard"],
    }
    if profile.template_key.startswith("dual_"):
        plan.tables_needed = ["ml_output.mv_leakage_dashboard"]
    elif profile.template_key.startswith("web_"):
        plan.tables_needed = ["marketing.website_sessions", "marketing.customer_interactions", "marketing.campaign_attribution", "marketing.marketing_campaigns", "ecommerce.orders"]
    else:
        plan.tables_needed = table_map.get(profile.template_key, [profile.target_source] if profile.target_source else [])

    if profile.intent == "review_analysis":
        plan.embedding_sources = ["review_embeddings", "schema_embeddings"] if plan.needs_sql else ["review_embeddings"]

    return plan

# ================================================================
# PUBLIC API
# ================================================================

async def plan_query(
    user_message: str,
    memory_context: str = "",
    compressed_schema: str = "",
) -> Tuple[QueryPlan, int, int]:
    """
    Main planning function.
    1. Runs fast embedding-based intent classification.
    2. For review/sentiment intents: decides route based on whether the query
       also requests financial impact (→ hybrid) or only review text (→ rag_only).
    3. For all other intents: delegates to Groq LLM planner.
    4. Applies review guardrails to ensure correct SQL tables and embedding sources.
    """
    pool = get_pool()
    intent, score = await classify_intent(pool, user_message)
    language = "arabic" if any('\u0600' <= c <= '\u06FF' for c in user_message) else "english"

    # ----------------------------------------------------------------
    # Review / sentiment intents — fast path (no Groq call needed)
    # ----------------------------------------------------------------
    if intent in ("review_analysis", "sentiment_analysis"):
        needs_financial = _query_needs_financial(user_message)
        if needs_financial:
            # Hybrid: RAG for review text + SQL for financial impact
            plan = QueryPlan(
                intent=intent,
                route="hybrid",
                difficulty="complex",
                needs_sql=True,
                needs_rag=True,
                embedding_sources=["review_embeddings"],
                question_language=language,
                target_source="ecommerce.refunds",
                tables_needed=[
                    "ecommerce.refunds",
                    "ml_output.order_leakage_reasons",
                    "ml_output.mv_leakage_dashboard",
                ],
            )
        else:
            # RAG-only: pure semantic review analysis
            plan = QueryPlan(
                intent=intent,
                route="rag_only",
                difficulty="medium",
                needs_sql=False,
                needs_rag=True,
                embedding_sources=["review_embeddings"],
                question_language=language,
            )
        plan = _apply_review_guardrails(plan, user_message)
        log.info(
            f"[planner] Fast-path intent='{intent}' route='{plan.route}' "
            f"needs_financial={needs_financial} score={score:.3f}"
        )
        return plan, 0, 0

    # ----------------------------------------------------------------
    # Leakage root cause — RAG-only fast path
    # ----------------------------------------------------------------
    if intent == "leakage_root_cause_analysis":
        plan = QueryPlan(
            intent=intent,
            route="rag_only",
            difficulty="complex",
            needs_sql=False,
            needs_rag=True,
            embedding_sources=["business_embeddings", "review_embeddings"],
            question_language=language,
        )
        log.info(f"[planner] Fast-path intent='{intent}' route='rag_only' score={score:.3f}")
        return plan, 0, 0

    # ----------------------------------------------------------------
    # Campaign analysis intent — fast path (direct SQL plan, no Groq call)
    # ----------------------------------------------------------------
    # Ensures campaign profitability questions always hit the correct 3-table
    # JOIN (campaign_attribution → marketing_campaigns → orders) without
    # relying on Groq to discover the join graph from the schema hints.
    _campaign_signal = (
        intent == "campaign_analysis"
        or any(kw in user_message.lower() for kw in (
            "best campaign", "top campaign", "most profitable campaign",
            "highest revenue campaign", "campaign roi", "campaign performance",
            "which campaign", "compare campaign",
            "أفضل حملة", "أكثر حملة ربحًا", "أكتر حملة",
            "أداء الحملات", "مقارنة الحملات", "ربحية الحملات",
            "أكتر marketing campaign", "أفضل marketing campaign",
        ))
    )
    if _campaign_signal:
        plan = QueryPlan(
            intent="campaign_analysis",
            route="sql_only",
            difficulty="medium",
            needs_sql=True,
            needs_rag=False,
            tables_needed=[
                "marketing.campaign_attribution",
                "marketing.marketing_campaigns",
                "ecommerce.orders",
            ],
            embedding_sources=["schema_embeddings"],
            metrics=["total_revenue", "avg_profit_margin", "order_count"],
            time_grain="",
            question_language=language,
            target_source="marketing.campaign_attribution",
            reasoning=(
                "Campaign analysis: JOIN campaign_attribution → marketing_campaigns → orders "
                "to aggregate revenue and profit margin per campaign."
            ),
            group_by=["mc.campaign_id", "mc.campaign_name"],
            order_by=[{"col": "total_revenue", "dir": "DESC"}],
            limit=10,
            select_columns=[
                "mc.campaign_id", "mc.campaign_name",
                "SUM(o.total_revenue) AS total_revenue",
                "AVG(o.profit_margin) AS avg_profit_margin",
                "COUNT(o.order_id) AS order_count",
            ],
        )
        log.info(
            f"[planner] Fast-path intent='campaign_analysis' route='sql_only' score={score:.3f}"
        )
        return plan, 0, 0

    # ----------------------------------------------------------------
    # Web analytics intent — fast path (direct SQL plan, no Groq call)
    # ----------------------------------------------------------------
    # BUG D FIX: Added redundant keyword check here as a safety net.
    # Even if classify_intent() returns general_query (e.g. due to embedding
    # noise or DB unavailability), any query mentioning "website sessions" or
    # the Arabic equivalents will still be forced through this fast path.
    _web_analytics_signal = (
        intent == "web_analytics"
        or any(kw in user_message.lower() for kw in (
            "website sessions", "web sessions", "website traffic", "web traffic",
            "جلسات الموقع", "جلسات موقع",
            "ال sessions", "sessions على", "sessions مبيعات",
            "sessions والارباح", "sessions والأرباح",
            "sessions وتأثير",
        ))
    )
    if _web_analytics_signal:
        plan = QueryPlan(
            intent="web_analytics",
            route="sql_only",
            difficulty="medium",
            needs_sql=True,
            needs_rag=False,
            tables_needed=[
                "marketing.website_sessions",
                "marketing.customer_interactions",
                "marketing.campaign_attribution",
                "marketing.marketing_campaigns",
                "ecommerce.orders",
            ],
            embedding_sources=["schema_embeddings"],
            metrics=["total_sessions", "total_revenue", "avg_profit_margin", "conversion_rate"],
            time_grain="monthly",
            question_language=language,
            target_source="marketing.website_sessions",
            reasoning=(
                "Web analytics: primary=website_sessions JOIN orders for revenue/profit impact. "
                "Fallback chain: customer_interactions (web channel) → campaign_attribution if empty."
            ),
            group_by=["month"],
            order_by=[{"col": "month", "dir": "DESC"}],
            limit=24,
            web_analytics_dimension=_detect_web_dimension(user_message),  # BUG-01 FIX
        )
        log.info(
            f"[planner] Fast-path intent='web_analytics' route='sql_only' score={score:.3f}"
        )
        return plan, 0, 0

    # ----------------------------------------------------------------
    # Deterministic question-pattern fast path (no Groq call, no LLM SQL
    # generation call either — see sql_generator._deterministic_profile_sql)
    # ----------------------------------------------------------------
    # Covers high-confidence, well-understood question families (monthly/
    # quarterly trend, KPI summary, seller/region/payment/shipping/refund
    # ranking, scenario ranking, lookup) using the rule engine in
    # question_patterns.py. Only intercepts queries that would otherwise
    # fall through to the generic Groq-delegation branch below — every
    # fast path above (review, leakage root cause, campaign, web analytics)
    # is untouched and still wins first. Anything below the confidence
    # threshold still goes to Groq exactly as before.
    profile = detect_question_profile(user_message)
    if should_use_profile_fast_path(profile):
        plan = QueryPlan(question_language=language, **profile.to_plan_patch())
        log.info(
            f"[planner] Pattern fast-path family='{profile.family}' "
            f"intent='{plan.intent}' template='{plan.template_key}' "
            f"confidence={profile.confidence:.2f} (classifier intent='{intent}')"
        )
        return plan, 0, 0

    # ----------------------------------------------------------------
    # All other intents — delegate to Groq LLM planner
    # ----------------------------------------------------------------
    plan, in_tok, out_tok = await planner.plan(user_message, memory_context, compressed_schema)

    # Override intent with classifier result when Groq drifts
    if intent not in ("general_query",) and plan.intent in ("general_query", ""):
        plan.intent = intent

    # Safety guardrail: if Groq somehow flagged review intent, apply guardrails
    if plan.intent in ("review_analysis", "sentiment_analysis"):
        plan = _apply_review_guardrails(plan, user_message)

    # Language override: always trust deterministic Unicode detection over Groq's judgment.
    # Groq sometimes returns question_language="english" for Arabic questions, which causes
    # the analytics interpreter to reply in English even when the user wrote in Arabic.
    plan.question_language = language   # `language` was set at the top of plan_query()

    log.info(
        f"[planner] Groq plan: intent='{plan.intent}' route='{plan.route}' "
        f"difficulty='{plan.difficulty}' language='{plan.question_language}'"
    )
    return plan, in_tok, out_tok