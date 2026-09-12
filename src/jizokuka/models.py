"""データモデル.

設計方針: ヒアリングシートは「単語レベルでしか埋まっていない」ことを前提とする。
したがって、ほぼ全てのフィールドを optional とし、文字列でもリストでも受け取れる
ようにする。検証で弾くのではなく、欠損を `GapAnalysis` で可視化する。
"""

from __future__ import annotations

import re
from datetime import date
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _as_list(v: Any) -> list[str]:
    """単語1個・カンマ区切り文字列・リストのいずれでもリストに正規化する."""
    if v is None:
        return []
    if isinstance(v, str):
        parts = re.split(r"[,、,\n/／・]+", v)
        return [p.strip() for p in parts if p.strip()]
    if isinstance(v, (list, tuple)):
        out: list[str] = []
        for item in v:
            out.extend(_as_list(item))
        return out
    return [str(v).strip()]


class Loose(BaseModel):
    """未知キーを捨てずに保持するベースモデル.

    支援機関が独自にヒアリング項目を足しても壊れないようにする。
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)


# --------------------------------------------------------------------------
# 入力: ヒアリングシート
# --------------------------------------------------------------------------


class Company(Loose):
    name: str | None = None
    kana: str | None = None
    representative: str | None = None
    address: str | None = None
    founded: str | int | None = Field(default=None, description="創業年")
    employees: int | None = Field(default=None, description="常時使用する従業員数")
    capital: int | None = None
    industry: str | None = None
    business: list[str] = Field(default_factory=list, description="事業内容(単語可)")
    products: list[str] = Field(default_factory=list, description="主力商品・サービス")
    customers: list[str] = Field(default_factory=list, description="顧客層")
    channels: list[str] = Field(default_factory=list, description="販売チャネル")
    area: list[str] = Field(default_factory=list, description="商圏")
    sales: dict[str, int] = Field(default_factory=dict, description="年度→売上高(円)")
    profit: dict[str, int] = Field(default_factory=dict, description="年度→営業利益(円)")
    is_invoice_registered: bool | None = None
    is_deficit: bool | None = None

    _norm = field_validator(
        "business", "products", "customers", "channels", "area", mode="before"
    )(lambda v: _as_list(v))

    @field_validator("sales", "profit", mode="before")
    @classmethod
    def _norm_series(cls, v: Any) -> dict[str, int]:
        if not v:
            return {}
        if isinstance(v, dict):
            return {str(k): int(x) for k, x in v.items() if x is not None}
        return {}

    @property
    def sales_trend(self) -> str | None:
        """売上の増減トレンドを返す（分析文の根拠に使う）."""
        if len(self.sales) < 2:
            return None
        years = sorted(self.sales)
        first, last = self.sales[years[0]], self.sales[years[-1]]
        if first == 0:
            return None
        pct = (last - first) / first * 100
        direction = "増加" if pct > 3 else ("減少" if pct < -3 else "横ばい")
        return f"{years[0]}年度{first:,}円 → {years[-1]}年度{last:,}円（{pct:+.1f}%、{direction}傾向）"


class ExpenseItem(Loose):
    """経費明細1行. 単語レベル入力（費目と概算だけ）を許容する."""

    category: str | None = Field(default=None, description="補助対象経費区分")
    item: str | None = Field(default=None, description="品名・内容")
    unit_price: int | None = None
    quantity: int | None = None
    amount: int | None = Field(default=None, description="税抜金額(円)")
    basis: str | None = Field(default=None, description="積算根拠・見積先")
    linked_activity: str | None = Field(
        default=None, description="本文のどの取組に対応するか"
    )

    def resolved_amount(self) -> int:
        if self.amount is not None:
            return int(self.amount)
        if self.unit_price is not None:
            return int(self.unit_price) * int(self.quantity or 1)
        return 0


class ProjectIdea(Loose):
    """補助事業のタネ. 「冷凍機」「EC」程度の単語しか無いのが普通."""

    idea: list[str] = Field(default_factory=list)
    target_customer: list[str] = Field(default_factory=list)
    expenses: list[ExpenseItem] = Field(default_factory=list)
    schedule_note: str | None = None

    _norm = field_validator("idea", "target_customer", mode="before")(
        lambda v: _as_list(v)
    )


class HearingSheet(Loose):
    """顧客ヒアリングシート（単語レベル可）."""

    project_id: str
    koubo_id: str = "r17"
    frame: str = "通常枠"
    specials: list[str] = Field(default_factory=list, description="インボイス特例 等")
    company: Company = Field(default_factory=Company)
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    market: list[str] = Field(default_factory=list, description="市場動向のキーワード")
    goal: list[str] = Field(default_factory=list, description="今後やりたいこと")
    project: ProjectIdea = Field(default_factory=ProjectIdea)
    policy_bonus: list[str] = Field(default_factory=list)
    free_notes: str | None = Field(default=None, description="面談メモ等の自由記述")

    _norm = field_validator(
        "specials", "strengths", "weaknesses", "market", "goal", "policy_bonus",
        mode="before",
    )(lambda v: _as_list(v))

    def total_expense(self) -> int:
        return sum(e.resolved_amount() for e in self.project.expenses)


# --------------------------------------------------------------------------
# 中間: ギャップ分析と追加質問
# --------------------------------------------------------------------------


class Confidence(str, Enum):
    GIVEN = "given"        # 顧客が明示的に回答した
    INFERRED = "inferred"  # 単語から推論した（要確認）
    MISSING = "missing"    # 不明


class Fact(Loose):
    """推論を含む確定事実. 出所を必ず持たせ、捏造を検知できるようにする."""

    key: str
    value: str
    confidence: Confidence = Confidence.INFERRED
    source: str | None = Field(default=None, description="どの入力語から導いたか")
    rationale: str | None = None


class Question(Loose):
    """顧客への追加質問. 自由記述させず、必ず選択肢を用意して手間を減らす."""

    id: str
    text: str
    why: str = Field(description="どの審査観点に効くか（顧客に提示する）")
    choices: list[str] = Field(default_factory=list, description="推定選択肢")
    allow_free_text: bool = True
    impact: int = Field(default=3, ge=1, le=5, description="採択への影響度 1-5")
    section: str | None = None
    answer: str | None = None

    @property
    def answered(self) -> bool:
        return bool(self.answer and self.answer.strip())


class GapAnalysis(Loose):
    facts: list[Fact] = Field(default_factory=list)
    questions: list[Question] = Field(default_factory=list)
    blocking: list[str] = Field(
        default_factory=list, description="これが無いと計画が書けない致命的欠損"
    )

    def unanswered(self) -> list[Question]:
        return sorted(
            [q for q in self.questions if not q.answered],
            key=lambda q: -q.impact,
        )


# --------------------------------------------------------------------------
# 出力: ドラフトと採点
# --------------------------------------------------------------------------


class SectionDraft(Loose):
    key: str
    heading: str
    body: str
    char_count: int = 0
    max_chars: int | None = None
    revision: int = 0

    def model_post_init(self, _ctx: Any) -> None:
        self.char_count = len(self.body.replace("\n", ""))

    @property
    def over_limit(self) -> bool:
        return self.max_chars is not None and self.char_count > self.max_chars


class AxisScore(Loose):
    axis: str
    score: int = Field(ge=0, le=100)
    weight: int = 0
    findings: list[str] = Field(default_factory=list, description="減点理由")
    improvements: list[str] = Field(default_factory=list, description="具体的改善指示")


class ScoreReport(Loose):
    axes: list[AxisScore] = Field(default_factory=list)
    total: float = 0.0
    verdict: str = ""

    def recompute(self) -> float:
        w = sum(a.weight for a in self.axes) or 1
        self.total = round(sum(a.score * a.weight for a in self.axes) / w, 1)
        if self.total >= 80:
            self.verdict = "採択圏（A）"
        elif self.total >= 70:
            self.verdict = "当落線上（B）"
        elif self.total >= 55:
            self.verdict = "要改善（C）"
        else:
            self.verdict = "大幅な作り直しが必要（D）"
        return self.total

    def weakest_axes(self, n: int = 2) -> list[AxisScore]:
        return sorted(self.axes, key=lambda a: a.score)[:n]


class ComplianceIssue(Loose):
    severity: str = Field(description="error / warn / info")
    rule: str
    message: str
    where: str | None = None
    fix_hint: str | None = None


class ComplianceReport(Loose):
    issues: list[ComplianceIssue] = Field(default_factory=list)

    @property
    def errors(self) -> list[ComplianceIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[ComplianceIssue]:
        return [i for i in self.issues if i.severity == "warn"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def add(self, severity: str, rule: str, message: str, **kw: Any) -> None:
        self.issues.append(
            ComplianceIssue(severity=severity, rule=rule, message=message, **kw)
        )


class Plan(Loose):
    """1案件の成果物一式."""

    project_id: str
    koubo_id: str
    frame: str
    specials: list[str] = Field(default_factory=list)
    generated_at: str = Field(default_factory=lambda: date.today().isoformat())
    sections: list[SectionDraft] = Field(default_factory=list)
    project_sections: list[SectionDraft] = Field(default_factory=list)
    expenses: list[ExpenseItem] = Field(default_factory=list)
    funding: dict[str, int] = Field(default_factory=dict)
    score: ScoreReport | None = None
    compliance: ComplianceReport | None = None
    facts: list[Fact] = Field(default_factory=list)

    def all_sections(self) -> list[SectionDraft]:
        return [*self.sections, *self.project_sections]

    def section(self, key: str) -> SectionDraft | None:
        return next((s for s in self.all_sections() if s.key == key), None)

    def full_text(self) -> str:
        return "\n\n".join(f"【{s.heading}】\n{s.body}" for s in self.all_sections())
