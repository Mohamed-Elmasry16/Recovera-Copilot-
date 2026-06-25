from app.agents.sql_safety import ensure_outer_limit


def test_outer_limit_wraps_cte():
    sql = "WITH x AS (SELECT 1 AS a) SELECT a FROM x"
    wrapped = ensure_outer_limit(sql, 200)
    assert wrapped.startswith("SELECT * FROM (")
    assert "WITH x AS" in wrapped
    assert "LIMIT 201" in wrapped
