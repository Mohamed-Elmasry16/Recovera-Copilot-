import pytest

from app.core.chart_grammar import build_chart_data
from app.agents.analytics_interpreter import _deterministic_trend_answer


def test_dual_profit_loss_chart_uses_two_tones():
    rows = [
        {"ranking_type": "highest_profit", "rank": 1, "region": "Cairo", "gross_profit": 1000, "gross_loss": 50, "net_profit": 950},
        {"ranking_type": "highest_loss", "rank": 1, "region": "Giza", "gross_profit": 500, "gross_loss": 900, "net_profit": -400},
    ]
    chart = build_chart_data(rows, {"intent": "dual_ranking"}, "ايه اكتر مناطق ارباح واكتر مناطق خسائر")
    assert chart is not None
    assert chart["type"] == "bar"
    assert len(chart["datasets"]) == 2
    assert chart["datasets"][0]["tone"] == "primary"
    assert chart["datasets"][1]["tone"] == "danger"
    assert chart["datasets"][0]["data"] == [1000, 0]
    assert chart["datasets"][1]["data"] == [0, 900]


def test_month_labels_are_human_readable_for_arabic_line_chart():
    rows = [
        {"month": 1, "total_revenue": 100},
        {"month": 2, "total_revenue": 80},
        {"month": 3, "total_revenue": 120},
    ]
    chart = build_chart_data(rows, {"intent": "trend_analysis", "primary_metric": "total_revenue"}, "حلل مبيعات الشهور")
    assert chart is not None
    assert chart["type"] == "line"
    assert chart["labels"][:3] == ["يناير", "فبراير", "مارس"]


def test_deterministic_trend_answer_uses_actual_lowest_month():
    rows = [
        {"month": 1, "total_revenue": 43298372.17},
        {"month": 2, "total_revenue": 39800000.0},
        {"month": 12, "total_revenue": 41632665.28},
    ]
    answer = _deterministic_trend_answer(
        "هاتلي تحليل الإيرادات خلال سنة 2024",
        rows,
        {"intent": "trend_analysis", "primary_metric": "total_revenue", "question_language": "arabic"},
    )
    assert answer is not None
    assert "فبراير" in answer
    assert "ديسمبر" not in answer or answer.find("فبراير") < answer.find("ديسمبر")
