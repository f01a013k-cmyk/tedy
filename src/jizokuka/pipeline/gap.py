"""ギャップ分析: 単語レベル入力から推論できる事実を立て、聞くべきことだけ質問化する.

本システムの工数削減の中核。「聞けば分かること」を全部聞くのではなく、
推論を誤ると採択可否に響く項目だけを質問にする。
"""

from __future__ import annotations

import yaml

from ..knowledge import Koubo
from ..llm import LLM
from ..llm.client import load_prompt
from ..models import Confidence, Fact, GapAnalysis, HearingSheet, Question


def _sheet_yaml(sheet: HearingSheet) -> str:
    data = sheet.model_dump(mode="json", exclude_none=True, exclude_defaults=False)
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=100)


def analyze(
    sheet: HearingSheet,
    koubo: Koubo,
    llm: LLM,
    max_questions: int = 8,
) -> GapAnalysis:
    prompt = load_prompt("gap_analysis").format(
        max_questions=max_questions,
        hearing_yaml=_sheet_yaml(sheet),
        review_axes=koubo.review_axes_prompt(),
    )
    from ..config import MODEL_EXTRACT

    raw = llm.complete_json(
        prompt,
        system=load_prompt("system"),
        model=MODEL_EXTRACT,
        max_tokens=12000,
        fallback={"facts": [], "questions": [], "blocking": []},
    )

    facts = [_to_fact(f) for f in raw.get("facts", []) if f.get("key")]
    facts = _seed_facts(sheet) + facts

    questions = []
    for i, q in enumerate(raw.get("questions", []), start=1):
        if not q.get("text"):
            continue
        questions.append(
            Question(
                id=q.get("id") or f"q{i}",
                text=q["text"],
                why=q.get("why", ""),
                choices=[str(c) for c in q.get("choices", [])],
                allow_free_text=bool(q.get("allow_free_text", True)),
                impact=max(1, min(5, int(q.get("impact", 3)))),
                section=q.get("section"),
            )
        )
    questions = sorted(questions, key=lambda q: -q.impact)[:max_questions]

    return GapAnalysis(
        facts=facts,
        questions=questions,
        blocking=[str(b) for b in raw.get("blocking", [])],
    )


def _to_fact(f: dict) -> Fact:
    try:
        conf = Confidence(f.get("confidence", "inferred"))
    except ValueError:
        conf = Confidence.INFERRED
    return Fact(
        key=str(f["key"]),
        value=str(f.get("value", "")),
        confidence=conf,
        source=f.get("source"),
        rationale=f.get("rationale"),
    )


def _seed_facts(sheet: HearingSheet) -> list[Fact]:
    """LLM を通さずに確定できる事実. 推論より優先して信頼できる."""
    out: list[Fact] = []
    c = sheet.company

    if c.sales_trend:
        out.append(
            Fact(
                key="売上推移",
                value=c.sales_trend,
                confidence=Confidence.GIVEN,
                source="hearing.company.sales",
            )
        )
    if c.founded:
        out.append(
            Fact(
                key="創業年",
                value=str(c.founded),
                confidence=Confidence.GIVEN,
                source="hearing.company.founded",
            )
        )
    if c.employees is not None:
        out.append(
            Fact(
                key="従業員数",
                value=f"{c.employees}名",
                confidence=Confidence.GIVEN,
                source="hearing.company.employees",
            )
        )
    total = sheet.total_expense()
    if total:
        out.append(
            Fact(
                key="補助対象経費(概算)",
                value=f"{total:,}円",
                confidence=Confidence.GIVEN,
                source="hearing.project.expenses",
            )
        )
    return out


def apply_answers(gap: GapAnalysis, answers: dict[str, str]) -> GapAnalysis:
    """回答ファイル（id -> 回答文字列）をギャップ分析に反映する."""
    for q in gap.questions:
        if q.id in answers and answers[q.id] is not None:
            q.answer = str(answers[q.id]).strip()
    return gap


def facts_text(facts: list[Fact]) -> str:
    if not facts:
        return "（なし）"
    lines = []
    for f in facts:
        mark = {"given": "確", "inferred": "推", "missing": "欠"}[f.confidence.value]
        line = f"[{mark}] {f.key}: {f.value}"
        if f.confidence is Confidence.INFERRED and f.rationale:
            line += f"（推定根拠: {f.rationale}）"
        lines.append(line)
    return "\n".join(lines)


def answers_text(gap: GapAnalysis) -> str:
    answered = [q for q in gap.questions if q.answered]
    if not answered:
        return "（追加質問は未回答。推定事実で補うこと）"
    return "\n".join(f"Q: {q.text}\nA: {q.answer}" for q in answered)
