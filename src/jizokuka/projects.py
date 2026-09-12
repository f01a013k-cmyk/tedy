"""案件（顧客）ごとの状態管理.

支援機関は同時に数十件を回すため、案件フォルダに全ての中間生成物を残し、
どの段階からでも再開できるようにする。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import yaml

from .config import SETTINGS
from .models import GapAnalysis, HearingSheet, Plan


@dataclass
class Project:
    project_id: str
    root: Path

    # -- パス ---------------------------------------------------------------
    @property
    def hearing_path(self) -> Path:
        return self.root / "hearing.yaml"

    @property
    def gap_path(self) -> Path:
        return self.root / "gap.yaml"

    @property
    def answers_path(self) -> Path:
        return self.root / "answers.yaml"

    @property
    def plan_path(self) -> Path:
        return self.root / "plan.json"

    @property
    def outputs(self) -> Path:
        d = self.root / "outputs"
        d.mkdir(parents=True, exist_ok=True)
        return d

    # -- 入出力 -------------------------------------------------------------
    @classmethod
    def open(cls, project_id: str) -> Project:
        return cls(project_id=project_id, root=SETTINGS.ensure(project_id))

    def save_hearing(self, sheet: HearingSheet) -> Path:
        self.hearing_path.write_text(
            yaml.safe_dump(
                sheet.model_dump(mode="json", exclude_none=True),
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        return self.hearing_path

    def load_hearing(self) -> HearingSheet:
        if not self.hearing_path.exists():
            raise FileNotFoundError(
                f"案件 '{self.project_id}' にヒアリングシートがありません。\n"
                f"  jizokuka new {self.project_id} --from <ファイル>"
            )
        return HearingSheet.model_validate(
            yaml.safe_load(self.hearing_path.read_text(encoding="utf-8"))
        )

    def save_gap(self, gap: GapAnalysis) -> Path:
        self.gap_path.write_text(
            yaml.safe_dump(gap.model_dump(mode="json"), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        return self.gap_path

    def load_gap(self) -> GapAnalysis | None:
        if not self.gap_path.exists():
            return None
        return GapAnalysis.model_validate(
            yaml.safe_load(self.gap_path.read_text(encoding="utf-8"))
        )

    def load_answers(self) -> dict[str, str]:
        if not self.answers_path.exists():
            return {}
        raw = yaml.safe_load(self.answers_path.read_text(encoding="utf-8")) or {}
        answers = raw.get("answers", raw)
        return {str(k): str(v) for k, v in answers.items() if v not in (None, "")}

    def save_plan(self, plan: Plan) -> Path:
        self.plan_path.write_text(
            json.dumps(plan.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return self.plan_path

    def load_plan(self) -> Plan:
        if not self.plan_path.exists():
            raise FileNotFoundError(
                f"案件 '{self.project_id}' に計画がありません。先に `jizokuka build` を実行してください。"
            )
        return Plan.model_validate(json.loads(self.plan_path.read_text(encoding="utf-8")))


def list_projects() -> list[str]:
    ws = SETTINGS.workspace
    if not ws.exists():
        return []
    return sorted(p.name for p in ws.iterdir() if p.is_dir() and (p / "hearing.yaml").exists())
