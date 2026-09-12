"""Word（.docx）出力 — 様式2・様式3.

公式の様式ファイルが手元にある場合は `fill_template()` で本文を流し込める。
無い場合は `build_yoshiki2()` が様式に沿った体裁の文書を生成する。
"""

from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from ..knowledge import Koubo
from ..models import Plan
from ..pipeline.compliance import grant_amount

JP_FONT = "Yu Gothic"
JP_MINCHO = "Yu Mincho"


def _set_jp_font(run, name: str = JP_FONT, size: int = 10, bold: bool = False) -> None:
    run.font.name = name
    run.font.size = Pt(size)
    run.bold = bold
    # 日本語は eastAsia を指定しないとフォントが効かない
    run._element.rPr.rFonts.set(qn("w:eastAsia"), name)


def _new_document() -> Document:
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)  # A4縦
    for attr in ("top_margin", "bottom_margin"):
        setattr(sec, attr, Cm(2.0))
    for attr in ("left_margin", "right_margin"):
        setattr(sec, attr, Cm(2.0))

    style = doc.styles["Normal"]
    style.font.name = JP_FONT
    style.font.size = Pt(10)
    style.element.rPr.rFonts.set(qn("w:eastAsia"), JP_FONT)
    return doc


def _heading(doc: Document, text: str, size: int = 11) -> None:
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(10)
    p.paragraph_format.space_after = Pt(4)
    _set_jp_font(p.add_run(text), size=size, bold=True)


def _body(doc: Document, text: str, size: int = 10) -> None:
    for block in text.split("\n"):
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(2)
        p.paragraph_format.line_spacing = 1.15
        _set_jp_font(p.add_run(block), name=JP_MINCHO, size=size)


def _kv_table(doc: Document, rows: list[tuple[str, str]]) -> None:
    t = doc.add_table(rows=0, cols=2)
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for k, v in rows:
        cells = t.add_row().cells
        cells[0].width, cells[1].width = Cm(4.5), Cm(12.5)
        _set_jp_font(cells[0].paragraphs[0].add_run(k), size=9, bold=True)
        _set_jp_font(cells[1].paragraphs[0].add_run(v), size=9)


def build_yoshiki2(plan: Plan, koubo: Koubo, out_path: Path) -> Path:
    """様式2「経営計画書兼補助事業計画書①」."""
    doc = _new_document()

    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _set_jp_font(title.add_run("経営計画書兼補助事業計画書①"), size=14, bold=True)

    sub = doc.add_paragraph()
    sub.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    _set_jp_font(sub.add_run("（様式2）"), size=9)

    rows = [("補助事業者名", plan.company_name or plan.project_id)]
    if plan.representative:
        rows.append(("代表者名", plan.representative))
    rows.append(
        ("申請枠", plan.frame + (f"（{'・'.join(plan.specials)}）" if plan.specials else ""))
    )
    _kv_table(doc, rows)

    doc.add_paragraph()
    _heading(doc, "【経営計画】", size=12)
    for spec in koubo.sections:
        sec = plan.section(spec["key"])
        if not sec:
            continue
        _heading(doc, f"{spec['number']}．{sec.heading}")
        _body(doc, sec.body)

    doc.add_page_break()
    _heading(doc, "【補助事業計画】", size=12)
    for spec in koubo.project_sections:
        sec = plan.section(spec["key"])
        if not sec:
            continue
        _heading(doc, f"{spec['number']}．{sec.heading}")
        _body(doc, sec.body)

    _footer_note(doc, plan, koubo)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out_path)
    return out_path


