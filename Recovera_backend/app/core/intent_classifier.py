"""
intent_classifier.py - Intent Classification Using Embeddings
=============================================================
Classifies user query intent using cosine similarity between query embedding
and pre-computed intent description embeddings (bilingual: Arabic + English).

FIXES:
  - [Bug #1] Threshold lowered 0.60 → 0.50 to handle mixed-language queries
  - [Bug #1] Version-based re-embedding: bumping INTENT_DESCRIPTIONS_VERSION
              forces the table to rebuild without manual DB intervention
  - [Bug #1] Richer bilingual descriptions including Arabic+English mixed patterns
              e.g. "حلل الreviews", "غير مطابق للوصف", "product description mismatch"
  - Added `leakage_root_cause_analysis` as a first-class intent
"""

import logging
from typing import Tuple

import asyncpg

from app.core.config import settings
from app.retrieval.retrieval_engine import _embed_client

log = logging.getLogger(__name__)

# ================================================================
# VERSION — bump this string whenever INTENT_DESCRIPTIONS changes.
# The ensure_intent_embeddings_table() function will detect the mismatch
# and re-embed all intents automatically on next startup.
# ================================================================
INTENT_DESCRIPTIONS_VERSION = "v7"  # fix: web_analytics was tuple (stray comma) → now str

