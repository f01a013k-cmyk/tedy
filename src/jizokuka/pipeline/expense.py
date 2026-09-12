"""経費明細の構造化と積算根拠の生成."""

from __future__ import annotations

import math

import yaml

from ..config import MODEL_EXTRACT
from ..knowledge import Koubo
from ..llm import LLM
from ..llm.client import load_prompt
from ..models import ExpenseItem, HearingSheet
from .compliance import max_expense_for_limit


def structure(
    sheet: HearingSheet,
    koubo: Koubo,
    llm: LLM,
    project_text: str = "",
) -> tuple[list[ExpenseItem], list[str]]:
    raw_expenses = [e.model_dump(mode="json", exclude_none=True) for e in sheet.project.expenses]
    if not raw_expenses and not sheet.project.idea:
        return [], ["経費の手掛かりが入力にありません。事業者に確認してください。"]

    rate = koubo.rate(sheet.frame, sheet.company.is_deficit)
    limit = koubo.limit_yen(sheet.frame, sheet.specials)

    prompt = load_prompt("expense").format(
        expense_yaml=yaml.safe_dump(
            {"expenses": raw_expenses, "idea": sheet.project.idea},
            allow_unicode=True,
            sort_keys=False,
        ),
        project_text=project_text or "（本文未生成。ヒアリングのアイデアから推定すること）",
        categories=koubo.expense_categories_prompt(),
        frame=sheet.frame,
        limit=limit,
        rate_label=koubo.rate_label(sheet.frame),
        max_expense=max_expense_for_limit(limit, rate),
    )
    raw = llm.complete_json(
        prompt,
        system=load_prompt("system"),
        model=MODEL_EXTRACT,
        max_tokens=6000,
        fallback={"expenses": raw_expenses, "warnings": []},
    )

    items: list[ExpenseItem] = []
    for e in raw.get("expenses", []):
        item = ExpenseItem.model_validate(e)
        if item.amount is None:
            item.amount = item.resolved_amount() or None
        items.append(item)

    return items, [str(w) for w in raw.get("warnings", [])]


def funding_plan(total_expense: int, koubo: Koubo, frame: str, specials: list[str],
                 is_deficit: bool | None = None) -> dict[str, int]:
    """資金調達方法（様式3）を組む. 補助金＋自己資金で総額に一致させる."""
    rate = koubo.rate(frame, is_deficit)
    limit = koubo.limit_yen(frame, specials)
    grant = min(int(math.floor(total_expense * rate)), limit)
    return {
        "補助金": grant,
        "自己資金": total_expense - grant,
    }
