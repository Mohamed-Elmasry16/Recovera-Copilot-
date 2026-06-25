from app.core.question_patterns import detect_question_profile, should_use_profile_fast_path


GOLDEN_CASES = [
    ("ايه اكثر مناطق تحقيق للايرادات واكثر المناطق تحقيق للخسائر", "dual_ranking", "dual_ranking"),
    ("show monthly leakage trend", "trend", "trend_analysis"),
    ("top risky sellers by leakage rate", "seller", "seller_risk"),
    ("which leakage scenarios cost the most money", "scenario", "leakage_detection"),
    ("campaign ROI by channel", "campaign", "campaign_analysis"),
    ("website sessions conversion trend", "web_analytics", "web_analytics"),
    ("COD unpaid orders leakage", "payment", "payment_leakage"),
    ("shipping delay leakage by carrier", "shipping", "shipping_leakage"),
    ("duplicate refund amount by reason", "refund", "refund_analysis"),
    ("product categories with highest revenue", "product", "product_analysis"),
    ("ملخص اجمالي التسرب والايرادات", "kpi", "aggregation"),
]


def test_golden_questions_route_to_deterministic_profiles():
    for question, family, intent in GOLDEN_CASES:
        profile = detect_question_profile(question)
        assert profile.family == family, question
        assert profile.intent == intent, question
        assert should_use_profile_fast_path(profile), question
        assert profile.template_key, question
        assert profile.chart_policy is not None, question


def test_review_text_without_financial_impact_is_rag_only():
    profile = detect_question_profile("what are customers complaining about in negative reviews")
    assert profile.family == "reviews"
    assert profile.route == "rag_only"
    assert not profile.needs_sql
    assert profile.needs_rag
