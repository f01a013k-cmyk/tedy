"""生成パイプラインのオーケストレーション.

  ヒアリング → ギャップ分析 → 経費構造化 → ドラフト → 採点 → リライト → 準拠チェック

リライトは目標点に達するか上限回数まで繰り返す。
各ラウンドのスコア推移を残し、支援員が「どこで伸びたか」を追えるようにする。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .config import SETTINGS
from .knowledge import Koubo, load_koubo
from .llm import LLM
from .models import GapAnalysis, HearingSheet, Plan
from .pipeline import compliance, draft, expense, gap, review, revise

Reporter = Callable[[str], None]


@dataclass
class BuildResult:
    plan: Plan
    gap: GapAnalysis
    score_history: list[float] = field(default_factory=list)
    expense_warnings: list[str] = field(default_factory=list)


def build(
    sheet: HearingSheet,
    llm: LLM,
    *,
    koubo: Koubo | None = None,
    existing_gap: GapAnalysis | None = None,
    answers: dict[str, str] | None = None,
    max_revisions: int | None = None,
    target_score: float | None = None,
    include_optional: bool = True,
    report: Reporter = lambda m: None,
) -> BuildResult:
    koubo = koubo or load_koubo(sheet.koubo_id)
    max_revisions = SETTINGS.max_revisions if max_revisions is None else max_revisions
    target_score = SETTINGS.target_score if target_score is None else target_score

    # 1. ギャップ分析 ------------------------------------------------------
    if existing_gap is not None:
        g = existing_gap
        report("ギャップ分析: 既存の結果を再利用")
    else:
        report("ギャップ分析: 単語から事実を推論し、必要な質問を抽出中…")
        g = gap.analyze(sheet, koubo, llm)
        report(f"  → 事実{len(g.facts)}件 / 追加質問{len(g.questions)}件")

    if answers:
        gap.apply_answers(g, answers)
        report(f"  → 回答{sum(1 for q in g.questions if q.answered)}件を反映")

    # 2. 経費の構造化 ------------------------------------------------------
    report("経費明細: 費目の割り当てと積算根拠を生成中…")
    items, warns = expense.structure(sheet, koubo, llm)
    total = sum(e.resolved_amount() for e in items)
    report(f"  → {len(items)}件 / 補助対象経費 {total:,}円")

    # 3. 本文ドラフト ------------------------------------------------------
    report("本文: 様式2の各セクションを執筆中…")
    sections, project_sections = draft.draft_all(
        sheet, g, koubo, llm, include_optional=include_optional
    )

    plan = Plan(
        project_id=sheet.project_id,
        koubo_id=koubo.id,
        frame=sheet.frame,
        specials=sheet.specials,
        sections=sections,
        project_sections=project_sections,
        expenses=items,
        funding=expense.funding_plan(
            total, koubo, sheet.frame, sheet.specials, sheet.company.is_deficit
        ),
        facts=g.facts,
    )

    # 4. 採点 → リライトのループ -------------------------------------------
    history: list[float] = []
    report("採点: 審査員視点でスコアリング中…")
    plan.score = review.score(plan, koubo, llm)
    history.append(plan.score.total)
    report(f"  → {plan.score.total}点 / {plan.score.verdict}")

    for r in range(1, max_revisions + 1):
        if plan.score.total >= target_score:
            report(f"目標{target_score}点に到達。リライトを終了")
            break
        weak = ", ".join(f"{a.axis}({a.score})" for a in plan.score.weakest_axes(2))
        report(f"リライト{r}回目: 弱点[{weak}]を改稿中…")
        plan = revise.revise_round(plan, sheet, g, koubo, llm)
        plan.score = review.score(plan, koubo, llm)
        history.append(plan.score.total)
        delta = history[-1] - history[-2]
        report(f"  → {plan.score.total}点（{delta:+.1f}） / {plan.score.verdict}")
        if delta <= 0 and r >= 2:
            report("  スコアが改善しないため打ち切り。人手での加筆を推奨します")
            break

    # 5. 公募要領準拠チェック ----------------------------------------------
    report("準拠チェック: 公募要領との突合中…")
    plan.compliance = compliance.check(plan, koubo, sheet)
    n_err = len(plan.compliance.errors)
    n_warn = len(plan.compliance.warnings)
    report(f"  → エラー{n_err}件 / 警告{n_warn}件")

    return BuildResult(plan=plan, gap=g, score_history=history, expense_warnings=warns)
