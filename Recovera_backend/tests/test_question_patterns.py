from app.core.question_patterns import detect_question_profile


def test_arabic_dual_region_maps_to_deterministic_template():
    p = detect_question_profile("ايه اكثر مناطق تحقيق للارباح واكثر المناطق تحقيق للخسائر")
    assert p.intent == "dual_ranking"
    assert p.confidence >= 0.90
    assert p.template_key.startswith("dual_region_")
    assert p.rank_metric == "leakage_revenue"


def test_campaign_question_uses_campaign_template():
    p = detect_question_profile("أفضل حملة marketing حققت إيرادات؟")
    assert p.intent == "campaign_analysis"
    assert p.template_key == "campaign_performance"


def test_web_sessions_question_uses_web_template():
    p = detect_question_profile("تأثير جلسات الموقع على المبيعات")
    assert p.intent == "web_analytics"
    assert p.template_key.startswith("web_")


def test_review_only_is_rag_only():
    p = detect_question_profile("حلل شكاوى العملاء في المراجعات")
    assert p.intent == "review_analysis"
    assert p.route == "rag_only"
    assert not p.needs_sql


def test_review_financial_is_hybrid():
    p = detect_question_profile("المراجعات السلبية أثرت على الإيرادات قد ايه؟")
    assert p.intent == "review_analysis"
    assert p.route == "hybrid"
    assert p.needs_sql