# ================================================================
# BILINGUAL INTENT DESCRIPTIONS
# Rich, diverse examples including Arabic-English mixed patterns
# ================================================================
INTENT_DESCRIPTIONS = {
    "sentiment_analysis": (
        # Arabic
        "تحليل مشاعر العملاء، شكاوى العملاء، الآراء السلبية والإيجابية، "
        "ما يقوله العملاء عن المنتج أو الخدمة، استخراج المشاكل من التقييمات، "
        "الزباين بتشتكي من ايه، شكاوى الزبائن، المراجعات السلبية، "
        "ما هي المشاكل التي يذكرها العملاء، تحليل sentiment المراجعات، "
        "المراجعات السيئة، العملاء غير الراضين، "
        # English
        "Analyze review content, customer complaints, negative feedback, "
        "what customers say about products or services, extract issues from ratings, "
        "customer complaints analysis, review sentiment analysis, negative reviews, "
        "customer feedback, what are customers unhappy about, "
        # Mixed Arabic-English patterns
        "تحليل الsentiment، analyze reviews بالعربي، customer complaints عربي"
    ),
    "review_analysis": (
        # Arabic
        "تحليل المراجعات، تقييمات العملاء، الآراء الإيجابية والسلبية، "
        "ملخص التعليقات، ما يقوله العملاء، استخراج الأنماط من التقييمات، "
        "حلل المراجعات، المراجعات الي بتقول، reviews الي بتشير إلى، "
        "غير مطابق للوصف، المنتج مختلف عما وصف، المنتجات مش زي ما وصفوها، "
        "وصف المنتج غير دقيق، مطابقة الوصف، تحليل تقييمات العملاء، "
        "شكاوى عن المنتج، مراجعات المنتج، ما يشتكي منه العملاء عن الجودة، "
        # English
        "Review analysis, customer ratings, positive and negative feedback, "
        "summarize customer comments, what customers are saying, "
        "pattern extraction from reviews, product description mismatch, "
        "not as described, product different from listing, inaccurate description, "
        "analyze customer reviews, product quality complaints, item not as described, "
        # Mixed
        "حلل الreviews، analyze المراجعات، reviews الي بتقول ان المنتجات، "
        "reviews عن عدم المطابقة، product description غير مطابق، "
        "المنتجات غير مطابقة للوصف، سببت مشاكل في الإيرادات"
    ),
    "leakage_root_cause_analysis": (
        # Arabic
        "تحليل أسباب التسرب، الأسباب الجذرية لخسارة الإيرادات، "
        "لماذا نخسر المال، تحليل مسببات تسرب الإيرادات، "
        "ما هي أسباب الخسائر، فهم مصادر التسرب، "
        # English
        "Root cause analysis of revenue leakage, why are we losing money, "
        "investigate leakage causes, leakage source analysis, "
        "what is causing revenue loss, leakage root cause, "
        "analyze reasons for anomalies, explain revenue leakage patterns"
    ),
    "aggregation": (
        # Arabic
        "حساب الإجماليات، المتوسطات، المقاييس المجمعة، إجمالي الإيرادات، "
        "الربح الإجمالي، عدد الطلبات، نسبة التسرب، مقارنة الأداء، "
        "كم عدد، كم إجمالي، ما هو متوسط، احسب، "
        # English
        "Calculate totals, averages, aggregated metrics, total revenue, "
        "total profit, order counts, leakage rate, performance comparison, "
        "how many, sum of, count orders, aggregate, total amount"
    ),
    "trend_analysis": (
        # Arabic
        "تحليل الاتجاهات، التغيرات بمرور الوقت، مقارنة شهر بشهر، "
        "ربع سنوي، سنوي، نمط التسرب خلال الفترات، "
        "هل يزداد، هل يتحسن، الاتجاه خلال الأشهر، "
        # English
        "Trend analysis, changes over time, month-over-month comparison, "
        "quarterly, yearly, leakage patterns over periods, "
        "is it increasing, historical trend, time series, over months"
    ),
    "anomaly_investigation": (
        # Arabic
        "البحث عن حالات الشذوذ، التسريبات، الاحتيال، الطلبات المشبوهة، "
        "الأسباب الجذرية للتسرب، الطلبات عالية الخطورة، "
        "طلبات غريبة، قيم غير عادية، anomaly، "
        # English
        "Find anomalies, leakage, fraud, suspicious orders, "
        "root causes of leakage, high risk orders, "
        "outliers, unusual patterns, anomaly detection, high risk"
    ),
    "simple_lookup": (
        # Arabic
        "البحث عن طلب معين، تفاصيل عميل، معلومات منتج، "
        "استعلام بسيط بقيمة محددة، ابحث عن، أين، من هو، "
        # English
        "Look up a specific order, customer details, product info, "
        "simple query with a specific value, find, where is, who is, "
        "get details of, show me order, specific ID"
    ),
    "schema_discovery": (
        # Arabic
        "فهم بنية قاعدة البيانات، العلاقات بين الجداول، الأعمدة المتاحة، "
        "كيفية ربط الجداول، ما هي الجداول الموجودة، "
        # English
        "Understand database structure, table relationships, available columns, "
        "how to join tables, what tables exist, schema, ERD, "
        "what columns are available, how is data structured"
    ),
    "kpi_definition": (
        # Arabic
        "تعريف مؤشرات الأداء، شرح المعايير، كيفية حساب الربحية، "
        "ما معنى هامش الربح، كيفية حساب التسرب، تعريف KPI، "
        # English
        "Define KPIs, explain metrics, how to calculate profitability, "
        "what profit margin means, how to calculate leakage, "
        "definition of, what is, explain the metric, KPI meaning"
    ),
    "sql_template_lookup": (
        # Arabic
        "البحث عن استعلام جاهز، كيفية كتابة استعلام لتحليل معين، "
        "قالب SQL للتسرب، "
        # English
        "Find pre-built queries, how to write a query for a specific analysis, "
        "SQL template for leakage, give me a SQL query, "
        "query example, SQL for this analysis"
    ),
    "web_analytics": (
        # Arabic — exact user query patterns observed in production
        "عايزك تحلل ال website sessions وتقولي ايه تأثيرها علي الارباح، "
        "تأثير جلسات الموقع على المبيعات والأرباح، "
        "تحليل حركة الموقع والإيرادات، "
        "ايه تأثير الwebsite sessions على مبيعات السيستم والأرباح، "
        "كيف تؤثر زيارات الموقع على الأرباح، "
        "العلاقة بين جلسات الموقع والمبيعات، "
        "عدد الجلسات ومعدل التحويل والإيرادات، "
        "تحليل أداء الموقع والتحويلات والإيرادات، "
        "الزيارات مقابل المبيعات، جلسات الموقع والمبيعات، "
        "تحليل مسار التحويل على الموقع، "
        "مدة الجلسة ومعدل الارتداد وتأثيرها على المبيعات، "
        "أداء حملات التسويق الرقمي والإيرادات، "
        "website sessions مبيعات أرباح، "
        "زيارات الموقع الإلكتروني وتأثيرها على الربح، "
        "تحليل جلسات الموقع وعلاقتها بالأرباح، "
        "عايز أعرف تأثير الجلسات على الأرباح، "
        "ايه اللي بتعمله الجلسات للمبيعات، "
        "زوار الموقع وعلاقتهم بالإيرادات، "
        "ترافيك الموقع والتحويلات، "
        "كام session عندنا وبيتحولوا لكام طلب، "
        # English
        "Impact of website sessions on sales and profit, "
        "website traffic and revenue analysis, "
        "how sessions affect conversion and revenue, "
        "session count vs total revenue correlation, "
        "web analytics impact on system sales, "
        "conversion rate from website sessions to orders, "
        "website traffic revenue profit analysis, "
        "digital marketing session revenue impact, "
        "sessions sales profit correlation, "
        "website sessions ecommerce sales performance, "
        "bounce rate session duration and revenue, "
        "web sessions order conversion analysis, "
        "analyze website sessions and their effect on profits, "
        "what is the impact of sessions on revenue, "
        "website visit to purchase conversion funnel, "
        "traffic source revenue breakdown, "
        # Mixed Arabic-English (most common real-world pattern)
        "website sessions على المبيعات، sessions مبيعات، "
        "تأثير sessions على الأرباح، web traffic وإيرادات، "
        "تحلل ال website sessions وتأثيرها، "
        "sessions وتأثيرها على الأرباح، "
        "analyze ال sessions وتأثيرها على الإيرادات، "
        "website sessions وعلاقتها بالمبيعات والأرباح"
        # customer_interactions as web analytics proxy
        "تحليل تفاعل العملاء الرقمي وتأثيره على الإيرادات، "
        "customer interactions impact on revenue and profit, "
        "channel events sales correlation, "
        "تأثير الحملات الإعلانية على الأرباح، "
        "campaign attribution revenue analysis, "
        "digital channel performance vs revenue"
    ),
    "campaign_analysis": (
        # Arabic — direct profitability / ROI questions
        "أفضل حملة تسويقية، أعلى إيرادات حملة، أكثر حملة ربحًا، "
        "أكتر حملة حققت أرباح، مقارنة أداء الحملات، أي حملة الأفضل، "
        "عائد الاستثمار للحملات، تحليل أداء الحملات، ROI الحملات، "
        "إيرادات الحملات التسويقية، ربحية الحملات، أفضل قناة إعلانية، "
        "أعلى هامش ربح حملة، كفاءة الإنفاق الإعلاني، أداء الحملة، "
        "تكلفة الاكتساب، الحملة الأكثر مبيعًا، أي حملة جلبت أكتر طلبات، "
        # English
        "Best performing marketing campaign, highest revenue campaign, "
        "most profitable campaign, campaign ROI analysis, compare campaigns, "
        "campaign performance ranking, which campaign generated most revenue, "
        "campaign attribution analysis, top campaigns by profit margin, "
        "marketing channel efficiency, campaign cost per acquisition, "
        "campaign conversion rate, ad spend effectiveness, "
        "which campaign had highest orders, campaign profitability comparison, "
        # Mixed Arabic-English patterns (common in production)
        "أكتر marketing campaign حققت أرباح، best campaign بالعربي، "
        "campaign performance عربي، أي campaign أفضل، "
        "حملات Google vs Snapchat vs Instagram، "
        "campaign revenue analysis عربي، أداء الcampaigns"
    ),
    "general_query": (
        # Arabic
        "أسئلة عامة لا تنتمي لتصنيف محدد، تحية، مساعدة، تعريف بالمنصة، "
        "مرحبا، كيف حالك، شكراً، ما هي إمكانياتك، "
        # English
        "General questions not belonging to a specific category, greetings, help, "
        "platform introduction, hello, what can you do, capabilities, "
        "hi, thanks, who are you"
    ),
}


