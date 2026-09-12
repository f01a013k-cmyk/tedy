"""審査観点に基づく自己採点."""

from __future__ import annotations

from ..config import MODEL_REVIEW
from ..knowledge import Koubo
from ..llm import LLM
from ..llm.client import load_prompt
from ..models import AxisScore, ExpenseItem, Plan, ScoreReport


def expense_text(expenses: list[ExpenseItem]) -> str:
    if not expenses:
        return "（経費明細なし）"
    lines = ["区分 | 品名 | 単価 | 数量 | 金額 | 積算根拠 | 対応する取組"]
    for e in expenses:
        lines.append(
            " | ".join(
                [
                    e.category or "-",
                    e.item or "-",
                    f"{e.unit_price:,}" if e.unit_price else "-",
                    str(e.quantity or "-"),
                    f"{e.resolved_amount():,}",
                    e.basis or "（根拠なし）",
                    e.linked_activity or "（対応不明）",
                ]
            )
        )
    total = sum(e.resolved_amount() for e in expenses)
    lines.append(f"合計: {total:,}円")
    return "\n".join(lines)


def score(plan: Plan, koubo: Koubo, llm: LLM) -> ScoreReport:
    prompt = load_prompt("review").format(
        review_axes=koubo.review_axes_prompt(),
        plan_text=plan.full_text(),
        expense_text=expense_text(plan.expenses),
    )
    raw = llm.complete_json(
        prompt,
        system=load_prompt("system"),
        model=MODEL_REVIEW,
        max_tokens=8000,
        temperature=0.1,
        fallback={"axes": []},
    )

    by_axis = {a.get("axis"): a for a in raw.get("axes", [])}
    report = ScoreReport()
    for spec in koubo.review_axes:
        a = by_axis.get(spec["key"], {})
        report.axes.append(
            AxisScore(
                axis=spec["key"],
                score=max(0, min(100, int(a.get("score", 0)))),
                weight=int(spec["weight"]),
                findings=[str(f) for f in a.get("findings", [])],
                improvements=[str(s) for s in a.get("improvements", [])],
            )
        )
    report.recompute()
    return report
