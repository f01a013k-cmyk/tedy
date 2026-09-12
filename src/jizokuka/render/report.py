"""審査観点スコアレポート（支援員向け内部資料）."""

from __future__ import annotations

from ..models import Plan

_BAR_FULL, _BAR_EMPTY = "█", "░"


def _bar(score: int, width: int = 20) -> str:
    filled = round(score / 100 * width)
    return _BAR_FULL * filled + _BAR_EMPTY * (width - filled)


def score_report(plan: Plan, history: list[float] | None = None) -> str:
    out = [f"# 審査観点スコアレポート — {plan.project_id}", ""]

    if plan.quality:
        must = [f for f in plan.quality if f.severity == "must"]
        should = [f for f in plan.quality if f.severity != "must"]
        out += [
            f"> 文章品質チェック: **要修正 {len(must)}件** / 推奨 {len(should)}件",
            "",
        ]

    if plan.score:
        s = plan.score
        out += [
            f"## 総合 {s.total}点 / 100点 — {s.verdict}",
            "",
            "```",
            f"総合 {_bar(int(s.total))} {s.total}",
            "```",
            "",
        ]
        if history and len(history) > 1:
            out += [
                "### スコア推移",
                "",
                " → ".join(f"{h}" for h in history)
                + f"（改善 {history[-1] - history[0]:+.1f}点）",
                "",
            ]

        out += ["## 観点別", ""]
        for a in sorted(s.axes, key=lambda x: x.score):
            out += [
                f"### {a.axis} — {a.score}点（配点{a.weight}）",
                "",
                f"```\n{_bar(a.score)} {a.score}\n```",
                "",
            ]
            if a.findings:
                out.append("**指摘**")
                out += [f"- {f}" for f in a.findings]
                out.append("")
            if a.improvements:
                out.append("**改善指示（次回リライトで反映すべき内容）**")
                out += [f"- {i}" for i in a.improvements]
                out.append("")
    else:
        out.append("（未採点）")

    out += _quality_section(plan)

    if plan.compliance:
        c = plan.compliance
        out += ["## 公募要領準拠チェック", ""]
        if not c.issues:
            out.append("指摘なし。")
        else:
            icon = {"error": "🛑", "warn": "⚠️", "info": "ℹ️"}
            for sev, label in (("error", "エラー（提出前に必ず解消）"),
                               ("warn", "警告（採択率に影響）"),
                               ("info", "情報")):
                items = [i for i in c.issues if i.severity == sev]
                if not items:
                    continue
                out += [f"### {icon[sev]} {label} — {len(items)}件", ""]
                for i in items:
                    line = f"- **[{i.rule}]** {i.message}"
                    if i.where:
                        line += f"　（該当: {i.where}）"
                    out.append(line)
                    if i.fix_hint:
                        out.append(f"  - 対処: {i.fix_hint}")
                out.append("")

    # 要確認マーカーの一覧（提出前チェックリストとして使う）
    todos = []
    for sec in plan.all_sections():
        for line in sec.body.splitlines():
            if "〈要確認" in line:
                todos.append((sec.heading, line.strip()))
    if todos:
        out += ["## 事業者に確認が必要な箇所", "",
                "以下は推定値です。実数値に置き換えるまで提出しないでください。", ""]
        for heading, line in todos:
            out.append(f"- **{heading}**: {line}")
        out.append("")

    return "\n".join(out)


def _quality_section(plan: Plan) -> list[str]:
    """文章品質チェックの結果. LLM を通さず検出した、審査で確実に減点される書き方."""
    if not plan.quality:
        return []

    out = ["## 文章品質チェック（機械検査）", ""]
    labels = {
        "must": ("🛑", "要修正 — 審査で確実に減点される"),
        "should": ("⚠️", "推奨 — 直すと説得力が上がる"),
    }
    for severity in ("must", "should"):
        items = [f for f in plan.quality if f.severity == severity]
        if not items:
            continue
        icon, label = labels[severity]
        out += [f"### {icon} {label} — {len(items)}件", ""]

        by_section: dict[str, list] = {}
        for f in items:
            by_section.setdefault(f.section_heading or f.section_key, []).append(f)
        for heading, fs in by_section.items():
            out.append(f"**{heading}**")
            for f in fs:
                out.append(f"- `{f.code}` {f.message}")
                out.append(f"  - 直し方: {f.fix}")
                if f.excerpt:
                    out.append(f"  - 該当: 「{f.excerpt.strip()}」")
            out.append("")
    return out