# ================================================================
# KEYWORD SHORTCUTS — BUG C FIX
# ================================================================
# For high-confidence signal words, bypass the embedding entirely.
# These are cases where the query is UNAMBIGUOUS regardless of phrasing.
# Runs before cosine similarity — O(n keywords) not O(vector distance).
#
# Format: { intent_name: [list of keyword fragments (lowercase)] }
# A match fires if ANY fragment is a substring of the lowercased query.
# ================================================================
_KEYWORD_SHORTCUTS: dict[str, list[str]] = {
    "campaign_analysis": [
        # English — unambiguous campaign profitability signals
        "best campaign",
        "top campaign",
        "most profitable campaign",
        "highest revenue campaign",
        "campaign roi",
        "campaign performance",
        "campaign profitab",
        "campaign ranking",
        "which campaign",
        "compare campaign",
        "campaign attribution",
        # Arabic
        "أفضل حملة",
        "أعلى إيرادات حملة",
        "أكثر حملة ربحًا",
        "ربحية الحملات",
        "أداء الحملات",
        "مقارنة الحملات",
        "عائد الحملة",
        # Mixed
        "أكتر marketing campaign",
        "أكتر campaign",
        "best marketing campaign",
        "أفضل marketing campaign",
    ],
    "web_analytics": [
        # English — any mention of sessions in a web context
        "website sessions",
        "web sessions",
        "website traffic",
        "web traffic",
        "session impact",
        "sessions impact",
        "sessions on profit",
        "sessions on revenue",
        "sessions on sales",
        "customer interactions",
        "channel events",
        # Arabic
        "جلسات الموقع",
        "جلسات موقع",
        "جلسة الموقع",
        "تفاعل العملاء على",
        "تأثير الحملات على",
        "حملات التسويق على الارباح",
        # Mixed patterns — most common in production
        "website session",   # catches "website sessions" too
        "ال sessions",       # "تأثير ال sessions" / "تحلل ال sessions"
        "الsessions",        # no space variant
        "sessions على",      # "sessions على المبيعات"
        "sessions وتأثير",   # "sessions وتأثيرها"
        "sessions مبيعات",
        "sessions والارباح",
        "sessions والأرباح",
        "sessions وإيرادات",
        "sessions وايرادات",
        "ال website",        # catches "ال website sessions"
    ],
}


