from app.agents.sql_generator import SQLGenerator


def test_deterministic_region_template_is_schema_qualified():
    g = SQLGenerator()
    sql = g._deterministic_profile_sql("ايه اكثر مناطق تحقيق للارباح واكثر المناطق تحقيق للخسائر", {
        "template_key": "dual_region_leakage_revenue",
        "primary_dimension": "customer_city",
        "primary_metric": "leakage_revenue",
        "limit": 10,
    })
    assert "ml_output.mv_leakage_dashboard" in sql
    assert "WITH base_stats" in sql
    assert "UNION ALL" in sql


def test_payment_template_uses_mv_dashboard():
    g = SQLGenerator()
    sql = g._deterministic_profile_sql("COD unpaid orders", {
        "template_key": "payment_leakage",
        "primary_metric": "leakage_revenue",
        "limit": 20,
    })
    assert "FROM ml_output.mv_leakage_dashboard" in sql
    assert "payment_status" in sql
