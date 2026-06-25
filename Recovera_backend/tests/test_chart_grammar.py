from app.core.chart_grammar import build_chart_data


def test_dual_ranking_uses_region_labels_and_one_metric_family():
    rows = [
        {"ranking_type": "highest_revenue", "rank": 1, "region": "Cairo", "total_revenue": 32000000, "leakage_revenue": 2800000, "leakage_orders": 533, "leakage_rate_pct": 5.57},
        {"ranking_type": "highest_leakage_revenue", "rank": 1, "region": "Giza", "total_revenue": 25000000, "leakage_revenue": 3100000, "leakage_orders": 610, "leakage_rate_pct": 6.2},
    ]
    plan = {"intent": "dual_ranking", "primary_metric": "leakage_revenue", "chart_policy": {"type": "bar"}}
    chart = build_chart_data(rows, plan, "ايه اكتر مناطق تحقيق للايرادات واكتر مناطق تحقيق للخسائر")
    assert chart is not None
    assert chart["type"] == "bar"
    assert chart["unit_family"] == "money"
    assert chart["labels"][0].startswith("Cairo")
    assert "highest_revenue" not in chart["labels"][0]
    assert chart["value_cols"] == ["leakage_revenue"]
    assert len(chart["datasets"]) == 1


def test_monthly_trend_uses_line_and_one_money_metric():
    rows = [
        {"month": "2024-01-01", "month_label": "Jan 2024", "total_revenue": 100000, "revenue_at_risk": 12000, "leakage_rate_pct": 5.1},
        {"month": "2024-02-01", "month_label": "Feb 2024", "total_revenue": 110000, "revenue_at_risk": 15000, "leakage_rate_pct": 5.5},
    ]
    plan = {
        "intent": "trend_analysis",
        "primary_metric": "revenue_at_risk",
        "chart_policy": {"label": "month_label", "metrics": ["revenue_at_risk"], "unit_family": "money", "type": "line"},
    }
    chart = build_chart_data(rows, plan, "show monthly leakage trend")
    assert chart is not None
    assert chart["type"] == "line"
    assert chart["labels"] == ["Jan 2024", "Feb 2024"]
    assert chart["value_cols"] == ["revenue_at_risk"]
    assert chart["unit_family"] == "money"


def test_web_sessions_does_not_mix_count_and_rate():
    rows = [
        {"month": "2024-01-01", "total_sessions": 1000, "converted_orders": 80, "conversion_rate_pct": 8.0, "revenue_per_session": 12.5},
        {"month": "2024-02-01", "total_sessions": 1200, "converted_orders": 96, "conversion_rate_pct": 8.0, "revenue_per_session": 13.2},
    ]
    plan = {
        "intent": "web_analytics",
        "chart_policy": {"label": "month", "metrics": ["total_sessions", "converted_orders"], "unit_family": "count", "type": "line"},
    }
    chart = build_chart_data(rows, plan, "website sessions trend")
    assert chart is not None
    assert chart["type"] == "line"
    assert chart["unit_family"] == "count"
    assert chart["value_cols"] == ["total_sessions", "converted_orders"]
    assert "conversion_rate_pct" not in chart["value_cols"]


def test_kpi_and_lookup_are_table_only():
    rows = [
        {"total_orders": 100, "leakage_orders": 10, "total_revenue": 5000},
        {"total_orders": 110, "leakage_orders": 12, "total_revenue": 5200},
    ]
    assert build_chart_data(rows, {"intent": "aggregation", "chart_policy": {"type": "kpi"}}, "summary") is None
    assert build_chart_data(rows, {"intent": "lookup", "chart_policy": {"type": "table_only"}}, "details") is None


def test_explicit_distribution_can_use_doughnut_for_small_part_to_whole():
    rows = [
        {"payment_status": "approved", "leakage_revenue": 5000},
        {"payment_status": "partial", "leakage_revenue": 3000},
        {"payment_status": "unpaid", "leakage_revenue": 2000},
    ]
    plan = {"intent": "payment_leakage", "chart_policy": {"label": "payment_status", "metrics": ["leakage_revenue"], "unit_family": "money"}}
    chart = build_chart_data(rows, plan, "distribution of leakage by payment status")
    assert chart is not None
    assert chart["type"] == "doughnut"
