"""文章品質の決定論的チェック."""

from __future__ import annotations

import pytest

from jizokuka.models import SectionDraft
from jizokuka.pipeline.quality import (
    check_duplication,
    check_section,
    count_numbers,
    load_rules,
    requirements_prompt,
    summarize,
)


def _sec(key: str, body: str, heading: str = "見出し", max_chars: int = 800) -> SectionDraft:
    return SectionDraft(key=key, heading=heading, body=body, max_chars=max_chars)


def _codes(findings) -> set[str]:
    return {f.code for f in findings}


def test_年号は数値として数えない():
    """年号を並べただけで数値要件を満たせてしまうのを防ぐ."""
    rules = load_rules()
    assert count_numbers("2023年、2024年、2025年", rules) == 0
    assert count_numbers("2025年度の売上は3,500万円で、客数は45人である", rules) == 2


def test_要確認マーカーは品質判定から除外される():
    rules = load_rules()
    assert count_numbers("売上は〈要確認: 決算書で12345を確認〉である", rules) == 0


def test_主観形容詞を検出する():
    f = check_section(_sec("strengths", "当社は丁寧な対応と高品質な商品が強みである。"))
    assert "subjective" in _codes(f)
    assert any(x.severity == "must" for x in f if x.code == "subjective")


def test_曖昧な数量表現を検出する():
    f = check_section(_sec("company_overview", "多くのお客様にさまざまな商品を提供している。"))
    assert "vague" in _codes(f)


def test_具体策を伴わないバズワードを検出する():
    f = check_section(_sec("effect", "DXを推進し、企業価値を高める。"))
    assert "buzzword" in _codes(f)


def test_具体化されたバズワードは指摘しない():
    body = "受注管理システムを導入するDXにより、月18時間の入力作業を3時間に削減する。"
    assert "buzzword" not in _codes(check_section(_sec("effect", body)))


def test_効果に算出式がなければ指摘する():
    body = "売上は初年度200万円、3年目700万円、利益は180万円を見込む。"
    f = check_section(_sec("effect", body))
    assert "formula" in _codes(f)


def test_算出式があれば指摘しない():
    body = (
        "初年度のEC売上は、客単価3,200円 × 新規購入者52人/月 × 12か月 = 年間1,996,800円である。"
    )
    assert "formula" not in _codes(check_section(_sec("effect", body)))


def test_市場動向に出典がなければ指摘する():
    f = check_section(_sec("market_needs", "EC市場は拡大しており、観光需要も回復している。"))
    assert "source" in _codes(f)


def test_出典があれば指摘しない():
    body = "松本市の人口は、国勢調査によれば2020年に24万1千人で、65歳以上が28.8%を占める。"
    assert "source" not in _codes(check_section(_sec("market_needs", body)))


def test_月次スケジュールがなければ指摘する():
    f = check_section(_sec("sales_channel", "冷凍機を導入し、ECサイトを構築する。"))
    assert "schedule" in _codes(f)


def test_月が2つ以上あればスケジュールとみなす():
    body = "4月に機種選定と発注を行い、5月に搬入・設置する。7月にECサイトを公開する。"
    assert "schedule" not in _codes(check_section(_sec("sales_channel", body)))


def test_年度別目標がなければ指摘する():
    f = check_section(_sec("policy_and_plan", "売上を伸ばし、利益率を改善していく方針である。"))
    assert "yearly_target" in _codes(f)


def test_長すぎる一文を検出する():
    body = "あ" * 130 + "。"
    assert "long_sentence" in _codes(check_section(_sec("company_overview", body)))


def test_数値不足を件数付きで指摘する():
    required = load_rules().requirements("company_overview")["min_numbers"]
    f = [
        x
        for x in check_section(_sec("company_overview", "当社は和菓子を製造している。"))
        if x.code == "numbers"
    ]
    assert f and f"{required}個以上" in f[0].message


def test_分量不足を要修正として検出する():
    """記入欄が埋まっていない計画書は『書くことがない事業』と映る."""
    sec = SectionDraft(
        key="company_overview", heading="企業概要", body="あ" * 400,
        min_chars=900, target_chars=1100, max_chars=1400,
    )
    f = [x for x in check_section(sec) if x.code == "length_short"]
    assert f and f[0].severity == "must"
    assert "500字不足" in f[0].fix or "500" in f[0].message
    assert "1100字程度" in f[0].fix


def test_下限を満たせば分量は指摘しない():
    sec = SectionDraft(
        key="company_overview", heading="企業概要", body="あ" * 1000,
        min_chars=900, target_chars=1100, max_chars=1400,
    )
    assert "length_short" not in _codes(check_section(sec))


def test_字数は空白と改行を除いて数える():
    """記入欄の字数に改行やインデントは含まれない."""
    sec = SectionDraft(key="effect", heading="効果", body="あいう\n\n  えお  \n")
    assert sec.char_count == 5


def test_充足率を返す():
    sec = SectionDraft(key="effect", heading="効果", body="あ" * 550, target_chars=1100)
    assert sec.fill_ratio == 0.5


def test_セクション間の重複を検出する():
    shared = "創業以来50年にわたり小豆の仕入れから炊き上げまでを自社で行っている"
    findings = check_duplication(
        [
            _sec("company_overview", f"当社は和菓子店である。{shared}。", heading="企業概要"),
            _sec("strengths", f"{shared}点が強みである。", heading="強み"),
        ]
    )
    assert findings and findings[0].code == "duplication"
    assert "企業概要" in findings[0].message


def test_重複がなければ指摘しない():
    findings = check_duplication(
        [
            _sec("company_overview", "1975年創業の和菓子製造小売業である。", heading="企業概要"),
            _sec("strengths", "餡を内製しており糖度を1度刻みで調整できる。", heading="強み"),
        ]
    )
    assert findings == []


def test_指摘はリライト指示として使える形に整形される():
    text = summarize(check_section(_sec("effect", "売上が向上する見込みである。")))
    assert " → " in text  # 「問題 → 直し方」の形
    assert "算出式" in text


@pytest.mark.parametrize(
    "key,expected",
    [
        ("effect", "算出式"),
        ("market_needs", "出典"),
        ("sales_channel", "月単位"),
        ("policy_and_plan", "年度別"),
    ],
)
def test_執筆時に品質要件が伝えられる(key, expected):
    """後段のチェックと同じ条件を最初のドラフトに伝え、リライト回数を減らす."""
    assert expected in requirements_prompt(key)


def test_推定値の出所を人に帰属させたら要修正():
    """モデルが逆算した数値を『聞き取りに基づく』と書くのは出所の偽装にあたる."""
    body = "品目別の構成比は、代表への聞き取りに基づく概算値である。" + "あ" * 200
    f = [x for x in check_section(_sec("company_overview", body)) if x.code == "attribution"]
    assert f and f[0].severity == "must"
    assert "〈要確認〉" in f[0].fix or "推定" in f[0].fix


def test_推定と明示していれば指摘しない():
    body = "品目別の構成比は、売上高を提示単価で割り戻して算出した推計値である。" + "あ" * 200
    codes = {x.code for x in check_section(_sec("company_overview", body))}
    assert "attribution" not in codes
