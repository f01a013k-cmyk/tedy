#!/usr/bin/env python3
"""既存の Word 様式に本文と表を流し込む.

支援機関は自前の事業計画書フォーマットを持っている。こちらの体裁で出力し直すと
使ってもらえないため、**相手の .docx をそのまま開いて中身だけ埋める**方式をとる。
書式・フォント・表の罫線はテンプレート側が保持する。

埋め方:
  見出し段落（「１－１－１　設立経緯…」）を見つけ、その直後に続く空段落
  （＝記入欄）を本文で置き換える。足りなければ段落を追加し、余れば削除する。
  ①②③ のような箇条書きプレースホルダも記入欄として扱う。
  表は、行見出しと列見出しの組み合わせでセルを特定して埋める。
"""

from __future__ import annotations

import copy
import re
import unicodedata
import sys
from pathlib import Path
from typing import Any

from docx import Document
from docx.oxml.ns import qn
from docx.shared import Pt
from docx.table import Table
from docx.text.paragraph import Paragraph

JP_FONT = "Yu Mincho"

# ①②③ などの箇条書きプレースホルダ
_BULLET_PLACEHOLDER = re.compile(r"^[①-⑳\d]{1,2}[\.．、]?$")
# 「例：代表　氏名　××××」のような記入例。記入欄と同じく本文で置き換える
_EXAMPLE_LINE = re.compile(r"^例[：:）)]|[×✕]{2,}|[●○]{2,}|〇名")
# 章番号の区切りに使われうるハイフン類。長音記号「ー」は**含めない**
# （含めると「顧客ニーズ」が「顧客ニ-ズ」になり、見出し語の照合が壊れる）
_DASHES = "-‐‑‒–—―−"
# 章番号のあとには必ず空白が入る。ここを省略可にすると
# 「2026年度 売上高…」「1,996,800円 × 54%」「4月 冷凍機の発注」といった
# 本文行まで見出しとして拾ってしまい、流し込み先を取り違える。
_HEADING_NUMBER = re.compile(
    rf"^([0-9]{{1,2}}(?:[{_DASHES}][0-9]{{1,2}}){{0,3}})[\s　]+(\S.*)$"
)
# 丸数字は NFKC で「①」→「1」になるため、正規化前に弾く
_LIST_MARKER = re.compile(r"^\s*[①-⑳・▪●○※]")


def normalize(text: str) -> str:
    """全角英数・全角空白を半角にそろえる（本文の文字は変えない）."""
    return unicodedata.normalize("NFKC", text or "").strip()


def heading_key(text: str) -> tuple[str, str] | None:
    """見出し段落から (章番号, 見出し語) を取り出す. 見出しでなければ None."""
    if _LIST_MARKER.match(text or ""):
        return None
    m = _HEADING_NUMBER.match(normalize(text))
    if not m:
        return None
    number = re.sub(rf"[{_DASHES}]", "-", m.group(1))
    title = m.group(2)
    if not title:
        return None
    return number, title


def _iter_body(doc: Document) -> list[tuple[str, Any]]:
    """本文の段落と表を、文書に現れる順で返す."""
    out: list[tuple[str, Any]] = []
    for child in doc.element.body.iterchildren():
        if child.tag.endswith("}p"):
            out.append(("p", Paragraph(child, doc)))
        elif child.tag.endswith("}tbl"):
            out.append(("t", Table(child, doc)))
    return out


def _is_fillable(para: Paragraph) -> bool:
    """記入欄として潰してよい段落か.

    空欄、①②③ のプレースホルダ、および「例：…」の記入例が対象。
    「〜を挙げてください」のような指示文は様式の一部なので残す。
    """
    t = para.text.strip()
    if not t:
        return True
    return bool(_BULLET_PLACEHOLDER.match(t)) or bool(_EXAMPLE_LINE.search(t))


def _set_text(para: Paragraph, text: str, size: float = 10.5) -> None:
    """段落の中身を差し替える。改行は Word の改行として入れる.

    改行文字をそのまま run に入れても Word では折り返されないため、
    行ごとに run を分けて break を挿入する。
    """
    for r in list(para.runs):
        r._element.getparent().remove(r._element)

    lines = str(text).split("\n")
    for i, line in enumerate(lines):
        run = para.add_run(line)
        run.font.name = JP_FONT
        run.font.size = Pt(size)
        if run._element.rPr is not None and run._element.rPr.rFonts is not None:
            run._element.rPr.rFonts.set(qn("w:eastAsia"), JP_FONT)
        if i < len(lines) - 1:
            run.add_break()


def _clone_after(para: Paragraph, text: str) -> Paragraph:
    """para と同じ書式の段落を直後に作る."""
    new_el = copy.deepcopy(para._p)
    para._p.addnext(new_el)
    new_para = Paragraph(new_el, para._parent)
    _set_text(new_para, text)
    return new_para


