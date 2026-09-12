"""公募要領準拠チェック（決定論的）.

金額・上限・文字数・対象外経費は LLM に判断させず、コードで確定させる。
LLM は文章を書くのが仕事であり、規程の適合判定をさせてはならない。
"""

from __future__ import annotations

import math
import re
from fractions import Fraction

from ..knowledge import Koubo
from ..models import ComplianceReport, ExpenseItem, HearingSheet, Plan

# 本文に残っていてはいけない作業用マーカー
TODO_MARKER = re.compile(r"〈要確認[:：]?([^〉]*)〉")


def grant_amount(total_expense: int, rate: Fraction, limit: int) -> int:
    """補助金申請額 = min(補助対象経費 × 補助率, 上限額). 円未満切り捨て."""
    return min(int(math.floor(total_expense * rate)), limit)


def max_expense_for_limit(limit: int, rate: Fraction) -> int:
    """補助上限に張り付く補助対象経費の目安."""
    return int(math.ceil(limit / rate))


def check(plan: Plan, koubo: Koubo, sheet: HearingSheet | None = None) -> ComplianceReport:
    rep = ComplianceReport()

    _check_koubo_verified(rep, koubo)
    _check_sections(rep, plan, koubo)
    _check_expenses(rep, plan, koubo, sheet)
    _check_text_quality(rep, plan, koubo)
    _check_funding(rep, plan, koubo, sheet)

    return rep


# --------------------------------------------------------------------------


def _check_koubo_verified(rep: ComplianceReport, koubo: Koubo) -> None:
    if not koubo.verified:
        rep.add(
            "warn",
            "koubo.verified",
            f"公募要領ナレッジ『{koubo.display_name}』が未検証です。"
            f"金額・補助率・様式構成が実際の公募要領と異なる可能性があります。",
            where=str(koubo.path),
            fix_hint="`jizokuka koubo verify` で突合チェックリストを出力し、"
            "確認後 meta.verified を true にしてください。",
        )


def _check_sections(rep: ComplianceReport, plan: Plan, koubo: Koubo) -> None:
    present = {s.key for s in plan.all_sections()}

    for spec in koubo.all_section_specs():
        key = spec["key"]
        if key not in present:
            if spec.get("optional"):
                rep.add(
                    "info",
                    "form.section.optional",
                    f"任意項目「{spec['heading']}」が未記入です。"
                    "記載すると審査観点『補助事業計画の有効性』で有利になります。",
                    where=key,
                )
            else:
                rep.add(
                    "error",
                    "form.section.missing",
                    f"必須項目「{spec['heading']}」がありません。",
                    where=key,
                )
            continue

        sec = plan.section(key)
        assert sec is not None
        if not sec.body.strip():
            rep.add("error", "form.section.empty", f"「{sec.heading}」が空です。", where=key)
            continue

        max_chars = spec.get("max_chars")
        if max_chars and sec.char_count > max_chars:
            severity = "error" if spec.get("hard_limit") else "warn"
            over = sec.char_count - max_chars
            rep.add(
                severity,
                "form.section.length",
                f"「{sec.heading}」が{sec.char_count}文字で、上限{max_chars}文字を"
                f"{over}文字超過しています。",
                where=key,
                fix_hint=f"`jizokuka trim {plan.project_id} --section {key}` で圧縮できます。",
            )
        # 分量不足は quality.py が must として扱うため、ここでは重複して出さない


