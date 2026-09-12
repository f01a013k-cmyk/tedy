"""採点結果に基づくリライトループ."""

from __future__ import annotations

from ..config import MODEL_DRAFT
from ..knowledge import Koubo
from ..llm import LLM
from ..llm.client import load_prompt
from ..models import AxisScore, GapAnalysis, HearingSheet, Plan, SectionDraft
from .gap import _sheet_yaml, facts_text


def _sections_for_axis(plan: Plan, koubo: Koubo, axis: str) -> list[SectionDraft]:
    keys = {
        spec["key"]
        for spec in koubo.all_section_specs()
        if axis in spec.get("review_axes", [])
    }
    return [s for s in plan.all_sections() if s.key in keys]


def revise_section(
    sec: SectionDraft,
    axis_score: AxisScore,
    sheet: HearingSheet,
    gap: GapAnalysis,
    llm: LLM,
) -> SectionDraft:
    prompt = load_prompt("revise").format(
        heading=sec.heading,
        body=sec.body,
        axis=axis_score.axis,
        score=axis_score.score,
        findings="\n".join(f"- {f}" for f in axis_score.findings) or "（なし）",
        improvements="\n".join(f"- {s}" for s in axis_score.improvements) or "（なし）",
        hearing_yaml=_sheet_yaml(sheet),
        facts=facts_text(gap.facts),
        max_chars=sec.max_chars or 800,
    )
    body = llm.complete(
        prompt,
        system=load_prompt("system"),
        model=MODEL_DRAFT,
        max_tokens=4000,
        temperature=0.4,
    ).strip()

    return SectionDraft(
        key=sec.key,
        heading=sec.heading,
        body=body,
        max_chars=sec.max_chars,
        revision=sec.revision + 1,
    )


def revise_round(
    plan: Plan,
    sheet: HearingSheet,
    gap: GapAnalysis,
    koubo: Koubo,
    llm: LLM,
    focus_axes: int = 2,
) -> Plan:
    """最も点の低い観点に紐づくセクションだけを書き直す.

    全セクションを毎回書き直すと、良かった箇所まで壊れ、コストも膨らむ。
    弱点に限定するのが安定する。
    """
    assert plan.score is not None, "リライトには採点結果が必要です"

    targets: dict[str, AxisScore] = {}
    for axis_score in plan.score.weakest_axes(focus_axes):
        if axis_score.score >= 90:
            continue  # 十分高い観点は触らない
        for sec in _sections_for_axis(plan, koubo, axis_score.axis):
            # 1セクションが複数観点に紐づく場合、より低い点の指摘を採用する
            cur = targets.get(sec.key)
            if cur is None or axis_score.score < cur.score:
                targets[sec.key] = axis_score

    def _replace(seclist: list[SectionDraft]) -> list[SectionDraft]:
        out = []
        for sec in seclist:
            if sec.key in targets:
                out.append(revise_section(sec, targets[sec.key], sheet, gap, llm))
            else:
                out.append(sec)
        return out

    plan.sections = _replace(plan.sections)
    plan.project_sections = _replace(plan.project_sections)
    return plan
