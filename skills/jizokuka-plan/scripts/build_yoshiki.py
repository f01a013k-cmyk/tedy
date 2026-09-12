#!/usr/bin/env python3
"""下書き（draft.yaml）から提出用の様式2・様式3と経費明細Excelを作る。

公式の様式ファイルが手元にある場合は fill_form.py で流し込むほうがよい。
このスクリプトは様式ファイルが無いときに、様式に沿った体裁で一式を起こす。

Excel の補助金額は固定値ではなく MIN/ROUNDDOWN の数式で入れる。
支援員が経費を直したときに自動で再計算されないと、金額の食い違いが起きるためである。

使い方:
    python3 build_yoshiki.py draft.yaml -o ./out
"""

from __future__ import annotations

import argparse
import math
import sys
from fractions import Fraction
from pathlib import Path

import yaml
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

REF_DIR = Path(__file__).resolve().parent.parent / "references"
JP_GOTHIC, JP_MINCHO = "Yu Gothic", "Yu Mincho"
YEN = '#,##0"円"'
THIN = Side(style="thin", color="999999")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


# ---------------------------------------------------------------------------
# 共通
# ---------------------------------------------------------------------------


def load(path: Path) -> dict:
    if not path.exists():
        sys.exit(f"ファイルが見つかりません: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def amount_of(e: dict) -> int:
    if e.get("amount") is not None:
        return int(e["amount"])
    if e.get("unit_price") is not None:
        return int(e["unit_price"]) * int(e.get("quantity") or 1)
    return 0


class Money:
    """補助率は分数で扱う。float だと 600,000 × 2/3 が 400,020 円になる."""

    def __init__(self, draft: dict, koubo: dict) -> None:
        frame = draft.get("frame") or "通常枠"
        if frame not in koubo["frames"]:
            sys.exit(f"未知の申請枠『{frame}』。利用可能: {'、'.join(koubo['frames'])}")
        spec = koubo["frames"][frame]
        is_deficit = (draft.get("company") or {}).get("is_deficit")
        raw = spec["rate_if_deficit"] if (is_deficit and "rate_if_deficit" in spec) else spec["rate"]

        self.frame = frame
        self.rate = Fraction(str(raw))
        self.rate_label = spec.get("rate_label", str(raw))
        self.specials = draft.get("specials") or []
        self.limit = int(spec["limit_yen"]) + sum(
            int((koubo.get("specials") or {}).get(s, {}).get("add_limit_yen", 0))
            for s in self.specials
        )
        self.total = sum(amount_of(e) for e in (draft.get("expenses") or []))
        self.grant = min(int(math.floor(self.total * self.rate)), self.limit)

    @property
    def own_funds(self) -> int:
        return self.total - self.grant


def set_font(run, name: str = JP_GOTHIC, size: float = 10, bold: bool = False) -> None:
    run.font.name = name
    run.font.size = Pt(size)
    run.bold = bold
    # 日本語は eastAsia を指定しないとフォントが効かない
    run._element.rPr.rFonts.set(qn("w:eastAsia"), name)


def new_doc() -> Document:
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
    for attr in ("top_margin", "bottom_margin", "left_margin", "right_margin"):
        setattr(sec, attr, Cm(2.0))
    style = doc.styles["Normal"]
    style.font.name = JP_GOTHIC
    style.font.size = Pt(10)
    style.element.rPr.rFonts.set(qn("w:eastAsia"), JP_GOTHIC)
    return doc


def heading(doc: Document, text: str, size: float = 11) -> None:
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(10)
    p.paragraph_format.space_after = Pt(4)
    set_font(p.add_run(text), size=size, bold=True)


def body_text(doc: Document, text: str) -> None:
    for line in str(text).strip().split("\n"):
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(2)
        p.paragraph_format.line_spacing = 1.15
        set_font(p.add_run(line), name=JP_MINCHO)


def warn_footer(doc: Document, draft: dict, koubo: dict) -> None:
    """未確認の推定値が残っていれば、提出前に気づけるよう赤字で残す."""
    todos = [k for k, v in (draft.get("sections") or {}).items() if "〈要確認" in str(v)]
    msgs = []
    if todos:
        msgs.append(f"本文に未確認の推定値が残っています（{len(todos)}項目）。")
    if not koubo["meta"].get("verified"):
        msgs.append("公募要領ナレッジが未検証です。金額・様式を実物と照合してください。")
    if not msgs:
        return
    doc.add_paragraph()
    run = doc.add_paragraph().add_run("【提出前に削除】" + " ".join(msgs))
    set_font(run, size=9, bold=True)
    run.font.color.rgb = RGBColor(0xCC, 0x00, 0x00)


# ---------------------------------------------------------------------------
# 様式2
# ---------------------------------------------------------------------------


def build_yoshiki2(draft: dict, koubo: dict, money: Money, out: Path) -> Path:
    doc = new_doc()
    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_font(title.add_run("経営計画書兼補助事業計画書①"), size=14, bold=True)
    sub = doc.add_paragraph()
    sub.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    set_font(sub.add_run("（様式2）"), size=9)

    company = draft.get("company") or {}
    rows = [("補助事業者名", company.get("name") or draft.get("project_id", ""))]
    if company.get("representative"):
        rows.append(("代表者名", company["representative"]))
    rows.append(("申請枠", money.frame + (f"（{'・'.join(money.specials)}）" if money.specials else "")))

    t = doc.add_table(rows=0, cols=2)
    t.style = "Table Grid"
    for k, v in rows:
        cells = t.add_row().cells
        cells[0].width, cells[1].width = Cm(4.5), Cm(12.5)
        set_font(cells[0].paragraphs[0].add_run(k), size=9, bold=True)
        set_font(cells[1].paragraphs[0].add_run(str(v)), size=9)

    form = koubo["form_yoshiki2"]
    sections = draft.get("sections") or {}
    for label, specs in (("【経営計画】", form["sections"]),
                         ("【補助事業計画】", form["project_sections"])):
        doc.add_paragraph() if label == "【経営計画】" else doc.add_page_break()
        heading(doc, label, size=12)
        for spec in specs:
            text = sections.get(spec["key"])
            if not text or not str(text).strip():
                continue
            heading(doc, f"{spec['number']}．{spec['heading']}")
            body_text(doc, text)

    warn_footer(doc, draft, koubo)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out)
    return out