def _check_expenses(
    rep: ComplianceReport, plan: Plan, koubo: Koubo, sheet: HearingSheet | None
) -> None:
    expenses: list[ExpenseItem] = plan.expenses
    if not expenses:
        rep.add("error", "expense.empty", "経費明細が1件もありません。")
        return

    total = sum(e.resolved_amount() for e in expenses)
    is_deficit = sheet.company.is_deficit if sheet else None
    rate = koubo.rate(plan.frame, is_deficit)
    limit = koubo.limit_yen(plan.frame, plan.specials)
    grant = grant_amount(total, rate, limit)

    rules = koubo.expense_rules
    quote_threshold = int(rules.get("quotation_threshold_yen", 500000))

    by_category: dict[str, int] = {}

    for i, e in enumerate(expenses, start=1):
        label = f"{i}行目「{e.item or '(品名なし)'}」"
        amount = e.resolved_amount()

        if amount <= 0:
            rep.add("error", "expense.amount", f"{label}の金額が0円または未設定です。", where=label)

        # 経費区分の妥当性
        cat = koubo.category(e.category or "")
        if cat is None:
            rep.add(
                "error",
                "expense.category",
                f"{label}の経費区分『{e.category}』が補助対象経費区分に存在しません。",
                where=label,
                fix_hint=f"有効な区分: {'、'.join(koubo.category_names())}",
            )
        else:
            by_category[cat["name"]] = by_category.get(cat["name"], 0) + amount

        # 対象外経費キーワード
        for kw, reason in koubo.ineligible_hits(f"{e.item or ''} {e.basis or ''}"):
            rep.add(
                "error",
                "expense.ineligible",
                f"{label}に対象外の可能性が高い『{kw}』が含まれます。{reason}",
                where=label,
                fix_hint="対象となる費目への振替、または当該経費の除外を検討してください。",
            )

        # 相見積
        unit = e.unit_price if e.unit_price is not None else amount
        if unit >= quote_threshold:
            rep.add(
                "warn",
                "expense.quotation",
                f"{label}は税抜単価{unit:,}円で、"
                f"{rules.get('quotation_threshold_message', '相見積が必要')}。",
                where=label,
            )

        # 積算根拠
        if not (e.basis or "").strip():
            rep.add(
                "warn",
                "expense.basis",
                f"{label}に積算根拠がありません。審査観点『積算の透明・適切性』の減点要因です。",
                where=label,
            )

        # 本文との対応
        if not (e.linked_activity or "").strip():
            rep.add(
                "warn",
                "expense.linkage",
                f"{label}が事業計画本文のどの取組に対応するか不明です。",
                where=label,
            )

    # -- 区分ごとの上限ルール ------------------------------------------------
    for cat in koubo.expense_categories:
        cap = cat.get("cap_rule")
        if not cap:
            continue
        spent = by_category.get(cat["name"], 0)
        if spent <= 0:
            continue

        if cap["type"] == "ratio_of_grant":
            # 当該経費に対応する補助金額が、交付申請額の一定割合以下であること
            cat_grant = math.floor(spent * rate)
            allowed = math.floor(grant * cap["ratio"])
            if cat_grant > allowed:
                rep.add(
                    "error",
                    "expense.cap",
                    f"{cat['name']}が{spent:,}円（補助金換算{cat_grant:,}円）で、"
                    f"上限{allowed:,}円を超過しています。{cap['message']}",
                    where=cat["name"],
                    fix_hint=f"他区分の経費を増やすか、{cat['name']}を"
                    f"{math.floor(allowed / rate):,}円以下に抑えてください。",
                )
            # 単独申請の禁止
            if len(by_category) == 1:
                rep.add(
                    "error",
                    "expense.cap.solo",
                    f"{cat['name']}のみでの申請はできません。他の経費区分と組み合わせてください。",
                    where=cat["name"],
                )

        elif cap["type"] == "ratio_of_total_expense":
            allowed = math.floor(total * cap["ratio"])
            if spent > allowed:
                rep.add(
                    "error",
                    "expense.cap",
                    f"{cat['name']}が{spent:,}円で、上限{allowed:,}円を超過しています。"
                    f"{cap['message']}",
                    where=cat["name"],
                )

    # -- 補助金額の上限 ------------------------------------------------------
    theoretical = math.floor(total * rate)
    if theoretical > limit:
        waste = theoretical - limit
        rep.add(
            "warn",
            "expense.over_limit",
            f"補助対象経費{total:,}円 × 補助率{koubo.rate_label(plan.frame)} = "
            f"{theoretical:,}円が、補助上限{limit:,}円を{waste:,}円超えています。"
            f"超過分{math.ceil(waste / rate):,}円相当は全額自己負担になります。",
            fix_hint=f"補助上限に張り付く補助対象経費の目安は"
            f"{max_expense_for_limit(limit, rate):,}円です。",
        )
    elif total > 0 and theoretical < limit * 0.5:
        rep.add(
            "info",
            "expense.under_limit",
            f"補助金申請額{grant:,}円は上限{limit:,}円の"
            f"{grant / limit * 100:.0f}%です。枠を活かしきれていない可能性があります。",
        )


def _check_text_quality(rep: ComplianceReport, plan: Plan, koubo: Koubo) -> None:
    for sec in plan.all_sections():
        for hit, reason in koubo.forbidden_hits(sec.body):
            rep.add(
                "warn",
                "text.forbidden",
                f"「{sec.heading}」に不適切な表現『{hit}』があります。{reason}",
                where=sec.key,
            )
        for m in TODO_MARKER.finditer(sec.body):
            rep.add(
                "error",
                "text.todo",
                f"「{sec.heading}」に未確認マーカーが残っています: 〈要確認{m.group(1)}〉",
                where=sec.key,
                fix_hint="事業者に確認して実数値に置き換えるまで提出できません。",
            )


def _check_funding(
    rep: ComplianceReport, plan: Plan, koubo: Koubo, sheet: HearingSheet | None
) -> None:
    total = sum(e.resolved_amount() for e in plan.expenses)
    if not plan.funding:
        if total > 0:
            rep.add(
                "warn",
                "funding.missing",
                "資金調達方法（様式3）が未記入です。",
            )
        return

    funded = sum(plan.funding.values())
    if funded != total:
        rep.add(
            "error",
            "funding.mismatch",
            f"資金調達額の合計{funded:,}円が、補助対象経費合計{total:,}円と一致しません"
            f"（差額{funded - total:+,}円）。",
            fix_hint="様式3では両者が一致している必要があります。",
        )