def _fill_zone(zone: list[Paragraph], lines: list[str]) -> None:
    """記入欄の段落群を本文行で置き換える."""
    if not zone:
        return
    for i, line in enumerate(lines):
        if i < len(zone):
            _set_text(zone[i], line)
        else:
            zone.append(_clone_after(zone[-1], line))
    # 余った記入欄は削除する（空行が残ると間延びして見えるため）
    for extra in zone[len(lines):]:
        extra._p.getparent().remove(extra._p)


def _as_lines(content: str) -> list[str]:
    return [ln.rstrip() for ln in str(content).strip().split("\n")]


def heading_para_of(body: list[tuple[str, Any]], idx: int) -> Paragraph:
    """書式の見本にする段落。見出しそのものを使う（本文と同じ Normal スタイル）."""
    return body[idx][1]


def fill(
    template: str | Path,
    out_path: str | Path,
    blocks: dict[str, str],
    tables: dict[int, dict[str, dict[str, str]]] | None = None,
    strict: bool = True,
) -> tuple[Path, list[str]]:
    """テンプレートを埋めて保存する.

    blocks のキーは章番号（"1-1-1"）。同じ番号が複数ある様式のために
    "2-2|顧客ニーズ" のように見出し語で絞り込むこともできる。

    戻り値は (出力パス, 埋まらなかったキーの一覧)。
    """
    doc = Document(str(template))
    body = _iter_body(doc)
    used: set[str] = set()

    # 見出しの位置を拾う
    headings: list[tuple[int, str, str]] = []
    for i, (kind, obj) in enumerate(body):
        if kind != "p":
            continue
        key = heading_key(obj.text)
        if key:
            headings.append((i, key[0], key[1]))

    for pos, (idx, number, title) in enumerate(headings):
        # 章番号が重複する様式のために "2-2|顧客ニーズ" の形でも引けるようにする
        matched_key = next(
            (c for c in (f"{number}|{title[:6]}", number) if c in blocks), None
        )
        if matched_key is None:
            # 見出し語の一部でも一致すれば拾う（様式の文言が長いため）
            for k in blocks:
                if "|" not in k:
                    continue
                num, frag = k.split("|", 1)
                if num == number and frag in title:
                    matched_key = k
                    break
        if matched_key is None:
            continue
        used.add(matched_key)
        content = blocks[matched_key]

        # この見出しから次の見出しまでの間にある記入欄を集める。
        # 途中に表がある様式では、本文は表より前に置く（「下表のとおり」と
        # 書いた段落が表の後ろに回ってしまうため）。
        end = headings[pos + 1][0] if pos + 1 < len(headings) else len(body)
        before: list[Paragraph] = []
        after: list[Paragraph] = []
        first_table: Table | None = None
        for kind, obj in body[idx + 1 : end]:
            if kind == "t":
                if first_table is None:
                    first_table = obj
            elif _is_fillable(obj):
                (after if first_table is not None else before).append(obj)

        # 記入欄が足りない様式のために段落を作る。
        # 様式によっては見出しの直後にいきなり表が来て、空欄が1つも無いことがある。
        # そのまま書き込み先なしにすると本文が黙って消えるため、必ず場所を作る。
        if not before:
            seed_para = after[0] if after else heading_para_of(body, idx)
            seed = copy.deepcopy(seed_para._p)
            if first_table is not None:
                # 「下表のとおりである」と書いた本文が表の後ろに回らないようにする
                first_table._tbl.addprevious(seed)
            else:
                body[idx][1]._p.addnext(seed)
            before = [Paragraph(seed, seed_para._parent)]

        zone = before
        _fill_zone(zone, _as_lines(content))
        # 表の後ろに残った空欄は削除する（間延びして見えるため）
        if zone is before:
            for extra_para in after:
                extra_para._p.getparent().remove(extra_para._p)

    # 表を埋める
    all_tables = doc.tables
    for t_index, cell_map in (tables or {}).items():
        if t_index >= len(all_tables):
            continue
        _fill_table(all_tables[t_index], cell_map)

    missing = [k for k in blocks if k not in used]
    if missing and strict:
        raise ValueError(
            "様式に対応する見出しが見つかりませんでした: " + ", ".join(missing)
        )

    # 書き込んだ内容が本当に文書に入ったかを確認する。
    # 記入欄の作り方を誤ると本文が黙って消えるため、ここで必ず検知する。
    written = "".join(p.text for p in doc.paragraphs)
    dropped = [
        k
        for k in blocks
        if k in used and _as_lines(blocks[k])[0][:20] not in written
    ]
    if dropped:
        raise ValueError("本文を配置できませんでした: " + ", ".join(dropped))

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out)
    return out, missing


