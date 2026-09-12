"""競合分析. 審査観点「経営状況分析の妥当性」で最も差がつく項目."""

from __future__ import annotations

from jizokuka.knowledge import examples_prompt, load_koubo, load_section_examples
from jizokuka.models import HearingSheet, SectionDraft
from jizokuka.pipeline import ingest
from jizokuka.pipeline.quality import check_section, load_rules
from jizokuka.samples import load_model_answer


def _codes(findings) -> set[str]:
    return {f.code for f in findings}


def test_様式に競合項目がある(koubo):
    spec = koubo.section_spec("competition")
    assert spec is not None
    assert spec["number"] == "2-3"  # 公式様式2の採番（1〜4）を崩さない位置
    assert "経営状況分析の妥当性" in spec["review_axes"]


def test_競合項目は顧客ニーズの直後に置かれる(koubo):
    keys = [s["key"] for s in koubo.sections]
    assert keys.index("competition") == keys.index("market_needs") + 1
    assert keys.index("competition") < keys.index("strengths")


def test_比較の記述が乏しければ要修正():
    body = "当社の競合は市内の他の和菓子店である。品質で差別化できている。" * 12
    findings = check_section(
        SectionDraft(key="competition", heading="競合他社との比較", body=body,
                     min_chars=900, target_chars=1100, max_chars=1400)
    )
    f = [x for x in findings if x.code == "comparison"]
    assert f and f[0].severity == "must"
    assert "劣位" in f[0].fix  # 劣位を書かせる指示が入っていること


def test_自社と競合を並べていれば比較として認める():
    body = (
        "A社は価格が当社の1.2倍である。一方、B社は焼菓子が主力で、"
        "当社より3年先行してECを運用している。当社が優位なのは餡の内製であり、"
        "劣位はEC運用の経験である。"
    )
    sec = SectionDraft(key="competition", heading="競合", body=body)
    assert "comparison" not in _codes(check_section(sec))


def test_競合項目には数値が10個以上必要():
    assert load_rules().requirements("competition")["min_numbers"] == 10


def test_ヒアリングシートの競合欄は単語の羅列を受ける():
    h = HearingSheet(project_id="x", competitors="○○堂、△△製菓・駅前の洋菓子店")
    assert h.competitors == ["○○堂", "△△製菓", "駅前の洋菓子店"]


def test_取込は競合の見出しを拾う(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("事業者名,A商店\n競合他社,○○堂・△△本舗", encoding="utf-8")
    assert ingest.load(p).competitors == ["○○堂", "△△本舗"]


def test_サンプルのヒアリングに競合が入っている():
    from jizokuka.samples import load_sample_hearing

    assert len(load_sample_hearing().competitors) >= 2


def test_模範解答の競合項目が比較として成立している():
    plan = load_model_answer()
    sec = plan.section("competition")
    assert sec is not None
    assert not sec.under_min
    assert check_section(sec) == []

    body = sec.body
    # 競合2社それぞれの規模・価格・EC対応と、自社の優位・劣位が揃っていること
    for token in ("A社", "B社", "従業員", "価格帯", "優位", "劣位", "補う"):
        assert token in body, f"競合分析に「{token}」の記述がない"


def test_競合のfew_shotがある():
    ex = load_section_examples()["competition"]
    assert "実名" in ex["note"]  # 社名を創作させない注意書き
    text = examples_prompt("competition")
    assert "不採択になる書き方" in text and "採択レベルの書き方" in text


def test_ギャップ分析は競合名を推論禁止にしている():
    """社名の創作は事実誤認になるため、必ず質問させる."""
    from jizokuka.llm.client import load_prompt

    p = load_prompt("gap_analysis")
    assert "推論してはいけない項目" in p
    assert "競合他社の社名" in p


def test_様式2全体の目標分量(koubo):
    """競合項目の追加で1万字規模になる。回次調整で崩れたら気づけるようにする."""
    total = sum(s["target_chars"] for s in koubo.all_section_specs())
    assert 9_500 <= total <= 11_000, total
