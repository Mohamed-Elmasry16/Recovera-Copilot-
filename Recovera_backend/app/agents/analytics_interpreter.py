"""
analytics_interpreter.py - Analytics Interpreter Agent (MiniMax)
=================================================================
Model: gamma
Temperature: 0.4
Purpose: Explain business meaning from SQL results

Behaves like:
  - Senior business analyst
  - Fraud investigator
  - BI analyst
  - Finance intelligence assistant

Response Structure (maps directly to parseAnalysis() card slots):
  1. Key Finding      → النتيجة الرئيسية
  2. Business Impact  → التأثير التجاري
  3. Evidence         → الأدلة
  4. Recommendation   → التوصية
"""

import json
import logging
import re
from typing import Optional, Any

from app.core.multi_key_router import call_llm
from app.core.question_patterns import is_forecast_question

log = logging.getLogger(__name__)

# ================================================================
# FRONTEND CARD CONTRACT
# ================================================================

_SECTION_HEADER_ALIASES = {
    "Key Finding": [
        "Key Finding / النتيجة الرئيسية",
        "Key Finding",
        "النتيجة الرئيسية",
    ],
    "Business Impact": [
        "Business Impact / التأثير التجاري",
        "Business Impact",
        "التأثير التجاري",
    ],
    "Evidence": [
        "Evidence / الأدلة",
        "Evidence",
        "الأدلة",
    ],
    "Recommendation": [
        "Recommendation / التوصية",
        "Recommendation",
        "التوصية",
    ],
}


def _normalize_analysis_headers(content: str) -> str:
    """
    Keep the response compatible with the browser's parseAnalysis() function.

    The frontend turns sections into cards only when it sees headers such as
    "## Key Finding". The previous prompt asked the LLM to emit bilingual
    headers like "## Key Finding / النتيجة الرئيسية"; the slash made the
    regex miss the section, so the answer stayed plain markdown instead of
    rendering cards. This post-processing makes the contract deterministic
    even if the model returns bilingual or Arabic-only labels.
    """
    if not content:
        return content

    normalized = content
    for canonical, aliases in _SECTION_HEADER_ALIASES.items():
        for alias in aliases:
            # Supports both standalone headers and "## Header: text" forms.
            pattern = re.compile(
                r"^\s*#{1,3}\s*" + re.escape(alias) + r"\s*[:：]?\s*(.*)$",
                flags=re.IGNORECASE | re.MULTILINE,
            )

            def repl(match, canonical=canonical):
                tail = match.group(1).strip()
                return f"## {canonical}" + (f"\n{tail}" if tail else "")

            normalized = pattern.sub(repl, normalized)

    return normalized.strip()


_MONTH_NAMES_AR = {
    1: "يناير", 2: "فبراير", 3: "مارس", 4: "أبريل", 5: "مايو", 6: "يونيو",
    7: "يوليو", 8: "أغسطس", 9: "سبتمبر", 10: "أكتوبر", 11: "نوفمبر", 12: "ديسمبر",
}
_MONTH_NAMES_EN = {
    1: "January", 2: "February", 3: "March", 4: "April", 5: "May", 6: "June",
    7: "July", 8: "August", 9: "September", 10: "October", 11: "November", 12: "December",
}


def _is_arabic_text(text: str) -> bool:
    return any("\u0600" <= ch <= "\u06FF" for ch in text or "")


