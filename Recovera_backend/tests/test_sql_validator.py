import pytest

from app.agents.sql_validator import validate_sql
from app.core.schema_registry import init_schema_registry


@pytest.mark.asyncio
async def test_validator_allows_complex_window_cte():
    await init_schema_registry(None)
    sql = """
    WITH base AS (
        SELECT customer_city,
               COUNT(*) AS total_orders,
               SUM(CASE WHEN anomaly_flag = 1 THEN 1 ELSE 0 END) AS leakage_orders,
               ROUND(SUM(CASE WHEN anomaly_flag = 1 THEN 1 ELSE 0 END)::decimal / NULLIF(COUNT(*),0) * 100, 2) AS leakage_rate_pct,
               SUM(total_revenue) AS total_revenue
        FROM ml_output.mv_leakage_dashboard
        GROUP BY customer_city
    ), ranked AS (
        SELECT *,
               ROW_NUMBER() OVER (ORDER BY total_revenue DESC) AS revenue_rank,
               ROW_NUMBER() OVER (ORDER BY leakage_rate_pct DESC) AS leakage_rank
        FROM base
    )
    SELECT 'top_revenue' AS ranking_type, customer_city, total_revenue, leakage_rate_pct
    FROM ranked WHERE revenue_rank <= 5
    UNION ALL
    SELECT 'top_leakage_rate' AS ranking_type, customer_city, total_revenue, leakage_rate_pct
    FROM ranked WHERE leakage_rank <= 5
    ORDER BY ranking_type, total_revenue DESC
    """
    result = await validate_sql(sql, plan={"difficulty": "complex"})
    assert not result.fatal, result.issues


@pytest.mark.asyncio
async def test_validator_blocks_forbidden_schema():
    await init_schema_registry(None)
    result = await validate_sql("SELECT * FROM pg_catalog.pg_tables", plan={})
    assert result.fatal
