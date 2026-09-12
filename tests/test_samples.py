"""同梱の模範解答が、自前の検査を全て通ることを保証する.

模範解答が自分の基準を満たさないなら、その基準か例のどちらかが間違っている。
few-shot としてプロンプトに載せる以上、ここは崩せない。
"""

from __future__ import annotations

from jizokuka.knowledge import examples_prompt, load_koubo, load_section_examples
from jizokuka.samples import load_model_answer, load_sample_hearing


def test_模範解答に文章品質の指摘が一つも無い():
    plan = load_model_answer()
    assert plan.quality == [], "\n".join(
        f"{f.severity} {f.section_heading}: {f.message}" for f in plan.quality
    )


def test_模範解答に公募要領の準拠エラーが無い():
    plan = load_model_answer()
    assert plan.compliance is not None
    assert plan.compliance.errors == [], "\n".join(
        i.message for i in plan.compliance.errors
    )


def test_模範解答が全セクションを文字数内で満たす():
    plan = load_model_answer()
    koubo = load_koubo(plan.koubo_id)
    assert {s.key for s in plan.all_sections()} == {
        spec["key"] for spec in koubo.all_section_specs()
    }
    for s in plan.all_sections():
        assert not s.over_limit, f"{s.heading}: {s.char_count}字 > 上限{s.max_chars}字"
        assert not s.under_min, f"{s.heading}: {s.char_count}字 < 下限{s.min_chars}字"
        # 模範解答は目標分量の85%以上を埋めていること（few-shot の分量基準になるため）
        assert s.fill_ratio >= 0.85, f"{s.heading}: 充足率{s.fill_ratio:.0%}"


def test_模範解答の金額が補助上限に収まる():
    plan = load_model_answer()
    koubo = load_koubo(plan.koubo_id)
    total = sum(e.resolved_amount() for e in plan.expenses)
    assert sum(plan.funding.values()) == total
    assert plan.funding["補助金"] <= koubo.limit_yen(plan.frame, plan.specials)


def test_模範解答に事業者名が入る():
    """様式の『補助事業者名』が案件IDのままにならないこと."""
    plan = load_model_answer()
    assert plan.company_name == "有限会社田中製菓"
    assert plan.representative == "田中太郎"


def test_サンプルのヒアリングシートは単語レベルである():
    """『単語しか無い入力』という前提を例が体現していること."""
    sheet = load_sample_hearing()
    assert sheet.strengths == ["創業50年", "自家製餡", "無添加", "地元産米使用"]
    assert sheet.project.idea == ["特殊冷凍機導入", "ECサイト構築", "冷凍配送"]


def test_few_shotが全セクション分そろっている():
    koubo = load_koubo()
    keys = {spec["key"] for spec in koubo.all_section_specs()}
    assert set(load_section_examples()) == keys


def test_few_shotのプロンプトに悪例と良例が両方入る():
    text = examples_prompt("strengths")
    assert "不採択になる書き方" in text
    assert "採択レベルの書き方" in text
    assert "架空の事業者" in text  # 数値流用を禁じる注意書き


def test_few_shotの良例は自前の品質チェックを通る():
    """プロンプトに『良い例』として見せる文章が、自分の基準で不合格ではおかしい."""
    from jizokuka.models import SectionDraft
    from jizokuka.pipeline.quality import check_section

    koubo = load_koubo()
    for key, ex in load_section_examples().items():
        spec = koubo.section_spec(key)
        sec = SectionDraft(
            key=key, heading=spec["heading"], body=ex["good"].strip()
        )
        # few-shot は抜粋なので、分量と数値の個数は満たさなくてよい
        skip = {"numbers", "length_short"}
        must = [f for f in check_section(sec) if f.severity == "must" and f.code not in skip]
        assert not must, f"{key}: " + "; ".join(f.message for f in must)


def test_few_shotの悪例は品質チェックに引っかかる():
    """『悪い例』が検出されないなら、チェッカーが機能していない."""
    from jizokuka.models import SectionDraft
    from jizokuka.pipeline.quality import check_section

    koubo = load_koubo()
    misses = []
    for key, ex in load_section_examples().items():
        if key == "project_name":
            continue  # 事業名は文章ではないため対象外
        spec = koubo.section_spec(key)
        sec = SectionDraft(
            key=key, heading=spec["heading"], body=ex["bad"].strip()
        )
        if not check_section(sec):
            misses.append(key)
    assert not misses, f"悪い例が検出されなかった: {misses}"
