"""セクション別ドラフト生成."""

from __future__ import annotations

from ..config import MODEL_DRAFT
from ..knowledge import Koubo, examples_prompt
from ..llm import LLM
from ..llm.client import load_prompt
from ..models import GapAnalysis, HearingSheet, SectionDraft
from .gap import _sheet_yaml, answers_text, facts_text
from .quality import requirements_prompt


def char_budget(spec: dict) -> tuple[int, int, int]:
    """(下限, 目標, 上限). 仕様に無ければ上限から比率で補う."""
    max_chars = int(spec.get("max_chars", 800))
    target = int(spec.get("target_chars", int(max_chars * 0.8)))
    minimum = int(spec.get("min_chars", int(max_chars * 0.65)))
    return minimum, target, max_chars


def draft_section(
    spec: dict,
    sheet: HearingSheet,
    gap: GapAnalysis,
    koubo: Koubo,
    llm: LLM,
    written: list[SectionDraft],
) -> SectionDraft:
    axes = "\n".join(
        f"■ {a}\n" + "\n".join(f"  - {c}" for c in (koubo.axis(a) or {}).get("criteria", []))
        for a in spec.get("review_axes", [])
    )
    written_text = (
        "\n\n".join(f"【{s.heading}】\n{s.body}" for s in written) if written else "（まだ無い）"
    )
    min_chars, target_chars, max_chars = char_budget(spec)
    prompt = load_prompt("draft_section").format(
        heading=spec["heading"],
        guidance=spec.get("guidance", ""),
        max_chars=max_chars,
        min_chars=min_chars,
        target_chars=target_chars,
        hard_note=(
            "この項目は様式上の**絶対的な上限**であり、1文字でも超えると受理されません。"
            if spec.get("hard_limit")
            else ""
        ),
        axes=axes or "（指定なし）",
        quality_requirements=requirements_prompt(spec["key"]) or "（特になし）",
        examples=examples_prompt(spec["key"]) or "（参考例なし）",
        hearing_yaml=_sheet_yaml(sheet),
        facts=facts_text(gap.facts),
        answers=answers_text(gap),
        written=written_text,
    )
    body = llm.complete(
        prompt,
        system=load_prompt("system"),
        model=MODEL_DRAFT,
        max_tokens=4000,
        temperature=0.5,
    ).strip()

    return SectionDraft(
        key=spec["key"],
        heading=spec["heading"],
        body=body,
        min_chars=min_chars,
        target_chars=target_chars,
        max_chars=max_chars,
    )


def draft_all(
    sheet: HearingSheet,
    gap: GapAnalysis,
    koubo: Koubo,
    llm: LLM,
    include_optional: bool = True,
) -> tuple[list[SectionDraft], list[SectionDraft]]:
    """様式2 の経営計画パートと補助事業計画パートを順に生成する.

    前のセクションを文脈として渡すことで、論理の接続と重複回避を担保する。
    """
    written: list[SectionDraft] = []

    sections: list[SectionDraft] = []
    for spec in koubo.sections:
        s = draft_section(spec, sheet, gap, koubo, llm, written)
        sections.append(s)
        written.append(s)

    project_sections: list[SectionDraft] = []
    for spec in koubo.project_sections:
        if spec.get("optional") and not include_optional:
            continue
        s = draft_section(spec, sheet, gap, koubo, llm, written)
        project_sections.append(s)
        written.append(s)

    return sections, project_sections
