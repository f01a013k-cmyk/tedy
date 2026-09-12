from fractions import Fraction

from jizokuka.models import ExpenseItem, Plan, SectionDraft
from jizokuka.pipeline.compliance import check, grant_amount, max_expense_for_limit


def _plan(**kw) -> Plan:
    base = dict(project_id="t", koubo_id="r17", frame="通常枠")
    base.update(kw)
    return Plan(**base)


def _rules(rep, rule: str):
    return [i for i in rep.issues if i.rule == rule]


def test_補助金額は切り捨てで上限に張り付く():
    assert grant_amount(750_000, Fraction(2, 3), 500_000) == 500_000
    assert grant_amount(300_001, Fraction(2, 3), 500_000) == 200_000  # 200000.67 → 切捨
    assert max_expense_for_limit(500_000, Fraction(2, 3)) == 750_000


def test_必須セクション欠落はエラー(koubo):
    """必須項目の数は仕様から導出する（様式の項目は回次で増減するため）."""
    specs = koubo.all_section_specs()
    required = [s for s in specs if not s.get("optional")]
    optional = [s for s in specs if s.get("optional")]

    rep = check(_plan(), koubo)
    assert len(_rules(rep, "form.section.missing")) == len(required)
    assert len(_rules(rep, "form.section.optional")) == len(optional)


def test_事業名30字超はエラー_他は警告(koubo):
    """上限値は仕様から取る。ハードコードすると回次の調整でテストが嘘になる."""
    name_spec = koubo.section_spec("project_name")
    overview_spec = koubo.section_spec("company_overview")
    plan = _plan(
        project_sections=[
            SectionDraft(
                key="project_name", heading="事業名",
                body="あ" * (name_spec["max_chars"] + 1), max_chars=name_spec["max_chars"],
            ),
        ],
        sections=[
            SectionDraft(
                key="company_overview", heading="企業概要",
                body="い" * (overview_spec["max_chars"] + 100),
                max_chars=overview_spec["max_chars"],
            ),
        ],
    )
    rep = check(plan, koubo)
    lengths = {i.where: i.severity for i in _rules(rep, "form.section.length")}
    assert lengths["project_name"] == "error"     # 様式の絶対上限
    assert lengths["company_overview"] == "warn"  # 実務上の目安


def test_分量不足は準拠チェックでは出さない(koubo):
    """字数不足は quality.py が must として扱うため、ここで重複させない."""
    plan = _plan(sections=[SectionDraft(key="company_overview", heading="企業概要", body="短い。")])
    assert not _rules(check(plan, koubo), "form.section.too_short")


def test_ウェブサイト関連費の4分の1上限(koubo):
    # 総経費150万 → 補助金50万(上限)。ウェブ分の補助金上限は12.5万 = 経費18.75万相当
    plan = _plan(
        expenses=[
            ExpenseItem(category="機械装置等費", item="冷凍機", amount=1_300_000, basis="見積"),
            ExpenseItem(category="ウェブサイト関連費", item="EC構築", amount=200_000, basis="見積"),
        ]
    )
    assert _rules(check(plan, koubo), "expense.cap")

    plan.expenses[1].amount = 150_000
    assert not _rules(check(plan, koubo), "expense.cap")


def test_ウェブサイト関連費のみの申請は不可(koubo):
    plan = _plan(expenses=[ExpenseItem(category="ウェブサイト関連費", item="EC", amount=100_000)])
    assert _rules(check(plan, koubo), "expense.cap.solo")


def test_設備処分費の2分の1上限(koubo):
    plan = _plan(
        expenses=[
            ExpenseItem(category="機械装置等費", item="機械", amount=100_000),
            ExpenseItem(category="設備処分費", item="旧設備撤去", amount=200_000),
        ]
    )
    assert _rules(check(plan, koubo), "expense.cap")


def test_対象外経費と未知区分を検出する(koubo):
    plan = _plan(
        expenses=[
            ExpenseItem(category="機械装置等費", item="ノートパソコン", amount=200_000),
            ExpenseItem(category="よくわからない費目", item="X", amount=10_000),
        ]
    )
    rep = check(plan, koubo)
    assert _rules(rep, "expense.ineligible")
    assert _rules(rep, "expense.category")


def test_相見積が必要な金額を警告する(koubo):
    plan = _plan(expenses=[ExpenseItem(category="機械装置等費", item="機械", unit_price=500_000, quantity=1)])
    assert _rules(check(plan, koubo), "expense.quotation")


def test_資金調達の合計不一致はエラー(koubo):
    plan = _plan(
        expenses=[ExpenseItem(category="機械装置等費", item="機械", amount=1_000_000, basis="見積")],
        funding={"補助金": 500_000, "自己資金": 400_000},
    )
    assert _rules(check(plan, koubo), "funding.mismatch")

    plan.funding["自己資金"] = 500_000
    assert not _rules(check(plan, koubo), "funding.mismatch")


def test_要確認マーカーが残っていたらエラー(koubo):
    """推定値を実数値に置き換えないまま提出することを防ぐ."""
    plan = _plan(
        sections=[
            SectionDraft(
                key="company_overview",
                heading="企業概要",
                body="売上は約3,500万円〈要確認: 決算書で実数値を確認〉である。",
                max_chars=800,
            )
        ]
    )
    issues = _rules(check(plan, koubo), "text.todo")
    assert issues and "決算書" in issues[0].message


def test_未検証の公募要領は警告される(koubo):
    assert _rules(check(_plan(), koubo), "koubo.verified")


def test_補助上限を超える経費は自己負担額を示す(koubo):
    plan = _plan(expenses=[ExpenseItem(category="機械装置等費", item="機械", amount=3_000_000, basis="見積")])
    issues = _rules(check(plan, koubo), "expense.over_limit")
    assert issues and "全額自己負担" in issues[0].message
