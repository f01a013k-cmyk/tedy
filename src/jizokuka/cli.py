"""コマンドラインインタフェース.

支援員の実務フローに沿ったコマンド体系:

    new    ヒアリングシートを案件として取り込む
    ask    ギャップ分析し、顧客に送る追加質問シートを出す
    build  事業計画書を生成する（採点・リライト込み）
    check  公募要領準拠チェックだけを再実行する
    render 生成済みの計画から成果物を出し直す
    list   案件一覧
    koubo  公募要領ナレッジの確認・突合
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .config import SETTINGS
from .knowledge import load_koubo
from .llm import LLM, LLMError
from .models import Plan
from .pipeline import compliance, quality
from .projects import Project, list_projects
from .render import docx_form, markdown, report, xlsx_expense

C_OK, C_WARN, C_ERR, C_DIM, C_RESET = "\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[0m"


def _say(msg: str) -> None:
    print(msg, file=sys.stderr)


def _ok(msg: str) -> None:
    _say(f"{C_OK}✔{C_RESET} {msg}")


def _warn(msg: str) -> None:
    _say(f"{C_WARN}⚠{C_RESET} {msg}")


def _err(msg: str) -> None:
    _say(f"{C_ERR}✖{C_RESET} {msg}")


def _make_llm(args: argparse.Namespace) -> LLM:
    return LLM(dry_run=getattr(args, "dry_run", False), use_cache=not getattr(args, "no_cache", False))


# --------------------------------------------------------------------------
# コマンド
# --------------------------------------------------------------------------


def cmd_new(args: argparse.Namespace) -> int:
    from .pipeline import ingest

    sheet = ingest.load(args.source)
    sheet.project_id = args.project_id
    if args.frame:
        sheet.frame = args.frame
    if args.koubo:
        sheet.koubo_id = args.koubo

    proj = Project.open(args.project_id)
    path = proj.save_hearing(sheet)
    _ok(f"案件 '{args.project_id}' を作成しました → {path}")

    filled = sum(
        1
        for v in sheet.model_dump(exclude_none=True).values()
        if v not in ([], {}, "", None)
    )
    _say(f"{C_DIM}  取り込み項目: {filled} / 申請枠: {sheet.frame} / 経費: "
         f"{sheet.total_expense():,}円{C_RESET}")
    _say(f"\n次: jizokuka ask {args.project_id}")
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    from .pipeline import gap as gap_mod

    proj = Project.open(args.project_id)
    sheet = proj.load_hearing()
    koubo = load_koubo(sheet.koubo_id)

    _say("ギャップ分析中… 単語から推論できる事実を立て、聞くべきことだけ質問化します")
    g = gap_mod.analyze(sheet, koubo, _make_llm(args), max_questions=args.max_questions)
    proj.save_gap(g)

    inferred = sum(1 for f in g.facts if f.confidence.value == "inferred")
    _ok(f"事実 {len(g.facts)}件（うち推定 {inferred}件） / 追加質問 {len(g.questions)}件")

    if g.blocking:
        for b in g.blocking:
            _err(f"計画作成に不可欠な欠損: {b}")

    q_path = proj.outputs / "追加質問シート.md"
    markdown.write(q_path, markdown.questions_markdown(g, args.project_id))
    _ok(f"追加質問シート → {q_path}")

    # 回答テンプレを用意しておく（支援員が転記するだけで済むように）
    if not proj.answers_path.exists() and g.questions:
        lines = ["# 顧客からの回答を転記してください", "answers:"]
        for q in g.questions:
            lines.append(f"  {q.id}: \"\"   # {q.text}")
        proj.answers_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        _ok(f"回答テンプレ → {proj.answers_path}")

    _say(f"\n次: 顧客に追加質問シートを送付 → answers.yaml に転記 → "
         f"jizokuka build {args.project_id}")
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    from .generator import build as build_plan

    proj = Project.open(args.project_id)
    sheet = proj.load_hearing()
    koubo = load_koubo(sheet.koubo_id)
    llm = _make_llm(args)

    existing_gap = None if args.regap else proj.load_gap()
    answers = proj.load_answers()
    if answers:
        _say(f"{C_DIM}回答 {len(answers)}件を読み込みました{C_RESET}")

    result = build_plan(
        sheet,
        llm,
        koubo=koubo,
        existing_gap=existing_gap,
        answers=answers,
        max_revisions=args.max_revisions,
        target_score=args.target_score,
        report=_say,
    )

    proj.save_gap(result.gap)
    proj.save_plan(result.plan)

    for w in result.expense_warnings:
        _warn(f"経費: {w}")

    _render_all(proj, result.plan, koubo, result.score_history)
    return _summary(result.plan, koubo)


def cmd_check(args: argparse.Namespace) -> int:
    proj = Project.open(args.project_id)
    plan = proj.load_plan()
    sheet = proj.load_hearing()
    koubo = load_koubo(plan.koubo_id)

    plan.compliance = compliance.check(plan, koubo, sheet)
    plan.quality = quality.check_plan(plan)
    proj.save_plan(plan)

    for f in plan.quality:
        fn = _err if f.severity == "must" else _warn
        fn(f"[{f.code}] {f.section_heading}: {f.message}")
        _say(f"{C_DIM}    → {f.fix}{C_RESET}")

    for i in plan.compliance.issues:
        fn = {"error": _err, "warn": _warn}.get(i.severity, _say)
        fn(f"[{i.rule}] {i.message}" + (f"（{i.where}）" if i.where else ""))
        if i.fix_hint:
            _say(f"{C_DIM}    → {i.fix_hint}{C_RESET}")

    return _summary(plan, koubo)


def cmd_render(args: argparse.Namespace) -> int:
    proj = Project.open(args.project_id)
    plan = proj.load_plan()
    koubo = load_koubo(plan.koubo_id)
    plan.quality = quality.check_plan(plan)
    _render_all(proj, plan, koubo, None, template=args.template)
    return _summary(plan, koubo)


def cmd_sample(args: argparse.Namespace) -> int:
    """同梱の模範解答から成果物一式を生成する（LLM を使わない）.

    API キーが無くても、出力物の形と「採択レベルとはどの粒度か」を確認できる。
    """
    from .samples import load_model_answer, sample_hearing_path

    plan = load_model_answer()
    koubo = load_koubo(plan.koubo_id)
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)

    paths = [
        markdown.write(out / "事業計画書.md", markdown.plan_markdown(plan, koubo)),
        markdown.write(out / "審査スコアレポート.md", report.score_report(plan, None)),
        docx_form.build_yoshiki2(plan, koubo, out / "様式2_経営計画書兼補助事業計画書1.docx"),
        docx_form.build_yoshiki3(plan, koubo, out / "様式3_補助事業計画書2.docx"),
        xlsx_expense.build(plan, koubo, out / "経費明細・資金調達.xlsx"),
    ]
    for pth in paths:
        _ok(f"{pth.name} → {pth}")

    _say("")
    _say(f"{C_DIM}元になったヒアリングシート（単語レベル）: {sample_hearing_path()}{C_RESET}")
    _say(f"{C_DIM}※ 本サンプルの事業者・数値・統計はすべて架空です。"
         f"書き方の型を示すためのもので、統計数値を流用しないでください。{C_RESET}")
    return _summary(plan, koubo)


def cmd_list(_args: argparse.Namespace) -> int:
    ids = list_projects()
    if not ids:
        _say(f"案件がありません（{SETTINGS.workspace}）")
        return 0
    print(f"{'案件ID':<28} {'枠':<12} {'スコア':>7}  状態")
    for pid in ids:
        proj = Project.open(pid)
        try:
            sheet = proj.load_hearing()
            frame = sheet.frame
        except Exception:
            frame = "-"
        score, state = "-", "未生成"
        if proj.plan_path.exists():
            try:
                plan = Plan.model_validate_json(proj.plan_path.read_text(encoding="utf-8"))
                if plan.score:
                    score = f"{plan.score.total}"
                n_err = len(plan.compliance.errors) if plan.compliance else 0
                state = "要修正" if n_err else "生成済"
                if n_err:
                    state += f"（エラー{n_err}件）"
            except Exception:
                state = "読込失敗"
        elif proj.gap_path.exists():
            state = "質問中"
        print(f"{pid:<28} {frame:<12} {score:>7}  {state}")
    return 0


def cmd_koubo(args: argparse.Namespace) -> int:
    koubo = load_koubo(args.koubo)
    print(f"# {koubo.display_name}（{koubo.id}）")
    print(f"検証済み: {'はい' if koubo.verified else 'いいえ'}")
    print(f"定義ファイル: {koubo.path}\n")

    if args.verify:
        print("## 公募要領との突合チェックリスト\n")
        print("以下を公募要領PDFと照合し、一致したらファイルの meta.verified を true にしてください。\n")
        for name, f in koubo.raw["frames"].items():
            print(f"- [ ] {name}: 上限 {f['limit_yen']:,}円 / 補助率 {f['rate_label']}")
        for name, s in koubo.raw.get("specials", {}).items():
            print(f"- [ ] {name}: +{s['add_limit_yen']:,}円（{s['condition']}）")
        print(f"- [ ] 補助対象経費区分が {len(koubo.expense_categories)} 区分で一致")
        for c in koubo.expense_categories:
            if c.get("cap_rule"):
                print(f"  - [ ] {c['name']}: {c['cap_rule']['message']}")
        print(f"- [ ] 様式2の項目構成（経営計画{len(koubo.sections)}項目 / "
              f"補助事業計画{len(koubo.project_sections)}項目）")
        for spec in koubo.all_section_specs():
            print(f"  - [ ] {spec['number']}. {spec['heading']}（記入欄の目安 {spec.get('max_chars')}字）")
        print(f"- [ ] 審査の観点 {len(koubo.review_axes)}項目")
        print(f"- [ ] 相見積の閾値 {koubo.expense_rules.get('quotation_threshold_yen', 0):,}円")
        return 0

    print("## 申請枠")
    for name, f in koubo.raw["frames"].items():
        print(f"  {name:<14} 上限 {f['limit_yen']:>9,}円  補助率 {f['rate_label']}")
    print("\n## 補助対象経費区分")
    for c in koubo.expense_categories:
        cap = f"  ※{c['cap_rule']['message']}" if c.get("cap_rule") else ""
        print(f"  {c['code']:>2}. {c['name']}{cap}")
    return 0


def cmd_template(args: argparse.Namespace) -> int:
    from .config import TEMPLATE_DIR

    src = TEMPLATE_DIR / "hearing_sheet.yaml"
    if not src.exists():
        _err(f"雛形が見つかりません: {src}")
        return 1
    dest = Path(args.output)
    dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    _ok(f"ヒアリングシート雛形 → {dest}")
    _say(f"{C_DIM}  単語レベルで構いません。埋まらない項目は空のままで結構です。{C_RESET}")
    return 0


# --------------------------------------------------------------------------


def _render_all(
    proj: Project,
    plan: Plan,
    koubo,
    history: list[float] | None,
    template: str | None = None,
) -> None:
    out = proj.outputs
    paths = [
        markdown.write(out / "事業計画書.md", markdown.plan_markdown(plan, koubo)),
        markdown.write(out / "審査スコアレポート.md", report.score_report(plan, history)),
        docx_form.build_yoshiki2(plan, koubo, out / "様式2_経営計画書兼補助事業計画書1.docx"),
        docx_form.build_yoshiki3(plan, koubo, out / "様式3_補助事業計画書2.docx"),
        xlsx_expense.build(plan, koubo, out / "経費明細・資金調達.xlsx"),
    ]
    if template:
        paths.append(
            docx_form.fill_template(Path(template), plan, koubo, out / "様式2_公式様式版.docx")
        )
    for p in paths:
        _ok(f"{p.name} → {p}")


def _summary(plan: Plan, koubo) -> int:
    total = sum(e.resolved_amount() for e in plan.expenses)
    grant = compliance.grant_amount(
        total, koubo.rate(plan.frame), koubo.limit_yen(plan.frame, plan.specials)
    )
    _say("")
    _say(f"  補助対象経費 {total:,}円 → 補助金申請額 {grant:,}円")
    if plan.score:
        _say(f"  自己採点 {plan.score.total}点（{plan.score.verdict}）")

    must = [f for f in plan.quality if f.severity == "must"]
    if must:
        _err(f"文章品質: 要修正{len(must)}件 — 審査で減点されます")
        for f in must[:5]:
            _say(f"{C_DIM}    {f.section_heading}: {f.message}{C_RESET}")
        if len(must) > 5:
            _say(f"{C_DIM}    …ほか{len(must) - 5}件（審査スコアレポート参照）{C_RESET}")
    elif plan.quality:
        _warn(f"文章品質: 推奨事項{len(plan.quality)}件")
    elif plan.all_sections():
        _ok("文章品質: 指摘なし")

    if plan.compliance:
        n_err, n_warn = len(plan.compliance.errors), len(plan.compliance.warnings)
        if n_err:
            _err(f"公募要領チェック: エラー{n_err}件 — 提出前に必ず解消してください")
            return 1
        if n_warn:
            _warn(f"公募要領チェック: 警告{n_warn}件（審査スコアレポート参照）")
        else:
            _ok("公募要領チェック: 指摘なし")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="jizokuka",
        description="小規模事業者持続化補助金 事業計画書 自動作成システム",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""典型的な流れ:
  jizokuka template hearing.yaml            # 雛形を出す（顧客に記入してもらう）
  jizokuka new tanaka --from hearing.yaml   # 案件として取り込む
  jizokuka ask tanaka                       # 追加質問シートを生成（★ここで手間が激減）
  （顧客の回答を answers.yaml に転記）
  jizokuka build tanaka                     # 生成・採点・リライト・チェック
  jizokuka check tanaka                     # 手直し後の再チェック

まず何が出るか見たいとき（APIキー不要）:
  jizokuka sample -o ./sample_outputs       # 模範解答から Word/Excel を生成
""",
    )
    p.add_argument("--version", action="version", version=f"jizokuka {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    def _common(sp):
        sp.add_argument("--dry-run", action="store_true", help="LLM を呼ばずに動作確認する")
        sp.add_argument("--no-cache", action="store_true", help="LLM 応答キャッシュを使わない")
        return sp

    sp = sub.add_parser("template", help="ヒアリングシートの雛形を出力する")
    sp.add_argument("output", nargs="?", default="hearing_sheet.yaml")
    sp.set_defaults(func=cmd_template)

    sp = sub.add_parser("new", help="ヒアリングシートを案件として取り込む")
    sp.add_argument("project_id")
    sp.add_argument("--from", dest="source", required=True, help="yaml/json/csv/xlsx")
    sp.add_argument("--frame", help="申請枠（通常枠・賃金引上げ枠 等）")
    sp.add_argument("--koubo", help="公募要領ID（既定: r17）")
    sp.set_defaults(func=cmd_new)

    sp = _common(sub.add_parser("ask", help="ギャップ分析して追加質問シートを出す"))
    sp.add_argument("project_id")
    sp.add_argument("--max-questions", type=int, default=8, help="質問数の上限（既定8）")
    sp.set_defaults(func=cmd_ask)

    sp = _common(sub.add_parser("build", help="事業計画書を生成する"))
    sp.add_argument("project_id")
    sp.add_argument("--max-revisions", type=int, default=None, help="リライト上限回数")
    sp.add_argument("--target-score", type=float, default=None, help="目標スコア（既定80）")
    sp.add_argument("--regap", action="store_true", help="ギャップ分析をやり直す")
    sp.set_defaults(func=cmd_build)

    sp = sub.add_parser("check", help="公募要領準拠チェックを再実行する")
    sp.add_argument("project_id")
    sp.set_defaults(func=cmd_check)

    sp = sub.add_parser("render", help="生成済みの計画から成果物を出し直す")
    sp.add_argument("project_id")
    sp.add_argument("--template", help="公式様式の .docx に流し込む")
    sp.set_defaults(func=cmd_render)

    sp = sub.add_parser("sample", help="模範解答から成果物一式を生成する（LLM 不要）")
    sp.add_argument("--output", "-o", default="./sample_outputs", help="出力先ディレクトリ")
    sp.set_defaults(func=cmd_sample)

    sp = sub.add_parser("list", help="案件一覧")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("koubo", help="公募要領ナレッジを表示・突合する")
    sp.add_argument("--koubo", default="r17")
    sp.add_argument("--verify", action="store_true", help="突合チェックリストを出力する")
    sp.set_defaults(func=cmd_koubo)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except LLMError as e:
        _err(str(e))
        return 2
    except (FileNotFoundError, ValueError, KeyError) as e:
        _err(str(e).strip('"'))
        return 1
    except KeyboardInterrupt:
        _err("中断しました")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
