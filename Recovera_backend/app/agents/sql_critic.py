"""Non-fatal SQL quality critic.

These checks improve analytical correctness but must not block execution.  The
validator blocks only safety/schema/compiler failures; this module emits warnings
and optional rewrite hints.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import sqlglot
import sqlglot.expressions as exp


@dataclass
class CriticResult:
    warnings: list[str] = field(default_factory=list)
    rewrite_hints: list[str] = field(default_factory=list)

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def hint(self, message: str) -> None:
        self.rewrite_hints.append(message)


def critique_sql(sql: str) -> CriticResult:
    res = CriticResult()
    lower = sql.lower()

    if re.search(r"avg_discount_pct\s*[><=!]+\s*(?:[5-9]\d|\d{3,}|[2-9](?:\.\d+)?)", lower):
        res.warn("[discount] avg_discount_pct is a decimal 0.0-1.0; use > 0.50, not > 50.")

    if "ecommerce.payments" in lower and "payment_sequential" not in lower:
        res.warn("[payments] ecommerce.payments may contain multiple rows per order; use payment_sequential = 1 when selecting primary method.")

    if "ecommerce.reviews" in lower and "lateral" not in lower:
        res.warn("[reviews] Direct reviews joins can duplicate orders. Prefer ml_output.mv_leakage_dashboard or LATERAL latest-review join.")

    if re.search(r"date_trunc\s*\([^)]*order_month", lower):
        res.warn("[generated] order_month is precomputed. Group by order_month directly instead of DATE_TRUNC(order_month).")
        res.hint("Replace DATE_TRUNC(... order_month ...) with order_month.")

    order_match = re.search(r"order\s+by\s+(.+?)(?:limit|;|$)", lower, re.S)
    if order_match:
        order_clause = order_match.group(1)
        has_abs_leakage = bool(re.search(r"leakage_amount|leakage_revenue|revenue_at_risk|sum\s*\(.*total_revenue", order_clause, re.S))
        has_rate = "leakage_rate_pct" in order_clause
        if has_abs_leakage and not has_rate:
            res.warn("[ranking] Absolute leakage amount biases toward large segments. Use leakage_rate_pct unless user asked for total EGP lost.")

    try:
        tree = sqlglot.parse_one(sql, read="postgres")
        select_star = any(isinstance(p, exp.Star) for p in tree.find_all(exp.Star))
        if select_star and "limit" not in lower:
            res.warn("[select_star] SELECT * without LIMIT can be expensive and verbose.")
    except Exception:
        pass

    return res