def _num(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        cleaned = str(value).strip().replace(",", "").replace("%", "")
        return float(cleaned)
    except Exception:
        return None


def _fmt_number(value: float, *, money: bool = False, pct: bool = False, arabic: bool = False) -> str:
    if money:
        return f"EGP {value:,.2f}"
    if pct:
        return f"{value:,.2f}%"
    if abs(value) >= 1000:
        return f"{value:,.2f}"
    return f"{value:.2f}"


def _period_label(value: Any, arabic: bool) -> str:
    raw = str(value or "").strip()
    try:
        month_num = int(float(raw))
        if 1 <= month_num <= 12:
            return (_MONTH_NAMES_AR if arabic else _MONTH_NAMES_EN)[month_num]
    except Exception:
        pass
    return raw


def _pick_time_col(rows: list[dict]) -> Optional[str]:
    if not rows:
        return None
    lower = {str(k).lower(): str(k) for k in rows[0].keys()}
    for name in ("month_label", "month", "order_month", "order_quarter", "quarter", "year", "period", "date"):
        if name in lower:
            return lower[name]
    return None


def _pick_primary_metric(rows: list[dict], plan: dict, user_message: str) -> Optional[str]:
    if not rows:
        return None
    lower = {str(k).lower(): str(k) for k in rows[0].keys()}
    primary = str((plan or {}).get("primary_metric") or "").lower()
    if primary in lower and any(_num(r.get(lower[primary])) is not None for r in rows):
        return lower[primary]
    text = (user_message or "").lower()
    if any(w in text for w in ("profit", "أرباح", "ارباح", "ربح")):
        for name in ("total_profit", "net_profit", "gross_profit"):
            if name in lower:
                return lower[name]
    if any(w in text for w in ("loss", "خسائر", "خسارة", "leakage")):
        for name in ("revenue_at_risk", "leakage_revenue", "gross_loss"):
            if name in lower:
                return lower[name]
    if any(w in text for w in ("revenue", "sales", "مبيعات", "إيرادات", "ايرادات")):
        for name in ("total_revenue", "revenue"):
            if name in lower:
                return lower[name]
    for name in ("total_revenue", "revenue_at_risk", "total_profit", "leakage_revenue", "total_orders", "leakage_orders"):
        if name in lower:
            return lower[name]
    for key in rows[0].keys():
        if _num(rows[0].get(key)) is not None and str(key).lower() not in {"month", "year", "rank"}:
            return str(key)
    return None


def _metric_display_name(metric: str, arabic: bool) -> str:
    names_ar = {
        "total_revenue": "الإيرادات", "revenue": "الإيرادات", "revenue_at_risk": "الإيرادات المعرضة للتسرب",
        "leakage_revenue": "إيرادات التسرب", "total_profit": "الأرباح", "net_profit": "صافي الربح",
        "gross_profit": "إجمالي الأرباح", "gross_loss": "إجمالي الخسائر", "total_orders": "عدد الطلبات",
        "leakage_orders": "طلبات التسرب", "leakage_rate_pct": "معدل التسرب",
    }
    names_en = {
        "total_revenue": "revenue", "revenue": "revenue", "revenue_at_risk": "revenue at risk",
        "leakage_revenue": "leakage revenue", "total_profit": "profit", "net_profit": "net profit",
        "gross_profit": "gross profit", "gross_loss": "gross loss", "total_orders": "orders",
        "leakage_orders": "leakage orders", "leakage_rate_pct": "leakage rate",
    }
    return (names_ar if arabic else names_en).get(metric.lower(), metric.replace("_", " "))


def _deterministic_trend_answer(user_message: str, sql_results: list[dict], plan: dict) -> Optional[str]:
    if not sql_results or len(sql_results) < 2:
        return None
    # FORECAST FIX: this template only ever describes min/max/total of the
    # historical rows — it has no forward-looking language at all. A
    # "forecast/predict next quarter…" question must go through the LLM
    # path instead, which now gets a real computed linear projection
    # appended to its prompt (see orchestrator._linear_forecast). Without
    # this bail-out, forecast questions were silently answered with pure
    # history before the LLM (or the projection) was ever consulted.
    if is_forecast_question(user_message):
        return None
    intent = str((plan or {}).get("intent") or "")
    text = user_message or ""
    trend_words = ("trend", "monthly", "quarterly", "month", "sales", "revenue", "اتجاه", "شهري", "شهور", "شهر", "مبيعات", "إيرادات", "ايرادات")
    if intent not in {"trend_analysis", "revenue_analysis"} and not any(w in text.lower() for w in trend_words):
        return None
    time_col = _pick_time_col(sql_results)
    metric_col = _pick_primary_metric(sql_results, plan, user_message)
    if not time_col or not metric_col:
        return None

    values: list[tuple[str, float]] = []
    arabic = _is_arabic_text(user_message) or str((plan or {}).get("question_language") or "") == "arabic"
    for row in sql_results:
        v = _num(row.get(metric_col))
        if v is None:
            continue
        values.append((_period_label(row.get(time_col), arabic), v))
    if len(values) < 2:
        return None

    max_period, max_val = max(values, key=lambda x: x[1])
    min_period, min_val = min(values, key=lambda x: x[1])
    total = sum(v for _, v in values)
    avg = total / len(values)
    diff = max_val - min_val
    is_money = any(k in metric_col.lower() for k in ("revenue", "profit", "loss", "amount", "risk"))
    is_pct = "pct" in metric_col.lower() or "rate" in metric_col.lower() or "margin" in metric_col.lower()
    metric_name = _metric_display_name(metric_col, arabic)

    if arabic:
        return _normalize_analysis_headers(f"""## Key Finding
بلغ إجمالي {metric_name} خلال الفترة المعروضة {_fmt_number(total, money=is_money)}، مع أعلى قيمة في {max_period} بقيمة {_fmt_number(max_val, money=is_money, pct=is_pct)} وأقل قيمة في {min_period} بقيمة {_fmt_number(min_val, money=is_money, pct=is_pct)}.

## Business Impact
- أعلى شهر/فترة: {max_period} بقيمة {_fmt_number(max_val, money=is_money, pct=is_pct)}.
- أقل شهر/فترة: {min_period} بقيمة {_fmt_number(min_val, money=is_money, pct=is_pct)}.
- متوسط الفترة: {_fmt_number(avg, money=is_money, pct=is_pct)}.
- الفارق بين أعلى وأقل قيمة: {_fmt_number(diff, money=is_money, pct=is_pct)}.

## Evidence
- {max_period}: {_fmt_number(max_val, money=is_money, pct=is_pct)}.
- {min_period}: {_fmt_number(min_val, money=is_money, pct=is_pct)}.
- عدد الفترات المحللة: {len(values)}.

## Recommendation
1. راجع العوامل المرتبطة بفترة {max_period} لتحديد ما يمكن تكراره.
2. افحص فترة {min_period} لأنها تمثل أدنى أداء واضح في النتائج.
3. قارن العروض والقنوات والمنتجات خلال الفترتين قبل اتخاذ قرار تشغيلي.
""")

    return _normalize_analysis_headers(f"""## Key Finding
Total {metric_name} across the displayed periods was {_fmt_number(total, money=is_money)}, with the highest value in {max_period} at {_fmt_number(max_val, money=is_money, pct=is_pct)} and the lowest value in {min_period} at {_fmt_number(min_val, money=is_money, pct=is_pct)}.

## Business Impact
- Highest period: {max_period} with {_fmt_number(max_val, money=is_money, pct=is_pct)}.
- Lowest period: {min_period} with {_fmt_number(min_val, money=is_money, pct=is_pct)}.
- Average period value: {_fmt_number(avg, money=is_money, pct=is_pct)}.
- Gap between highest and lowest: {_fmt_number(diff, money=is_money, pct=is_pct)}.

## Evidence
- {max_period}: {_fmt_number(max_val, money=is_money, pct=is_pct)}.
- {min_period}: {_fmt_number(min_val, money=is_money, pct=is_pct)}.
- Periods analyzed: {len(values)}.

## Recommendation
1. Review the drivers behind {max_period} to identify repeatable factors.
2. Investigate {min_period} because it is the lowest point in the returned data.
3. Compare campaigns, products, and channels across the two periods before taking action.
""")

# ================================================================
# ANALYTICS PROMPT
# ================================================================

ANALYTICS_SYSTEM_PROMPT = """You are a senior business intelligence analyst for an enterprise e-commerce analytics platform.

════════════════════════════════════════════
CRITICAL — DATA FIDELITY RULES (ABSOLUTE)
════════════════════════════════════════════

USE ONLY values explicitly present in the provided dataset.
NEVER infer causes, business explanations, or external context not present in data.
NEVER assume "loss", "profit", or "risk" semantics unless explicitly defined in metrics metadata.
NEVER perform root-cause analysis unless supporting variables exist in the dataset.
NEVER compare against historical or external benchmarks unless included in the result set.

════════════════════════════════════════════
METRIC SEMANTICS RULE (VERY IMPORTANT)
════════════════════════════════════════════
Interpret metrics strictly as follows:

total_revenue → actual recorded revenue
leakage_rate_pct → risk indicator (NOT financial loss)
leakage_revenue → revenue associated with anomalous/leakage events (not guaranteed loss unless explicitly defined)
leakage_orders → count of affected orders

IMPORTANT:
Do NOT reinterpret leakage metrics as "confirmed losses" unless the system explicitly labels them as "lost_revenue".

════════════════════════════════════════════
NUMBER FORMATTING RULES
════════════════════════════════════════════

Monetary values → append EGP
Percentages:
*_pct columns → display as-is with %
ratios (0–1) → multiply by 100 and add %
Never show raw decimals for ratios

════════════════════════════════════════════
OUTPUT STRUCTURE (STRICT — 4 SECTIONS ONLY)
════════════════════════════════════════════

You MUST output EXACTLY these four section headers in this order.
Each header must be on its own line, prefixed with ##.
These exact strings trigger card rendering in the UI — do not alter them.
Use English section headers even when the body language is Arabic.

## Key Finding

1–2 sentences only.
The single most important observable fact from the data.
Include the timeframe only if explicitly present in the results.
No interpretation beyond what the numbers directly show.

## Business Impact

2–4 bullet points.
Quantified impact: values, counts, rates — all from the dataset.
If leakage_rate_pct > 10%: flag as elevated risk indicator.
If multiple metrics present: rank by magnitude.
No assumed causation.

## Evidence

Direct supporting data from the result set.
Reference specific rows, aggregates, or computed values.
Format as bullet points with actual numbers.
Must substantiate the Key Finding above.

## Recommendation

3–5 numbered, actionable recommendations.
Each must be directly derivable from an observed pattern.
If cause is unknown → recommend further analysis, not an explanation.
No assumptions about operational causes (logistics, marketing, etc.).
No recommendations without evidentiary support in Evidence.

════════════════════════════════════════════
STRICT LANGUAGE RULES
════════════════════════════════════════════

Arabic question → formal Arabic only (all four section headers still appear exactly as written above, bilingual)
English question → English only
Never mention SQL, tables, queries, or database structure
Avoid repeated phrases like "the data shows"
Do not over-explain

════════════════════════════════════════════
ANTI-HALLUCINATION RULE
════════════════════════════════════════════
If a conclusion requires missing context:
→ State: "لا يمكن استنتاج السبب من البيانات الحالية"
→ Do NOT guess

════════════════════════════════════════════
OUTPUT END RULE
════════════════════════════════════════════
"## Recommendation" is the FINAL section. Nothing after it.
"""

class AnalyticsInterpreter:
    """Analytics interpreter using MiniMax via OpenRouter."""

    async def interpret(
        self,
        user_message: str,
        sql_results: list[dict],
        plan: dict,
        compressed_context: str = "",
        row_count: int = 0,
        execution_ms: int = 0,
    ) -> tuple[str, int, int]:
        """
        Generate business interpretation of SQL results.
        Returns (answer, input_tokens, output_tokens).
        """
        deterministic = _deterministic_trend_answer(user_message, sql_results, plan or {})
        if deterministic:
            log.info("[analytics] deterministic trend answer used metric-grounded min/max")
            return deterministic, 0, 0

        language = plan.get("question_language", "english")
        lang_instruction = (
            "CRITICAL: يجب الرد بالعربية الرسمية فقط في محتوى الأقسام. "
            "أبقِ رؤوس الأقسام الأربعة الإنجليزية كما هي بالضبط لأنها مطلوبة لواجهة المستخدم."
            if language == "arabic"
            else "Respond in English."
        )

        # Build results context — formatted as a readable table (easier for LLM)
        if sql_results:
            preview = sql_results[:50]
            note = (f"({row_count} rows total, showing {len(preview)})"
                    if row_count > len(preview) else f"({row_count} rows)")
            # Build markdown table for clarity
            if preview:
                headers = list(preview[0].keys())
                header_row = " | ".join(headers)
                sep_row   = " | ".join(["---"] * len(headers))
                data_rows = [
                    " | ".join(str(row.get(h, "")) for h in headers)
                    for row in preview
                ]
                table = "\n".join([header_row, sep_row] + data_rows)
                results_text = f"Query Results {note}:\n\n{table}"
            else:
                results_text = f"Query Results {note}: (empty)"
        else:
            results_text = "The query returned 0 rows."

        # Build user prompt
        user_prompt = f"""Original Question: {user_message}

{results_text}

Metrics Analyzed: {', '.join(plan.get('metrics', []))}
Tables Used: {', '.join(plan.get('tables_needed', []))}

Provide a comprehensive business analysis following the required 4-section format.
{lang_instruction}"""

        if compressed_context:
            user_prompt += f"\n\nBusiness Context:\n{compressed_context}"

        messages = [
            {"role": "system", "content": ANALYTICS_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        content, in_tok, out_tok = await call_llm(
            agent="analytics",
            messages=messages,
            temperature=0.1,
            max_tokens=2048,
        )

        # Strip <think>...</think> blocks emitted by reasoning models (e.g. nemotron)
        if "<think>" in content:
            content = content.split("</think>")[-1].strip()

        content = _normalize_analysis_headers(content)
        return content, in_tok, out_tok

    async def interpret_direct(
        self,
        user_message: str,
        plan: dict,
        compressed_context: str = "",
    ) -> tuple[str, int, int]:
        """
        For RAG-only or non-database queries - answer without SQL results.
        """
        language = plan.get("question_language", "english")
        lang_instruction = (
            "CRITICAL: يجب الرد بالعربية الرسمية فقط — ممنوع استخدام أي كلمة إنجليزية."
            if language == "arabic"
            else "Respond in English."
        )

        user_prompt = f"""Question: {user_message}

{lang_instruction}

Provide a clear, informative answer. If this is a definition/explanation question, be thorough but concise."""

        if compressed_context:
            user_prompt += f"\n\nReference Context:\n{compressed_context}"

        messages = [
            {"role": "system", "content": ANALYTICS_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        content, in_tok, out_tok = await call_llm(
            agent="analytics",
            messages=messages,
            temperature=0.1,
            max_tokens=1024,
        )

        # Strip <think>...</think> blocks emitted by reasoning models (e.g. nemotron)
        if "<think>" in content:
            content = content.split("</think>")[-1].strip()

        content = _normalize_analysis_headers(content)
        return content, in_tok, out_tok


# Global instance
interpreter = AnalyticsInterpreter()


async def interpret_results(
    user_message: str,
    sql_results: list[dict],
    plan: dict,
    compressed_context: str = "",
    row_count: int = 0,
    execution_ms: int = 0,
) -> tuple[str, int, int]:
    """Public API for analytics interpretation."""
    return await interpreter.interpret(user_message, sql_results, plan, compressed_context, row_count, execution_ms)


async def interpret_direct(
    user_message: str,
    plan: dict,
    compressed_context: str = "",
) -> tuple[str, int, int]:
    """Public API for direct interpretation without SQL."""
    return await interpreter.interpret_direct(user_message, plan, compressed_context)