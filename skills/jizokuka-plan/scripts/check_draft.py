#!/usr/bin/env python3
"""事業計画書の下書きを機械的に検査する。

LLM に自分の文章を採点させると甘くなる。このスクリプトは LLM を通さず、
「審査で確実に減点される書き方」と「公募要領という規程への不適合」を確定させる。

  品質検査 … 審査実務の経験則（主観語・数値不足・算出式の欠落・分量不足）
  準拠検査 … 公募要領の規程（金額・上限・補助率・対象外経費・文字数）

規程の適合判定を LLM にさせてはならない。補助率は分数で厳密に扱う。
float にすると 600,000 × 2/3 が 400,020 円のような端数が出る。

使い方:
    python3 check_draft.py draft.yaml
    python3 check_draft.py draft.yaml --json      # 機械可読で出す
    python3 check_draft.py draft.yaml --koubo r18 # 公募要領を差し替える

終了コード: 0 = 指摘なしまたは推奨のみ / 1 = 要修正あり
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from fractions import Fraction
from pathlib import Path
from typing import Any

import yaml

REF_DIR = Path(__file__).resolve().parent.parent / "references"

TODO_MARKER = re.compile(r"〈要確認[:：]?([^〉]*)〉")
SENTENCE_SPLIT = re.compile(r"[。！？\n]+")


# ---------------------------------------------------------------------------
# 読み込み
# ---------------------------------------------------------------------------


def load_yaml(path: Path) -> dict:
    if not path.exists():
        sys.exit(f"ファイルが見つかりません: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def strip_todo(text: str) -> str:
    """推定値マーカーは品質判定の対象外（準拠検査で別途エラーにする）."""
    return TODO_MARKER.sub("", text or "")


def char_count(text: str) -> int:
    """記入欄の字数。改行と空白は数えない."""
    return len(re.sub(r"\s+", "", text or ""))


# ---------------------------------------------------------------------------
# 指摘
# ---------------------------------------------------------------------------


class Finding:
    def __init__(self, level: str, code: str, where: str, message: str, fix: str = "") -> None:
        self.level = level  # must / should / error / warn / info
        self.code = code
        self.where = where
        self.message = message
        self.fix = fix

    def as_dict(self) -> dict:
        return {
            "level": self.level,
            "code": self.code,
            "where": self.where,
            "message": self.message,
            "fix": self.fix,
        }


# ---------------------------------------------------------------------------
# 文章品質
# ---------------------------------------------------------------------------


def check_quality(draft: dict, koubo: dict, rules: dict) -> list[Finding]:
    out: list[Finding] = []
    pats = {k: re.compile(v) for k, v in rules["patterns"].items()}
    specs = section_specs(koubo)
    sections = draft.get("sections") or {}

    for key, spec in specs.items():
        body = sections.get(key)
        if not body or not str(body).strip():
            continue
        body = str(body)
        heading = spec["heading"]
        req = (rules.get("section_requirements") or {}).get(key, {})
        clean = strip_todo(body)
        n_chars = char_count(body)

        def add(code: str, level: str, message: str, fix: str) -> None:
            out.append(Finding(level, code, heading, message, fix))

        # -- 分量 ------------------------------------------------------------
        # 記入欄が埋まっていない計画書は、審査員に「書くことがない事業」と映る。
        min_chars = spec.get("min_chars")
        target = spec.get("target_chars") or min_chars
        if min_chars and n_chars < min_chars:
            hints = req.get("hints") or []
            fix = f"{min_chars - n_chars}字以上加筆し、{target}字程度まで充実させる。"
            if hints:
                fix += "不足しがちな観点: " + "、".join(hints)
            add("length_short", "must",
                f"{n_chars}字しかなく、下限{min_chars}字に{min_chars - n_chars}字不足している", fix)

        # -- 主観形容詞 -------------------------------------------------------
        for group in rules.get("subjective_words", []):
            hit = next((w for w in group["words"] if w in clean), None)
            if hit:
                add("subjective", "must",
                    f"主観的な表現『{hit}』が使われている",
                    f"『{hit}』を削り、{group['reason']}")

        # -- 推定値の出所を人に帰属させていないか -----------------------------
        attrib = rules.get("attribution_phrases") or {}
        hit = next((w for w in attrib.get("words", []) if w in clean), None)
        if hit:
            add("attribution", "must",
                f"推定値の出所を人に帰属させている可能性がある（『{hit}』）",
                attrib.get("reason", "推定であることを明示するか〈要確認〉を残す").strip())

        # -- 曖昧な数量表現 ---------------------------------------------------
        vague = rules.get("vague_quantifiers") or {}
        hits = [w for w in vague.get("words", []) if w in clean]
        if hits:
            add("vague", "should",
                f"数量が曖昧な表現がある（{'、'.join(hits[:4])}）",
                vague.get("reason", "実数または割合に置き換える"))

        # -- バズワードの空振り ----------------------------------------------
        bz = rules.get("buzzwords") or {}
        concretizers = bz.get("concretizers", [])
        for w in bz.get("words", []):
            for m in re.finditer(re.escape(w), clean):
                window = clean[max(0, m.start() - 40):m.end() + 40]
                if not any(c in window for c in concretizers):
                    add("buzzword", "should",
                        f"『{w}』が具体策を伴わずに使われている",
                        bz.get("reason", "何をどう変えるのかを書く"))
                    break

        # -- 数値の量 ---------------------------------------------------------
        need = int(req.get("min_numbers", 0))
        if need:
            without_years = pats["year_like"].sub(" ", clean)
            n = len(pats["number"].findall(without_years))
            if n < need:
                hints = req.get("hints") or []
                add("numbers", "must",
                    f"数値が{n}個しかない（この項目では{need}個以上が必要）",
                    ("次のような数値を入れる: " + "、".join(hints)) if hints
                    else "検証可能な数値を追加する")

        # -- 算出式 -----------------------------------------------------------
        if req.get("require_formula") and not pats["formula"].search(clean):
            add("formula", "must", "効果の算出式がない",
                "「客単価◯円 × 客数◯人/月 × 12か月 = 年間◯円」の形で掛け算の式を明示する。"
                "審査員が検算できない効果は評価されない")

        # -- 出典 -------------------------------------------------------------
        if req.get("require_source") and not pats["source"].search(clean):
            add("source", "must", "市場・顧客の記述に出典がない",
                "統計名・調査名・年次を明記する（例「経済センサス（2021年）によれば」）。"
                "出典のない市場分析は一般論とみなされる")

        # -- 競合との比較 ------------------------------------------------------
        if req.get("require_comparison"):
            hits = pats["comparison"].findall(clean)
            if len(hits) < 3:
                add("comparison", "must",
                    f"競合と自社を並べて論じた記述が{len(hits)}箇所しかない",
                    "顧客層・価格・商品・販路・EC対応の軸で「A社は◯◯だが当社は△△」の形で書く。"
                    "自社が劣位な点も挙げ、それをどう補うかまで書く。"
                    "劣位を隠すと分析の妥当性を疑われる")

        # -- 実施スケジュール ---------------------------------------------------
        if req.get("require_schedule") and len(pats["schedule_month"].findall(clean)) < 2:
            add("schedule", "must", "実施スケジュールが月次で示されていない",
                "「◯月: 機器選定と発注」のように補助事業期間を月単位で区切る。実現可能性の根拠になる")

        # -- 年度別目標 ---------------------------------------------------------
        if req.get("require_yearly_target") and not pats["yearly_target"].search(clean):
            add("yearly_target", "must", "年度別の数値目標がない",
                "「初年度◯万円、2年後◯万円、3年後◯万円」のように年度を区切った売上・利益目標を置く")

        # -- 時間削減 -----------------------------------------------------------
        if req.get("require_time_saving") and not pats["time_saving"].search(clean):
            add("time_saving", "should", "削減される作業時間が定量化されていない",
                "「月◯時間の作業が◯時間に短縮される」のように時間で示す")

        # -- 一文の長さ ---------------------------------------------------------
        limit = int((rules.get("readability") or {}).get("max_sentence_chars", 110))
        long_one = next((s.strip() for s in SENTENCE_SPLIT.split(clean) if len(s.strip()) > limit), None)
        if long_one:
            add("long_sentence", "should",
                f"一文が{len(long_one)}字と長い",
                (rules.get("readability") or {}).get("max_sentence_reason", "2文に分割する"))

    out.extend(check_duplication(sections, specs, rules))
    return out


def check_duplication(sections: dict, specs: dict, rules: dict) -> list[Finding]:
    """セクション間で同じ記述が繰り返されていないか."""
    conf = rules.get("duplication") or {}
    n = int(conf.get("min_run_chars", 28))
    grams: dict[str, set[str]] = {}
    for key, body in sections.items():
        if key not in specs or not body:
            continue
        flat = re.sub(r"\s+", "", strip_todo(str(body)))
        grams[key] = {flat[i:i + n] for i in range(max(0, len(flat) - n + 1))}

    out: list[Finding] = []
    keys = list(grams)
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            shared = grams[a] & grams[b]
            if shared:
                sample = max(shared, key=len)
                out.append(Finding(
                    "should", "duplication", specs[b]["heading"],
                    f"「{specs[a]['heading']}」と同じ記述が繰り返されている（「{sample}」）",
                    conf.get("reason", "重複を削り、記入欄を別の情報に使う")))
    return out


# ---------------------------------------------------------------------------
# 公募要領への準拠
# ---------------------------------------------------------------------------


def section_specs(koubo: dict) -> dict[str, dict]:
    form = koubo["form_yoshiki2"]
    specs = {}
    for spec in [*form.get("sections", []), *form.get("project_sections", [])]:
        specs[spec["key"]] = spec
    return specs


def rate_of(koubo: dict, frame: str, is_deficit: bool | None) -> Fraction:
    f = koubo["frames"][frame]
    raw = f["rate_if_deficit"] if (is_deficit and "rate_if_deficit" in f) else f["rate"]
    return Fraction(str(raw))


def limit_of(koubo: dict, frame: str, specials: list[str]) -> int:
    total = int(koubo["frames"][frame]["limit_yen"])
    for s in specials or []:
        sp = (koubo.get("specials") or {}).get(s)
        if sp:
            total += int(sp["add_limit_yen"])
    return total


def amount_of(e: dict) -> int:
    if e.get("amount") is not None:
        return int(e["amount"])
    if e.get("unit_price") is not None:
        return int(e["unit_price"]) * int(e.get("quantity") or 1)
    return 0


def find_category(koubo: dict, name: str) -> dict | None:
    name = (name or "").strip()
    cats = koubo["expense_categories"]
    for c in cats:
        if c["name"] == name or c["code"] == name:
            return c
    for c in cats:
        if name and (name in c["name"] or c["name"] in name):
            return c
    return None


def check_compliance(draft: dict, koubo: dict) -> list[Finding]:
    out: list[Finding] = []
    specs = section_specs(koubo)
    sections = draft.get("sections") or {}
    frame = draft.get("frame") or "通常枠"
    specials = draft.get("specials") or []
    is_deficit = (draft.get("company") or {}).get("is_deficit")

    if frame not in koubo["frames"]:
        out.append(Finding("error", "frame.unknown", frame,
                           f"未知の申請枠『{frame}』", "利用可能: " + "、".join(koubo["frames"])))
        return out

    if not koubo["meta"].get("verified"):
        out.append(Finding(
            "warn", "koubo.verified", koubo["meta"]["display_name"],
            "公募要領ナレッジが未検証。金額・補助率・様式構成が実際と異なる可能性がある",
            "申請する回次の公募要領PDFと突合し、references/koubo_*.yaml の meta.verified を true にする"))

    # -- 上乗せ特例の適格性 --------------------------------------------------
    # 補助上限が倍変わるため、金額の計算より先に疑う。
    if "インボイス特例" in specials:
        spec = (koubo.get("specials") or {}).get("インボイス特例") or {}
        threshold = int(spec.get("sales_threshold_yen", 10_000_000))
        sales = ((draft.get("company") or {}).get("sales") or {})
        top = max((int(v) for v in sales.values()), default=0)
        if top > threshold:
            year = max(sales, key=lambda k: int(sales[k]))
            base_limit = int(koubo["frames"][frame]["limit_yen"])
            out.append(Finding(
                "warn", "specials.invoice_eligibility", "インボイス特例",
                f"インボイス特例を適用していますが、{year}年度の売上が{top:,}円で"
                f"{threshold:,}円を超えています。特例の対象は免税事業者からの転換者であり、"
                f"継続して課税売上高が{threshold:,}円を超える事業者は元から課税事業者のため"
                "適用できません。",
                f"適用できない場合、補助上限は{base_limit:,}円になります。"
                "課税・免税の別を事業者に確認してください。"))
        elif not sales:
            out.append(Finding(
                "info", "specials.invoice_eligibility", "インボイス特例",
                "売上が draft に無いため、インボイス特例の適格性を機械的に確認できません。",
                "company.sales に年度別売上を入れると自動で判定します。"))

    # -- セクション ---------------------------------------------------------
    for key, spec in specs.items():
        body = str(sections.get(key) or "")
        if not body.strip():
            if spec.get("optional"):
                out.append(Finding("info", "section.optional", spec["heading"],
                                   "任意項目が未記入", "記載すると『補助事業計画の有効性』で有利になる"))
            else:
                out.append(Finding("error", "section.missing", spec["heading"], "必須項目がない", ""))
            continue

        n = char_count(body)
        cap = spec.get("max_chars")
        if cap and n > cap:
            level = "error" if spec.get("hard_limit") else "warn"
            out.append(Finding(level, "section.length", spec["heading"],
                               f"{n}字で上限{cap}字を{n - cap}字超過", "記入欄に収まらない。圧縮する"))

        for m in TODO_MARKER.finditer(body):
            out.append(Finding("error", "text.todo", spec["heading"],
                               f"未確認マーカーが残っている: 〈要確認{m.group(1)}〉",
                               "事業者に確認して実数値に置き換えるまで提出できない"))

        for rule in koubo.get("forbidden_expressions", []):
            for m in re.finditer(rule["pattern"], body):
                out.append(Finding("warn", "text.forbidden", spec["heading"],
                                   f"不適切な表現『{m.group(0)}』", rule["reason"]))

    # -- 経費 ---------------------------------------------------------------
    expenses = draft.get("expenses") or []
    if not expenses:
        out.append(Finding("error", "expense.empty", "経費明細", "経費明細が1件もない", ""))
        return out

    total = sum(amount_of(e) for e in expenses)
    rate = rate_of(koubo, frame, is_deficit)
    limit = limit_of(koubo, frame, specials)
    grant = min(int(math.floor(total * rate)), limit)
    rules_exp = koubo.get("expense_rules") or {}
    threshold = int(rules_exp.get("quotation_threshold_yen", 500000))

    by_cat: dict[str, int] = {}
    for i, e in enumerate(expenses, start=1):
        label = f"{i}行目「{e.get('item') or '(品名なし)'}」"
        amount = amount_of(e)
        if amount <= 0:
            out.append(Finding("error", "expense.amount", label, "金額が0円または未設定", ""))

        cat = find_category(koubo, e.get("category") or "")
        if cat is None:
            out.append(Finding("error", "expense.category", label,
                               f"経費区分『{e.get('category')}』が補助対象経費区分にない",
                               "有効な区分: " + "、".join(c["name"] for c in koubo["expense_categories"])))
        else:
            by_cat[cat["name"]] = by_cat.get(cat["name"], 0) + amount

        text = f"{e.get('item') or ''} {e.get('basis') or ''}"
        for rule in koubo.get("ineligible_keywords", []):
            hit = next((kw for kw in rule["keyword"] if kw in text), None)
            if hit:
                out.append(Finding("error", "expense.ineligible", label,
                                   f"対象外の可能性が高い『{hit}』が含まれる。{rule['reason']}",
                                   "対象となる費目への振替、または除外を検討する"))

        unit = e.get("unit_price") if e.get("unit_price") is not None else amount
        if unit and int(unit) >= threshold:
            out.append(Finding("warn", "expense.quotation", label,
                               f"税抜単価{int(unit):,}円。"
                               + rules_exp.get("quotation_threshold_message", "相見積が必要"), ""))

        if not str(e.get("basis") or "").strip():
            out.append(Finding("warn", "expense.basis", label,
                               "積算根拠がない", "『積算の透明・適切性』の減点要因。見積先と金額の出所を書く"))
        if not str(e.get("linked_activity") or "").strip():
            out.append(Finding("warn", "expense.linkage", label,
                               "本文のどの取組に対応するか不明", "本文の見出しと対応づける"))

    # -- 区分ごとの上限 -------------------------------------------------------
    for cat in koubo["expense_categories"]:
        cap_rule = cat.get("cap_rule")
        spent = by_cat.get(cat["name"], 0)
        if not cap_rule or spent <= 0:
            continue
        if cap_rule["type"] == "ratio_of_grant":
            cat_grant = math.floor(spent * rate)
            allowed = math.floor(grant * cap_rule["ratio"])
            if cat_grant > allowed:
                out.append(Finding("error", "expense.cap", cat["name"],
                                   f"{spent:,}円（補助金換算{cat_grant:,}円）が上限{allowed:,}円を超過。"
                                   + cap_rule["message"],
                                   f"{math.floor(allowed / rate):,}円以下に抑えるか、他区分を増やす"))
            if len(by_cat) == 1:
                out.append(Finding("error", "expense.cap.solo", cat["name"],
                                   "この区分のみでの申請はできない", "他の経費区分と組み合わせる"))
        elif cap_rule["type"] == "ratio_of_total_expense":
            allowed = math.floor(total * cap_rule["ratio"])
            if spent > allowed:
                out.append(Finding("error", "expense.cap", cat["name"],
                                   f"{spent:,}円が上限{allowed:,}円を超過。" + cap_rule["message"], ""))

    # -- 補助金額 -------------------------------------------------------------
    theoretical = math.floor(total * rate)
    if theoretical > limit:
        waste = theoretical - limit
        out.append(Finding("warn", "expense.over_limit", "経費合計",
                           f"補助対象経費{total:,}円 × {koubo['frames'][frame]['rate_label']} = "
                           f"{theoretical:,}円が上限{limit:,}円を{waste:,}円超過。"
                           f"超過分{math.ceil(waste / rate):,}円相当は全額自己負担になる",
                           f"上限に張り付く経費の目安は{math.ceil(limit / rate):,}円"))
    elif total and theoretical < limit * 0.5:
        out.append(Finding("info", "expense.under_limit", "経費合計",
                           f"補助金申請額{grant:,}円は上限{limit:,}円の{grant / limit * 100:.0f}%。"
                           "枠を活かしきれていない可能性がある", ""))

    # -- 資金調達 -------------------------------------------------------------
    funding = draft.get("funding") or {}
    if funding:
        funded = sum(int(v) for v in funding.values())
        if funded != total:
            out.append(Finding("error", "funding.mismatch", "資金調達",
                               f"合計{funded:,}円が補助対象経費{total:,}円と一致しない"
                               f"（差額{funded - total:+,}円）",
                               "様式3では両者が一致している必要がある"))
    return out


# ---------------------------------------------------------------------------
# 出力
# ---------------------------------------------------------------------------


BLOCKING = {"must", "error"}
ICON = {"must": "🛑", "error": "🛑", "should": "⚠️ ", "warn": "⚠️ ", "info": "ℹ️ "}
LABEL = {
    "must": "要修正（審査で確実に減点される）",
    "error": "エラー（提出前に必ず解消）",
    "should": "推奨（直すと説得力が上がる）",
    "warn": "警告（採択率に影響）",
    "info": "情報",
}


def report(findings: list[Finding], draft: dict, koubo: dict) -> str:
    lines = [f"# 検査結果 — {draft.get('project_id', '(無題)')}", ""]

    total = sum(amount_of(e) for e in (draft.get("expenses") or []))
    frame = draft.get("frame") or "通常枠"
    if frame in koubo["frames"]:
        rate = rate_of(koubo, frame, (draft.get("company") or {}).get("is_deficit"))
        limit = limit_of(koubo, frame, draft.get("specials") or [])
        grant = min(int(math.floor(total * rate)), limit)
        lines += [f"補助対象経費 {total:,}円 × {koubo['frames'][frame]['rate_label']} "
                  f"→ 補助金申請額 {grant:,}円（上限 {limit:,}円）", ""]

    # 分量の一覧は支援員が最初に見る情報なので先頭に置く
    specs = section_specs(koubo)
    sections = draft.get("sections") or {}
    written = [(specs[k]["heading"], char_count(str(v)), specs[k].get("target_chars"))
               for k, v in sections.items() if k in specs and str(v).strip()]
    if written:
        lines.append("## 分量")
        lines.append("")
        for heading, n, target in written:
            bar = f"{n:,}字"
            if target:
                bar += f" / 目標{target:,}字（{n / target * 100:.0f}%）"
            lines.append(f"- {heading}: {bar}")
        lines.append(f"- **合計 {sum(n for _, n, _ in written):,}字**")
        lines.append("")

    blocking = [f for f in findings if f.level in BLOCKING]
    lines.append(f"## 指摘 — 要修正 {len(blocking)}件 / 全{len(findings)}件")
    lines.append("")
    if not findings:
        lines.append("指摘なし。")
    for level in ("must", "error", "should", "warn", "info"):
        items = [f for f in findings if f.level == level]
        if not items:
            continue
        lines.append(f"### {ICON[level]}{LABEL[level]} — {len(items)}件")
        lines.append("")
        for f in items:
            lines.append(f"- **[{f.code}]** {f.where}: {f.message}")
            if f.fix:
                lines.append(f"  - 直し方: {f.fix}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="事業計画書の下書きを機械的に検査する")
    ap.add_argument("draft", help="下書きの YAML")
    ap.add_argument("--koubo", default="r17", help="公募要領ID（既定 r17）")
    ap.add_argument("--json", action="store_true", help="JSON で出力する")
    args = ap.parse_args()

    draft = load_yaml(Path(args.draft))
    koubo = load_yaml(REF_DIR / f"koubo_{args.koubo}.yaml")
    rules = load_yaml(REF_DIR / "writing_rules.yaml")

    findings = check_quality(draft, koubo, rules) + check_compliance(draft, koubo)

    # 文章の出来とは無関係に申請を潰す論点は、毎回申し送る
    for note in koubo.get("procedural_notes", []):
        findings.append(Finding("info", "procedure", note["key"],
                                note["note"].strip().split("\n")[0]))

    if args.json:
        print(json.dumps([f.as_dict() for f in findings], ensure_ascii=False, indent=2))
    else:
        print(report(findings, draft, koubo))

    return 1 if any(f.level in BLOCKING for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
