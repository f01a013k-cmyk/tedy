"""Markdown 出力（レビュー・差分管理用）."""

from __future__ import annotations

from pathlib import Path

from ..knowledge import Koubo
from ..models import GapAnalysis, Plan
from ..pipeline.compliance import grant_amount


def plan_markdown(plan: Plan, koubo: Koubo) -> str:
    limit = koubo.limit_yen(plan.frame, plan.specials)
    rate = koubo.rate(plan.frame)
    total = sum(e.resolved_amount() for e in plan.expenses)
    grant = grant_amount(total, rate, limit)

    out: list[str] = [
        f"# 事業計画書（{koubo.display_name}）",
        "",
        f"- 案件ID: `{plan.project_id}`",
        f"- 申請枠: {plan.frame}"
        + (f"（{'・'.join(plan.specials)}）" if plan.specials else ""),
        f"- 補助率: {koubo.rate_label(plan.frame)} / 補助上限: {limit:,}円",
        f"- 補助対象経費: {total:,}円 → **補助金申請額 {grant:,}円**",
        f"- 生成日: {plan.generated_at}",
    ]
    if plan.score:
        out.append(f"- 自己採点: **{plan.score.total}点**（{plan.score.verdict}）")
    out.append("")

    out.append("## 様式2 経営計画")
    for spec in koubo.sections:
        sec = plan.section(spec["key"])
        if not sec:
            continue
        out += _section_md(spec["number"], sec)

    out.append("## 様式2 補助事業計画")
    for spec in koubo.project_sections:
        sec = plan.section(spec["key"])
        if not sec:
            continue
        out += _section_md(spec["number"], sec)

    out.append("## 様式3 経費明細表")
    out.append("")
    out.append("| 経費区分 | 内容 | 単価 | 数量 | 金額(税抜) | 積算根拠 | 対応する取組 |")
    out.append("|---|---|---:|---:|---:|---|---|")
    for e in plan.expenses:
        out.append(
            "| {} | {} | {} | {} | {} | {} | {} |".format(
                e.category or "-",
                e.item or "-",
                f"{e.unit_price:,}" if e.unit_price else "-",
                e.quantity or "-",
                f"{e.resolved_amount():,}",
                (e.basis or "-").replace("|", "／"),
                (e.linked_activity or "-").replace("|", "／"),
            )
        )
    out.append(f"| **合計** |  |  |  | **{total:,}** |  |  |")
    out.append("")

    out.append("## 様式3 資金調達方法")
    out.append("")
    out.append("| 区分 | 金額 |")
    out.append("|---|---:|")
    for k, v in plan.funding.items():
        out.append(f"| {k} | {v:,} |")
    out.append(f"| **合計** | **{sum(plan.funding.values()):,}** |")
    out.append("")

    return "\n".join(out)


def _section_md(no: str, sec) -> list[str]:
    limit = f" / 上限{sec.max_chars}字" if sec.max_chars else ""
    flag = " ⚠️**超過**" if sec.over_limit else ""
    return [
        "",
        f"### {no}. {sec.heading}",
        f"<sub>{sec.char_count}字{limit}{flag}"
        + (f" / 改稿{sec.revision}回" if sec.revision else "")
        + "</sub>",
        "",
        sec.body,
        "",
    ]


def questions_markdown(gap: GapAnalysis, project_id: str) -> str:
    """顧客に送る追加質問シート.

    自由記述を強いると返信が来ない。選択肢に丸を付けるだけで済む形にする。
    """
    qs = gap.unanswered()
    out = [
        f"# 追加確認事項（{project_id}）",
        "",
        "事業計画書を仕上げるために、以下だけご確認ください。",
        "**選択肢から選ぶだけで結構です。** 該当がなければ「その他」にご記入ください。",
        "",
    ]
    if gap.blocking:
        out += ["> ⚠️ 以下が判明しないと計画書を作成できません。", ""]
        out += [f"> - {b}" for b in gap.blocking]
        out.append("")

    if not qs:
        out.append("追加でお伺いしたいことはありません。")
        return "\n".join(out)

    for i, q in enumerate(qs, start=1):
        stars = "★" * q.impact
        out.append(f"## Q{i}. {q.text}")
        out.append("")
        out.append(f"<sub>重要度 {stars} / 確認理由: {q.why}</sub>")
        out.append("")
        for c in q.choices:
            out.append(f"- [ ] {c}")
        if q.allow_free_text:
            out.append("- [ ] その他（　　　　　　　　　　　　　　　　）")
        out.append("")

    out += [
        "---",
        "",
        "### 回答の記入方法（支援員向け）",
        "",
        "`answers.yaml` に以下の形式で転記してください。",
        "",
        "```yaml",
        "answers:",
    ]
    for q in qs:
        out.append(f"  {q.id}: \"\"   # {q.text}")
    out.append("```")
    return "\n".join(out)


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path