def build_yoshiki3(plan: Plan, koubo: Koubo, out_path: Path) -> Path:
    """様式3「補助事業計画書②」— 経費明細表・資金調達方法."""
    doc = _new_document()

    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _set_jp_font(title.add_run("補助事業計画書②"), size=14, bold=True)
    sub = doc.add_paragraph()
    sub.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    _set_jp_font(sub.add_run("（様式3）"), size=9)

    total = sum(e.resolved_amount() for e in plan.expenses)
    rate = koubo.rate(plan.frame)
    limit = koubo.limit_yen(plan.frame, plan.specials)
    grant = grant_amount(total, rate, limit)

    _heading(doc, "1．経費明細表", size=12)
    _set_jp_font(doc.add_paragraph().add_run("（単位: 円・税抜）"), size=8)

    headers = ["経費区分", "内容・必要理由", "単価", "数量", "補助対象経費", "積算根拠"]
    widths = [Cm(2.4), Cm(4.6), Cm(1.8), Cm(1.1), Cm(2.2), Cm(4.9)]
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Table Grid"
    for cell, h, w in zip(t.rows[0].cells, headers, widths):
        cell.width = w
        _set_jp_font(cell.paragraphs[0].add_run(h), size=8, bold=True)

    for e in plan.expenses:
        cells = t.add_row().cells
        content = e.item or ""
        if e.linked_activity:
            content += f"\n（対応する取組: {e.linked_activity}）"
        values = [
            e.category or "",
            content,
            f"{e.unit_price:,}" if e.unit_price else "",
            str(e.quantity or ""),
            f"{e.resolved_amount():,}",
            e.basis or "",
        ]
        for cell, v, w in zip(cells, values, widths):
            cell.width = w
            _set_jp_font(cell.paragraphs[0].add_run(v), size=8)

    cells = t.add_row().cells
    _set_jp_font(cells[0].paragraphs[0].add_run("合計"), size=8, bold=True)
    _set_jp_font(cells[4].paragraphs[0].add_run(f"{total:,}"), size=8, bold=True)

    p = doc.add_paragraph()
    _set_jp_font(
        p.add_run(
            f"補助対象経費合計 {total:,}円 × 補助率 {koubo.rate_label(plan.frame)}"
            f" → 補助金交付申請額 {grant:,}円（補助上限 {limit:,}円）"
        ),
        size=9,
        bold=True,
    )

    doc.add_paragraph()
    _heading(doc, "2．資金調達方法", size=12)
    ft = doc.add_table(rows=1, cols=2)
    ft.style = "Table Grid"
    for cell, h in zip(ft.rows[0].cells, ["区分", "金額（円）"]):
        _set_jp_font(cell.paragraphs[0].add_run(h), size=9, bold=True)
    for k, v in plan.funding.items():
        cells = ft.add_row().cells
        cells[0].width, cells[1].width = Cm(10), Cm(5)
        _set_jp_font(cells[0].paragraphs[0].add_run(k), size=9)
        _set_jp_font(cells[1].paragraphs[0].add_run(f"{v:,}"), size=9)
    cells = ft.add_row().cells
    _set_jp_font(cells[0].paragraphs[0].add_run("合計"), size=9, bold=True)
    _set_jp_font(cells[1].paragraphs[0].add_run(f"{sum(plan.funding.values()):,}"), size=9, bold=True)

    _footer_note(doc, plan, koubo)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out_path)
    return out_path


def _footer_note(doc: Document, plan: Plan, koubo: Koubo) -> None:
    """未確認事項が残っている場合、提出前に気づけるよう赤字で警告を残す."""
    todos = [s.heading for s in plan.all_sections() if "〈要確認" in s.body]
    if not (todos or not koubo.verified):
        return
    doc.add_paragraph()
    p = doc.add_paragraph()
    msgs = []
    if todos:
        msgs.append(f"本文に未確認の推定値が残っています（{'、'.join(sorted(set(todos)))}）。")
    if not koubo.verified:
        msgs.append("公募要領ナレッジが未検証です。金額・様式を必ず実物と照合してください。")
    run = p.add_run("【提出前に削除】" + " ".join(msgs))
    _set_jp_font(run, size=9, bold=True)
    run.font.color.rgb = RGBColor(0xCC, 0x00, 0x00)


def fill_template(template: Path, plan: Plan, koubo: Koubo, out_path: Path) -> Path:
    """公式様式の .docx にプレースホルダ経由で流し込む.

    テンプレート側に `{{company_overview}}` のような記法を置いておくと、
    対応するセクション本文に置換する。書式はテンプレート側が保持される。
    """
    doc = Document(template)
    mapping = {f"{{{{{s.key}}}}}": s.body for s in plan.all_sections()}
    total = sum(e.resolved_amount() for e in plan.expenses)
    mapping["{{total_expense}}"] = f"{total:,}"
    mapping["{{grant_amount}}"] = (
        f"{grant_amount(total, koubo.rate(plan.frame), koubo.limit_yen(plan.frame, plan.specials)):,}"
    )

    def _replace_in(paragraphs) -> None:
        for p in paragraphs:
            for key, value in mapping.items():
                if key in p.text:
                    # run をまたぐ置換に対応するため、段落テキストを1 run に畳む
                    for r in list(p.runs)[1:]:
                        r._element.getparent().remove(r._element)
                    if p.runs:
                        p.runs[0].text = p.text.replace(key, value)

    _replace_in(doc.paragraphs)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                _replace_in(cell.paragraphs)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out_path)
    return out_path
