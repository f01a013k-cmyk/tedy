"""同梱サンプル（模範解答）の読み込み.

API キーが無くても、支援員が「このシステムが何を出すのか」「採択レベルとは
どの粒度か」を確認できるようにするための機能。
"""

from __future__ import annotations

from pathlib import Path

import yaml

from .config import EXAMPLE_DIR
from .knowledge import Koubo, load_koubo
from .models import ExpenseItem, HearingSheet, Plan, SectionDraft
from .pipeline import compliance, quality
from .pipeline.expense import funding_plan

MODEL_ANSWER = "tanaka_seika_模範解答.yaml"
SAMPLE_HEARING = "tanaka_seika.yaml"


def sample_hearing_path() -> Path:
    return EXAMPLE_DIR / SAMPLE_HEARING


def load_sample_hearing() -> HearingSheet:
    path = sample_hearing_path()
    return HearingSheet.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def load_model_answer(koubo: Koubo | None = None) -> Plan:
    """模範解答を Plan として読み込み、検査まで通した状態で返す."""
    path = EXAMPLE_DIR / MODEL_ANSWER
    if not path.exists():
        raise FileNotFoundError(f"模範解答が見つかりません: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    koubo = koubo or load_koubo(raw.get("koubo_id", "r17"))

    def _sections(items: list[dict]) -> list[SectionDraft]:
        out = []
        for s in items:
            spec = koubo.section_spec(s["key"])
            if spec is None:
                raise ValueError(f"{path}: 未知のセクションキー '{s['key']}'")
            out.append(
                SectionDraft(
                    key=s["key"],
                    heading=s["heading"],
                    body=s["body"].strip(),
                    max_chars=spec.get("max_chars"),
                )
            )
        return out

    hearing = load_sample_hearing()
    plan = Plan(
        company_name=hearing.company.name,
        representative=hearing.company.representative,
        project_id=raw["project_id"],
        koubo_id=koubo.id,
        frame=raw["frame"],
        specials=raw.get("specials", []),
        sections=_sections(raw["sections"]),
        project_sections=_sections(raw["project_sections"]),
        expenses=[ExpenseItem.model_validate(e) for e in raw["expenses"]],
    )
    total = sum(e.resolved_amount() for e in plan.expenses)
    plan.funding = funding_plan(total, koubo, plan.frame, plan.specials)
    plan.quality = quality.check_plan(plan)
    plan.compliance = compliance.check(plan, koubo)
    return plan
