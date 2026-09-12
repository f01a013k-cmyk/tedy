"""採点結果と品質指摘に基づくリライトループ.

リライト対象の選び方:
  1. 機械チェックで must 級の品質指摘があるセクション（スコアに関係なく必ず直す）
  2. 最も点の低い審査観点に紐づくセクション

全セクションを毎回書き直すと、良かった箇所まで壊れ、コストも膨らむ。
弱点に限定するのが安定する。
"""

from __future__ import annotations

from ..config import MODEL_DRAFT
from ..knowledge import Koubo, examples_prompt
from ..llm import LLM
from ..llm.client import load_prompt
from ..models import AxisScore, GapAnalysis, HearingSheet, Plan, QualityFinding, SectionDraft
from .draft import min_chars_for
from .gap import _sheet_yaml, facts_text
from .quality import findings_for, requirements_prompt, summarize

_NO_AXIS = AxisScore(axis="（審査観点の指摘なし）", score=0, weight=0)


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
    quality: list[QualityFinding] | None = None,
) -> SectionDraft:
    max_chars = sec.max_chars or 800
    prompt = load_prompt("revise").format(
        heading=sec.heading,
        body=sec.body,
        axis=axis_score.axis,
        score=axis_score.score,
        findings="\n".join(f"- {f}" for f in axis_score.findings) or "（なし）",
        improvements="\n".join(f"- {s}" for s in axis_score.improvements) or "（なし）",
        quality_findings=summarize(quality or []) or "（指摘なし）",
        quality_requirements=requirements_prompt(sec.key) or "（特になし）",
        examples=examples_prompt(sec.key) or "（参考例なし）",
        hearing_yaml=_sheet_yaml(sheet),
        facts=facts_text(gap.facts),
        max_chars=max_chars,
        min_chars=min_chars_for(max_chars),
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


def select_targets(
    plan: Plan,
    koubo: Koubo,
    quality: list[QualityFinding],
    focus_axes: int = 2,
) -> dict[str, AxisScore]:
    """書き直すセクションと、その根拠となる審査観点の対応を決める."""
    targets: dict[str, AxisScore] = {}

    # 1. must 級の品質指摘があるセクションは、点数に関係なく必ず直す
    for f in quality:
        if f.severity == "must":
            targets.setdefault(f.section_key, _NO_AXIS)

    # 2. 点の低い審査観点に紐づくセクション
    if plan.score:
        for axis_score in plan.score.weakest_axes(focus_axes):
            if axis_score.score >= 90:
                continue  # 十分高い観点は触らない
            for sec in _sections_for_axis(plan, koubo, axis_score.axis):
                cur = targets.get(sec.key)
                # 1セクションが複数観点に紐づく場合、より低い点の指摘を採用する
                if cur is None or cur is _NO_AXIS or axis_score.score < cur.score:
                    targets[sec.key] = axis_score

    return targets


def revise_round(
    plan: Plan,
    sheet: HearingSheet,
    gap: GapAnalysis,
    koubo: Koubo,
    llm: LLM,
    focus_axes: int = 2,
    quality: list[QualityFinding] | None = None,
) -> Plan:
    quality = quality if quality is not None else plan.quality
    targets = select_targets(plan, koubo, quality, focus_axes)

    def _replace(seclist: list[SectionDraft]) -> list[SectionDraft]:
        out = []
        for sec in seclist:
            if sec.key in targets:
                out.append(
                    revise_section(
                        sec,
                        targets[sec.key],
                        sheet,
                        gap,
                        llm,
                        quality=findings_for(quality, sec.key),
                    )
                )
            else:
                out.append(sec)
        return out

    plan.sections = _replace(plan.sections)
    plan.project_sections = _replace(plan.project_sections)
    return plan
