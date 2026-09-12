"""文章品質の決定論的チェック.

審査で確実に減点される「書き方の型」を、LLM を呼ばずに検出する。

役割の分担:
  compliance.py … 公募要領という**規程**への適合（金額・上限・文字数）
  quality.py    … 審査実務の**経験則**（主観語・数値不足・算出式の欠落）

検出結果はそのままリライト指示として使える粒度にする。
「具体性を高める」ではなく「客単価と客数から算出式を書く」と言えること。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from ..config import KNOWLEDGE_DIR
from ..models import Plan, QualityFinding, SectionDraft

# 推定値マーカーは品質判定の対象外にする（compliance 側で別途エラーにする）
_TODO = re.compile(r"〈要確認[^〉]*〉")
_SENTENCE_SPLIT = re.compile(r"[。！？\n]+")


@dataclass
class WritingRules:
    raw: dict[str, Any]
    path: Path

    @property
    def patterns(self) -> dict[str, re.Pattern[str]]:
        return {k: re.compile(v) for k, v in self.raw["patterns"].items()}

    def requirements(self, section_key: str) -> dict[str, Any]:
        return self.raw.get("section_requirements", {}).get(section_key, {})


@lru_cache(maxsize=2)
def load_rules() -> WritingRules:
    path = KNOWLEDGE_DIR / "writing_rules.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    for name, pat in raw.get("patterns", {}).items():
        try:
            re.compile(pat)
        except re.error as e:
            raise ValueError(f"{path}: パターン '{name}' が不正です: {e}") from e
    return WritingRules(raw=raw, path=path)


# --------------------------------------------------------------------------


def _strip(text: str) -> str:
    return _TODO.sub("", text)


def count_numbers(text: str, rules: WritingRules) -> int:
    """年号を除いた数値トークンの個数.

    「2025年」を数値として数えると、年号を並べただけで要件を満たせてしまう。
    """
    pats = rules.patterns
    without_years = pats["year_like"].sub(" ", _strip(text))
    return len(pats["number"].findall(without_years))


def check_section(
    sec: SectionDraft, rules: WritingRules | None = None
) -> list[QualityFinding]:
    rules = rules or load_rules()
    body = _strip(sec.body)
    pats = rules.patterns
    req = rules.requirements(sec.key)
    out: list[QualityFinding] = []

    def add(code: str, severity: str, message: str, fix: str, excerpt: str | None = None) -> None:
        out.append(
            QualityFinding(
                section_key=sec.key,
                section_heading=sec.heading,
                code=code,
                severity=severity,
                message=message,
                fix=fix,
                excerpt=excerpt,
            )
        )

    # -- 主観形容詞 ---------------------------------------------------------
    for group in rules.raw.get("subjective_words", []):
        for w in group["words"]:
            if w in body:
                add(
                    "subjective",
                    "must",
                    f"主観的な表現『{w}』が使われている",
                    f"『{w}』を削り、{group['reason']}",
                    excerpt=_around(body, w),
                )
                break  # 同じ理由の指摘を重ねない

    # -- 曖昧な数量表現 -----------------------------------------------------
    vague = rules.raw.get("vague_quantifiers", {})
    hits = [w for w in vague.get("words", []) if w in body]
    if hits:
        add(
            "vague",
            "should",
            f"数量が曖昧な表現がある（{'、'.join(hits[:4])}）",
            vague.get("reason", "実数または割合に置き換える"),
            excerpt=_around(body, hits[0]),
        )

    # -- バズワードの空振り -------------------------------------------------
    bz = rules.raw.get("buzzwords", {})
    concretizers = bz.get("concretizers", [])
    for w in bz.get("words", []):
        for m in re.finditer(re.escape(w), body):
            window = body[max(0, m.start() - 40) : m.end() + 40]
            if not any(c in window for c in concretizers):
                add(
                    "buzzword",
                    "should",
                    f"『{w}』が具体策を伴わずに使われている",
                    bz.get("reason", "何をどう変えるのかを書く"),
                    excerpt=window,
                )
                break

    # -- 数値の量 -----------------------------------------------------------
    min_numbers = int(req.get("min_numbers", 0))
    if min_numbers:
        n = count_numbers(sec.body, rules)
        if n < min_numbers:
            hints = req.get("hints", [])
            add(
                "numbers",
                "must",
                f"数値が{n}個しかない（この項目では{min_numbers}個以上が必要）",
                "次のような数値を本文に入れる: " + "、".join(hints)
                if hints
                else "検証可能な数値を追加する",
            )

    # -- 算出式 -------------------------------------------------------------
    if req.get("require_formula") and not pats["formula"].search(body):
        add(
            "formula",
            "must",
            "効果の算出式がない",
            "「客単価◯円 × 想定客数◯人/月 × 12か月 = 年間◯円」の形で、"
            "掛け算の式を明示する。審査員が検算できない効果は評価されない",
        )

    # -- 出典 ---------------------------------------------------------------
    if req.get("require_source") and not pats["source"].search(body):
        add(
            "source",
            "must",
            "市場・顧客の記述に出典がない",
            "統計名・調査名・年次を明記する（例「経済センサス（2021年）によれば」）。"
            "出典のない市場分析は一般論とみなされる",
        )

    # -- 実施スケジュール ---------------------------------------------------
    if req.get("require_schedule"):
        months = pats["schedule_month"].findall(body)
        if len(months) < 2:
            add(
                "schedule",
                "must",
                "実施スケジュールが月次で示されていない",
                "「◯月: 機器選定と発注」のように、補助事業期間を月単位で区切って"
                "実施内容を並べる。実現可能性の根拠になる",
            )

    # -- 年度別目標 ---------------------------------------------------------
    if req.get("require_yearly_target") and not pats["yearly_target"].search(body):
        add(
            "yearly_target",
            "must",
            "年度別の数値目標がない",
            "「初年度◯万円、2年後◯万円、3年後◯万円」のように年度を区切った"
            "売上・利益目標を置く",
        )

    # -- 時間削減 -----------------------------------------------------------
    if req.get("require_time_saving") and not pats["time_saving"].search(body):
        add(
            "time_saving",
            "should",
            "削減される作業時間が定量化されていない",
            "「月◯時間の作業が◯時間に短縮される」のように時間で示す",
        )

    # -- 一文の長さ ---------------------------------------------------------
    max_chars = int(rules.raw.get("readability", {}).get("max_sentence_chars", 110))
    for s in _SENTENCE_SPLIT.split(body):
        s = s.strip()
        if len(s) > max_chars:
            add(
                "long_sentence",
                "should",
                f"一文が{len(s)}字と長い",
                rules.raw.get("readability", {}).get(
                    "max_sentence_reason", "2文に分割する"
                ),
                excerpt=s[:60] + "…",
            )
            break  # 1セクションにつき1回だけ指摘する

    return out


def check_duplication(
    sections: list[SectionDraft], rules: WritingRules | None = None
) -> list[QualityFinding]:
    """セクション間で同じ記述が繰り返されていないか."""
    rules = rules or load_rules()
    conf = rules.raw.get("duplication", {})
    n = int(conf.get("min_run_chars", 28))

    grams: dict[str, set[str]] = {}
    for sec in sections:
        body = re.sub(r"\s+", "", _strip(sec.body))
        grams[sec.key] = {body[i : i + n] for i in range(max(0, len(body) - n + 1))}

    out: list[QualityFinding] = []
    keys = [s.key for s in sections]
    by_key = {s.key: s for s in sections}
    seen: set[tuple[str, str]] = set()

    for i, a in enumerate(keys):
        for b in keys[i + 1 :]:
            shared = grams[a] & grams[b]
            if not shared or (a, b) in seen:
                continue
            seen.add((a, b))
            sample = max(shared, key=len)
            out.append(
                QualityFinding(
                    section_key=b,
                    section_heading=by_key[b].heading,
                    code="duplication",
                    severity="should",
                    message=f"「{by_key[a].heading}」と同じ記述が繰り返されている",
                    fix=conf.get("reason", "重複を削り、記入欄を別の情報に使う"),
                    excerpt=sample,
                )
            )
    return out


def check_plan(plan: Plan, rules: WritingRules | None = None) -> list[QualityFinding]:
    rules = rules or load_rules()
    out: list[QualityFinding] = []
    for sec in plan.all_sections():
        out.extend(check_section(sec, rules))
    out.extend(check_duplication(plan.all_sections(), rules))
    return out


def findings_for(findings: list[QualityFinding], section_key: str) -> list[QualityFinding]:
    return [f for f in findings if f.section_key == section_key]


def summarize(findings: list[QualityFinding]) -> str:
    """リライトプロンプトに差し込める形の指示文."""
    if not findings:
        return ""
    lines = []
    for f in findings:
        line = f"- {f.message} → {f.fix}"
        if f.excerpt:
            line += f"（該当箇所: 「{f.excerpt.strip()}」）"
        lines.append(line)
    return "\n".join(lines)


def _around(text: str, word: str, width: int = 24) -> str | None:
    i = text.find(word)
    if i < 0:
        return None
    return text[max(0, i - width) : i + len(word) + width]


def requirements_prompt(section_key: str, rules: WritingRules | None = None) -> str:
    """執筆時点で満たすべき品質要件を明文化する.

    後段の品質チェックと同じ条件を最初のドラフトに伝えることで、
    リライト回数（= API 呼び出し回数）を減らす。
    """
    rules = rules or load_rules()
    req = rules.requirements(section_key)
    if not req:
        return ""

    lines: list[str] = []
    n = int(req.get("min_numbers", 0))
    if n:
        hints = req.get("hints", [])
        line = f"- 検証可能な**数値を{n}個以上**含めること（年号は数に含めない）"
        if hints:
            line += f"。例: {'、'.join(hints)}"
        lines.append(line)
    if req.get("require_formula"):
        lines.append(
            "- 効果は**掛け算の算出式**で示すこと"
            "（例「客単価◯円 × 客数◯人/月 × 12か月 = 年間◯円」）。"
            "審査員が検算できない効果は評価されない"
        )
    if req.get("require_source"):
        lines.append(
            "- 市場・顧客の記述には**出典（統計名・調査名・年次）を明記**すること。"
            "出典がなければ一般論とみなされる"
        )
    if req.get("require_schedule"):
        lines.append(
            "- **実施スケジュールを月単位**で示すこと（例「4月: 機種選定と発注」）。"
            "最低2か月分以上を並べ、実現可能性の根拠とすること"
        )
    if req.get("require_yearly_target"):
        lines.append("- **年度別の数値目標**（売上・利益）を3年度分置くこと")
    if req.get("require_time_saving"):
        lines.append("- 削減される作業時間を**時間または分**で定量化すること")

    banned: list[str] = []
    for g in rules.raw.get("subjective_words", []):
        banned.extend(g["words"][:3])
    lines.append(
        f"- 次の主観的表現は使用禁止: {'、'.join(banned)} ほか。"
        "検証可能な事実に置き換えること"
    )
    lines.append(
        "- 数量が曖昧な表現（多くの・さまざま・大幅に・徐々に）を使わず、実数か割合で書くこと"
    )
    lines.append(
        f"- 一文は{rules.raw.get('readability', {}).get('max_sentence_chars', 110)}字以内に収めること"
    )
    return "\n".join(lines)