# ---------------------------------------------------------------------------
# 様式3
# ---------------------------------------------------------------------------


def build_yoshiki3(draft: dict, koubo: dict, money: Money, out: Path) -> Path:
    doc = new_doc()
    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_font(title.add_run("補助事業計画書②"), size=14, bold=True)
    sub = doc.add_paragraph()
    sub.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    set_font(sub.add_run("（様式3）"), size=9)

    heading(doc, "1．経費明細表", size=12)
    set_font(doc.add_paragraph().add_run("（単位: 円・税抜）"), size=8)

    headers = ["経費区分", "内容・必要理由", "単価", "数量", "補助対象経費", "積算根拠"]
    widths = [Cm(2.4), Cm(4.6), Cm(1.8), Cm(1.1), Cm(2.2), Cm(4.9)]
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Table Grid"
    for cell, h, w in zip(t.rows[0].cells, headers, widths):
        cell.width = w
        set_font(cell.paragraphs[0].add_run(h), size=8, bold=True)

    for e in draft.get("expenses") or []:
        content = str(e.get("item") or "")
        if e.get("linked_activity"):
            content += f"\n（対応する取組: {e['linked_activity']}）"
        values = [
            e.get("category") or "", content,
            f"{int(e['unit_price']):,}" if e.get("unit_price") else "",
            str(e.get("quantity") or ""), f"{amount_of(e):,}", e.get("basis") or "",
        ]
        for cell, v, w in zip(t.add_row().cells, values, widths):
            cell.width = w
            set_font(cell.paragraphs[0].add_run(v), size=8)

    cells = t.add_row().cells
    set_font(cells[0].paragraphs[0].add_run("合計"), size=8, bold=True)
    set_font(cells[4].paragraphs[0].add_run(f"{money.total:,}"), size=8, bold=True)

    p = doc.add_paragraph()
    set_font(p.add_run(
        f"補助対象経費合計 {money.total:,}円 × 補助率 {money.rate_label}"
        f" → 補助金交付申請額 {money.grant:,}円（補助上限 {money.limit:,}円）"), size=9, bold=True)

    doc.add_paragraph()
    heading(doc, "2．資金調達方法", size=12)
    funding = draft.get("funding") or {"補助金": money.grant, "自己資金": money.own_funds}
    ft = doc.add_table(rows=1, cols=2)
    ft.style = "Table Grid"
    for cell, h in zip(ft.rows[0].cells, ["区分", "金額（円）"]):
        set_font(cell.paragraphs[0].add_run(h), size=9, bold=True)
    for k, v in funding.items():
        cells = ft.add_row().cells
        cells[0].width, cells[1].width = Cm(10), Cm(5)
        set_font(cells[0].paragraphs[0].add_run(str(k)), size=9)
        set_font(cells[1].paragraphs[0].add_run(f"{int(v):,}"), size=9)
    cells = ft.add_row().cells
    set_font(cells[0].paragraphs[0].add_run("合計"), size=9, bold=True)
    set_font(cells[1].paragraphs[0].add_run(f"{sum(int(v) for v in funding.values()):,}"),
             size=9, bold=True)

    warn_footer(doc, draft, koubo)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out)
    return out


