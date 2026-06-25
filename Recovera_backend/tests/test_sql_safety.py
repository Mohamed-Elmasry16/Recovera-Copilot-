import pytest

from app.agents.sql_safety import normalize_sql, validate_readonly_shape, validate_table_access
from app.core.schema_registry import init_schema_registry


def test_cte_is_allowed():
    sql = """
    WITH base AS (
      SELECT order_id, total_revenue FROM ml_output.mv_leakage_dashboard
    )
    SELECT order_id FROM base LIMIT 10
    """
    res = validate_readonly_shape(sql)
    assert not res.fatal, res.errors


def test_dml_is_blocked():
    res = validate_readonly_shape("UPDATE ecommerce.orders SET total_revenue = 0")
    assert res.fatal


def test_created_at_substring_not_blocked():
    res = validate_readonly_shape("SELECT created_at FROM rag.documents LIMIT 1")
    assert not res.fatal, res.errors


def test_multiple_statements_blocked():
    res = validate_readonly_shape("SELECT 1; SELECT 2")
    assert res.fatal


@pytest.mark.asyncio
async def test_export_schema_registry_knows_customer_interactions():
    reg = await init_schema_registry(None)
    assert reg.column_exists("marketing.customer_interactions", "interaction_date")
    assert reg.column_exists("marketing.customer_interactions", "action_type")
    assert reg.column_exists("marketing.customer_interactions", "device")
    assert not reg.column_exists("marketing.customer_interactions", "event_timestamp")
    assert not reg.column_exists("marketing.customer_interactions", "is_converted")
