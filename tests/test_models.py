from jizokuka.models import Company, ExpenseItem, HearingSheet, ScoreReport, AxisScore


def test_単語羅列を正規化する():
    """現場のシートは『どら焼き、大福・季節菓子』のような書き方で来る."""
    c = Company(products="どら焼き、大福・季節菓子", customers=["観光客", "地元/高齢者"])
    assert c.products == ["どら焼き", "大福", "季節菓子"]
    assert c.customers == ["観光客", "地元", "高齢者"]


def test_売上推移を文章化する():
    c = Company(sales={2023: 42_000_000, 2025: 35_000_000})
    trend = c.sales_trend
    assert "42,000,000" in trend and "-16.7%" in trend and "減少傾向" in trend


def test_売上が1期分だけならトレンドを出さない():
    assert Company(sales={2025: 100}).sales_trend is None


def test_経費は単価と数量からも金額を算出する():
    assert ExpenseItem(unit_price=80_000, quantity=3).resolved_amount() == 240_000
    assert ExpenseItem(amount=500_000).resolved_amount() == 500_000
    assert ExpenseItem().resolved_amount() == 0


def test_スコアは加重平均で判定される():
    r = ScoreReport(
        axes=[
            AxisScore(axis="A", score=90, weight=50),
            AxisScore(axis="B", score=70, weight=50),
        ]
    )
    assert r.recompute() == 80.0
    assert r.verdict == "採択圏（A）"
    assert r.weakest_axes(1)[0].axis == "B"


def test_空のヒアリングシートでも壊れない():
    h = HearingSheet(project_id="x")
    assert h.total_expense() == 0
    assert h.company.name is None