# ---------------------------------------------------------------------------
# 経費明細 Excel
# ---------------------------------------------------------------------------


def build_xlsx(draft: dict, koubo: dict, money: Money, out: Path) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "経費明細表"

    headers = ["経費区分", "内容・必要理由", "単価(税抜)", "数量", "補助対象経費",
               "積算根拠", "対応する取組"]
    for i, (h, w) in enumerate(zip(headers, [16, 32, 12, 7, 14, 34, 26]), start=1):
        ws.cell(row=1, column=i, value=h)
        ws.column_dimensions[get_column_letter(i)].width = w
    for c in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=c)
        cell.font = Font(bold=True, size=10)
        cell.fill = PatternFill("solid", fgColor="DCE6F1")
        cell.border = BORDER
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    valid = {c["name"] for c in koubo["expense_categories"]}
    row = 2
    for e in draft.get("expenses") or []:
        ws.cell(row=row, column=1, value=e.get("category") or "")
        ws.cell(row=row, column=2, value=e.get("item") or "")
        if e.get("unit_price"):
            ws.cell(row=row, column=3, value=int(e["unit_price"])).number_format = YEN
        if e.get("quantity"):
            ws.cell(row=row, column=4, value=int(e["quantity"]))
        # 単価×数量があれば数式にし、支援員の編集に追随させる
        if e.get("unit_price") and e.get("quantity"):
            ws.cell(row=row, column=5, value=f"=C{row}*D{row}")
        else:
            ws.cell(row=row, column=5, value=amount_of(e))
        ws.cell(row=row, column=5).number_format = YEN
        ws.cell(row=row, column=6, value=e.get("basis") or "")
        ws.cell(row=row, column=7, value=e.get("linked_activity") or "")
        for c in range(1, len(headers) + 1):
            ws.cell(row=row, column=c).border = BORDER
            ws.cell(row=row, column=c).alignment = Alignment(vertical="top", wrap_text=True)
        if e.get("category") not in valid:
            ws.cell(row=row, column=1).fill = PatternFill("solid", fgColor="FFE5E5")
        row += 1

    ws.cell(row=row, column=1, value="合計").font = Font(bold=True)
    total_cell = ws.cell(row=row, column=5, value=f"=SUM(E2:E{row - 1})")
    total_cell.font, total_cell.number_format = Font(bold=True), YEN
    for c in range(1, len(headers) + 1):
        ws.cell(row=row, column=c).border = BORDER
    total_ref = f"'{ws.title}'!E{row}"

    fs = wb.create_sheet("資金調達方法")
    fs.column_dimensions["A"].width = 26
    fs.column_dimensions["B"].width = 18
    fs.column_dimensions["C"].width = 44
    rows = [
        ("申請枠", money.frame, ""),
        ("上乗せ特例", "・".join(money.specials) or "なし", ""),
        ("補助率", money.rate_label, ""),
        ("補助上限額", money.limit, "枠＋特例の合計"),
        ("補助対象経費 合計", f"={total_ref}", "経費明細表と連動"),
    ]
    for i, (k, v, note) in enumerate(rows, start=1):
        fs.cell(row=i, column=1, value=k).font = Font(bold=True)
        cell = fs.cell(row=i, column=2, value=v)
        if isinstance(v, int):
            cell.number_format = YEN
        fs.cell(row=i, column=3, value=note).font = Font(size=9, color="777777")

    fs.cell(row=7, column=1, value="補助金交付申請額").font = Font(bold=True)
    grant = fs.cell(row=7, column=2,
                    value=f"=MIN(ROUNDDOWN(B5*{money.rate.numerator}/{money.rate.denominator},0),B4)")
    grant.number_format, grant.font = YEN, Font(bold=True)
    fs.cell(row=7, column=3,
            value="経費または上限を変えると自動で再計算されます").font = Font(size=9, color="777777")

    fs.cell(row=9, column=1, value="【資金調達方法】").font = Font(bold=True)
    for c, h in ((1, "区分"), (2, "金額")):
        cell = fs.cell(row=10, column=c, value=h)
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="DCE6F1")
    fs.cell(row=11, column=1, value="補助金")
    fs.cell(row=11, column=2, value="=B7").number_format = YEN
    fs.cell(row=12, column=1, value="自己資金")
    fs.cell(row=12, column=2, value="=B5-B7").number_format = YEN
    fs.cell(row=13, column=1, value="合計").font = Font(bold=True)
    tot = fs.cell(row=13, column=2, value="=SUM(B11:B12)")
    tot.number_format, tot.font = YEN, Font(bold=True)
    fs.cell(row=14, column=3,
            value="※合計は補助対象経費合計(B5)と一致する必要があります").font = Font(size=9, color="CC0000")
    for r in range(10, 14):
        for c in (1, 2):
            fs.cell(row=r, column=c).border = BORDER

    rs = wb.create_sheet("補助対象経費リファレンス")
    for i, (h, w) in enumerate(zip(["No", "経費区分", "例", "注意事項"], [6, 18, 40, 52]), start=1):
        rs.cell(row=1, column=i, value=h).font = Font(bold=True)
        rs.column_dimensions[get_column_letter(i)].width = w
    for r, c in enumerate(koubo["expense_categories"], start=2):
        rs.cell(row=r, column=1, value=c["code"])
        rs.cell(row=r, column=2, value=c["name"])
        rs.cell(row=r, column=3, value="、".join(c["examples"]))
        rs.cell(row=r, column=4, value=c.get("caution") or "")
        for col in range(1, 5):
            rs.cell(row=r, column=col).alignment = Alignment(vertical="top", wrap_text=True)
            rs.cell(row=r, column=col).border = BORDER

    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="下書きから様式2・様式3・経費明細Excelを作る")
    ap.add_argument("draft", help="下書きの YAML")
    ap.add_argument("--out", "-o", default="./out", help="出力先ディレクトリ")
    ap.add_argument("--koubo", default="r17", help="公募要領ID（既定 r17）")
    args = ap.parse_args()

    draft = load(Path(args.draft))
    koubo = load(REF_DIR / f"koubo_{args.koubo}.yaml")
    money = Money(draft, koubo)
    out_dir = Path(args.out)

    paths = [
        build_yoshiki2(draft, koubo, money, out_dir / "様式2_経営計画書兼補助事業計画書1.docx"),
        build_yoshiki3(draft, koubo, money, out_dir / "様式3_補助事業計画書2.docx"),
        build_xlsx(draft, koubo, money, out_dir / "経費明細・資金調達.xlsx"),
    ]
    for p in paths:
        print(p)
    print(f"  補助対象経費 {money.total:,}円 → 補助金申請額 {money.grant:,}円"
          f"（上限 {money.limit:,}円）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