def _apply_keyword_shortcut(query_text: str) -> "tuple[str, float] | None":
    """
    BUG C FIX: Check for unambiguous keyword signals BEFORE embedding.

    Returns (intent_name, 1.0) if a shortcut matches, otherwise None.
    This prevents misclassification when query phrasing mixes multiple
    intent signals — e.g. "تحلل ال website sessions وتأثيرها على الارباح"
    contains "تحلل" (→ review_analysis) AND "الارباح" (→ aggregation)
    which can pull cosine similarity away from web_analytics.
    """
    lower = query_text.lower()
    for intent_name, keywords in _KEYWORD_SHORTCUTS.items():
        for kw in keywords:
            if kw in lower:
                log.info(
                    f"[intent_classifier] Keyword shortcut: '{kw}' matched → "
                    f"intent='{intent_name}' (bypassing embedding)"
                )
                return intent_name, 1.0
    return None


async def ensure_intent_embeddings_table(pool: asyncpg.Pool):
    """
    Create intent_embeddings table if not exists and populate with embeddings.
    Automatically re-embeds all intents when INTENT_DESCRIPTIONS_VERSION changes.
    Safe to call multiple times (idempotent).
    """
    async with pool.acquire() as conn:
        # 1. Create table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS rag.intent_embeddings (
                intent_name TEXT PRIMARY KEY,
                description TEXT NOT NULL,
                embedding vector(1024) NOT NULL,
                created_at TIMESTAMP DEFAULT NOW()
            );
        """)

        # 2. Add version column if missing (backward-compatible migration)
        await conn.execute("""
            ALTER TABLE rag.intent_embeddings
            ADD COLUMN IF NOT EXISTS version TEXT DEFAULT 'v1';
        """)

        # 3. Check how many intents match current version
        count_current = await conn.fetchval(
            "SELECT COUNT(*) FROM rag.intent_embeddings WHERE version = $1",
            INTENT_DESCRIPTIONS_VERSION,
        )
        total_intents = len(INTENT_DESCRIPTIONS)

        if count_current >= total_intents:
            log.debug(
                f"Intent embeddings up-to-date ({count_current}/{total_intents}, "
                f"version={INTENT_DESCRIPTIONS_VERSION})"
            )
            return

        # 4. Re-embed all intents (UPSERT so it's safe to run concurrently)
        log.info(
            f"[intent_classifier] Re-embedding intents "
            f"({count_current}/{total_intents} match version {INTENT_DESCRIPTIONS_VERSION}). "
            f"Embedding {total_intents} intents..."
        )
        for intent_name, desc in INTENT_DESCRIPTIONS.items():
            try:
                vector = await _embed_client.embed(desc)
                vector_str = "[" + ",".join(map(str, vector)) + "]"
                await conn.execute(
                    """
                    INSERT INTO rag.intent_embeddings
                        (intent_name, description, embedding, version)
                    VALUES ($1, $2, $3::vector, $4)
                    ON CONFLICT (intent_name) DO UPDATE SET
                        description = EXCLUDED.description,
                        embedding   = EXCLUDED.embedding,
                        version     = EXCLUDED.version,
                        created_at  = NOW()
                    """,
                    intent_name,
                    desc,
                    vector_str,
                    INTENT_DESCRIPTIONS_VERSION,
                )
                log.debug(f"  [OK] Embedded intent: {intent_name}")
            except Exception as e:
                log.error(f"  [FAIL] Could not embed intent '{intent_name}': {e}")

        log.info(
            f"[intent_classifier] Intent embeddings initialized "
            f"({total_intents} intents, version={INTENT_DESCRIPTIONS_VERSION})"
        )


async def classify_intent(
    pool: asyncpg.Pool,
    query_text: str,
    similarity_threshold: float = 0.50,   # FIX: was 0.60 — too strict for mixed-language queries
) -> Tuple[str, float]:
    """
    Classify query intent using vector similarity.

    Returns (intent_name, similarity_score).

    Falls back to "general_query" when score < similarity_threshold.
    The threshold is intentionally kept at 0.50 to handle:
      - Arabic-only queries
      - English-only queries
      - Mixed Arabic+English queries (e.g. "حلل الreviews")
      - Colloquial Egyptian Arabic (e.g. "الزباين بتشتكي")
    """
    await ensure_intent_embeddings_table(pool)

    # ── BUG C FIX: keyword shortcut — check before embedding ──────────────────
    # For queries with unambiguous signal words (e.g. "website sessions"),
    # return immediately without the DB vector search.  This prevents
    # misclassification caused by competing intent signals in the same query.
    shortcut = _apply_keyword_shortcut(query_text)
    if shortcut:
        return shortcut
    # ──────────────────────────────────────────────────────────────────────────

    try:
        query_vector = await _embed_client.embed(query_text)
    except Exception as e:
        log.warning(f"[intent_classifier] Embedding failed for query: {e}")
        return "general_query", 0.0

    query_vector_str = "[" + ",".join(map(str, query_vector)) + "]"

    async with pool.acquire() as conn:
        # Fetch the single best match and the runner-up for confidence delta
        rows = await conn.fetch(
            """
            SELECT intent_name,
                   1 - (embedding <=> $1::vector) AS similarity
            FROM rag.intent_embeddings
            ORDER BY embedding <=> $1::vector
            LIMIT 2
            """,
            query_vector_str,
        )

        if not rows:
            log.warning("[intent_classifier] No intent embeddings found in table.")
            return "general_query", 0.0

        best = rows[0]
        intent_name = best["intent_name"]
        sim = float(best["similarity"])

        # Optional: log runner-up for debugging
        if len(rows) > 1:
            runner_up = rows[1]
            delta = sim - float(runner_up["similarity"])
            log.debug(
                f"[intent_classifier] best='{intent_name}' sim={sim:.3f} "
                f"runner_up='{runner_up['intent_name']}' delta={delta:.3f}"
            )

        if sim >= similarity_threshold:
            # BUG-NEW-02 FIX: Add ambiguity guard — if the gap between best and runner-up
            # is too small, the signal is too weak to trust for fast-path routing.
            # Route to general_query instead so Groq makes the final call.
            AMBIGUITY_DELTA = 0.06
            if len(rows) > 1:
                runner_up = rows[1]
                delta = sim - float(runner_up["similarity"])
                if delta < AMBIGUITY_DELTA:
                    log.info(
                        f"[intent_classifier] Ambiguous: '{intent_name}' sim={sim:.3f} "
                        f"runner_up='{runner_up['intent_name']}' delta={delta:.3f} "
                        f"< {AMBIGUITY_DELTA} → returning general_query"
                    )
                    return "general_query", sim
            log.info(
                f"[intent_classifier] Classified as '{intent_name}' "
                f"(similarity={sim:.3f}, threshold={similarity_threshold})"
            )
            return intent_name, sim
        else:
            log.info(
                f"[intent_classifier] Score {sim:.3f} < threshold {similarity_threshold} "
                f"for best match '{intent_name}' → falling back to general_query"
            )
            return "general_query", sim

    return "general_query", 0.0