def _fill_table(table: Table, cell_map: dict[str, Any]) -> None:
    """{行見出し: {列見出し: 値}} で表を埋める.

    行見出しは1列目、列見出しは1行目のテキストで照合する。
    キー "__extra_rows__" には [(行見出し, {列見出し: 値}), ...] を渡せる。
    様式に用意された空行や「他は自由に項目を挙げてください」の行に、
    行見出しごと書き込む用途で使う。
    """
    headers = [normalize(c.text) for c in table.rows[0].cells]
    extra = list(cell_map.get("__extra_rows__", []))

    def _pick(values: dict[str, str], header: str) -> str | None:
        """列見出しを正規化して引く（「販売個数／日」と「販売個数/日」を同一視する）."""
        if header in values:
            return values[header]
        return next((v for k, v in values.items() if normalize(k) == header), None)

    for row in table.rows[1:]:
        label = normalize(row.cells[0].text)
        values = cell_map.get(label)
        if values is None and label:
            values = next(
                (v for k, v in cell_map.items() if k != "__extra_rows__" and normalize(k) == label),
                None,
            )
        if values is None:
            # 見出しの無い行・指示文だけの行は追加行として使う
            if extra and (not label or "自由に" in label or "その他" not in label and not values):
                if not label or "自由に" in label:
                    new_label, values = extra.pop(0)
                    _set_text(row.cells[0].paragraphs[0], new_label, size=9)
                else:
                    continue
            else:
                continue
        for col_i, header in enumerate(headers):
            if col_i == 0:
                continue
            value = _pick(values, header)
            if value is None:
                continue
            _set_text(row.cells[col_i].paragraphs[0], str(value), size=9)


# ---------------------------------------------------------------------------
# コマンドライン
# ---------------------------------------------------------------------------


def _load(path: Path) -> dict:
    import yaml

    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def coverage(draft: dict, blocks: dict[str, str]) -> tuple[int, int, list[str]]:
    """下書きの段落が、どれだけ様式に「そのまま」載っているかを測る.

    様式への流し込みは書き直しではなく配置である。せっかく検査を通した本文を
    書き直すと、品質検査の結果が成果物に効かなくなる。ここで機械的に検知する。

    戻り値は (載っている段落数, 全段落数, 載っていない段落の先頭抜粋)。
    """
    form = re.sub(r"\s+", "", "\n".join(str(v) for v in blocks.values()))
    total, hit, lost = 0, 0, []
    for body in (draft.get("sections") or {}).values():
        for para in str(body).split("\n"):
            para = para.strip()
            if len(para) < 18:
                continue
            total += 1
            if re.sub(r"\s+", "", para) in form:
                hit += 1
            elif len(lost) < 6:
                lost.append(para[:60])
    return hit, total, lost


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(
        description="支援機関の事業計画書様式（.docx）に本文と表を流し込む"
    )
    ap.add_argument("fill", help="流し込み内容の YAML（blocks と tables を持つ）")
    ap.add_argument("--template", help="様式の .docx（既定: assets/事業計画書_支援機関様式.docx）")
    ap.add_argument("--out", "-o", default="./事業計画書_記入済.docx", help="出力先")
    ap.add_argument("--loose", action="store_true",
                    help="様式に無い見出しがあってもエラーにしない")
    ap.add_argument("--draft", help="下書きの YAML。本文がそのまま載っているかを検査する")
    args = ap.parse_args()

    raw = _load(Path(args.fill))
    data = raw.get("shien", raw)
    blocks = data.get("blocks") or {}
    if not blocks:
        print("blocks が空です。流し込む内容がありません。", file=sys.stderr)
        return 1
    tables = {int(k): v for k, v in (data.get("tables") or {}).items()}

    template = Path(args.template) if args.template else (
        Path(__file__).resolve().parent.parent / "assets" / "事業計画書_支援機関様式.docx"
    )
    if not template.exists():
        print(f"様式が見つかりません: {template}", file=sys.stderr)
        return 1

    out, missing = fill(template, Path(args.out), blocks, tables, strict=not args.loose)
    body_chars = sum(len(str(v).replace("\n", "")) for v in blocks.values())
    print(f"{out}")
    print(f"  {len(blocks)}項目 / 本文{body_chars:,}字 / 表{len(tables)}点を流し込みました。")
    if missing:
        print(f"  様式に対応する見出しが無かったキー: {', '.join(missing)}", file=sys.stderr)

    if args.draft:
        hit, total, lost = coverage(_load(Path(args.draft)), blocks)
        pct = (hit / total * 100) if total else 100.0
        print(f"  下書き本文の収録率: {hit}/{total}段落（{pct:.0f}%）")
        if pct < 80:
            print(
                "\n  ⚠️  下書きの本文が様式に載っていません。\n"
                "     様式への流し込みは書き直しではなく配置です。検査を通した本文を\n"
                "     書き直すと、品質検査の結果が成果物に効かなくなります。\n"
                "     様式の小項目には下書きの段落をそのまま割り当て、様式が追加で\n"
                "     求める項目（設立経緯・組織体制など）だけを新たに書いてください。",
                file=sys.stderr)
            for x in lost:
                print(f"     載っていない例: {x}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
