"""ヒアリングシートの取込.

支援機関の現場では、顧客からの情報は YAML とは限らない。
Excel・CSV・面談メモのテキストのいずれでも受け取れるようにする。
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

import yaml

from ..models import HearingSheet

# Excel/CSV の見出し語 → スキーマのパス
_ALIASES: dict[str, str] = {
    "事業者名": "company.name", "会社名": "company.name", "屋号": "company.name",
    "代表者": "company.representative", "代表者名": "company.representative",
    "所在地": "company.address", "住所": "company.address",
    "創業": "company.founded", "創業年": "company.founded", "設立": "company.founded",
    "従業員数": "company.employees", "従業員": "company.employees",
    "資本金": "company.capital",
    "業種": "company.industry",
    "事業内容": "company.business",
    "主力商品": "company.products", "商品": "company.products", "サービス": "company.products",
    "顧客層": "company.customers", "顧客": "company.customers",
    "販売チャネル": "company.channels", "チャネル": "company.channels", "販路": "company.channels",
    "商圏": "company.area", "エリア": "company.area",
    "強み": "strengths", "弱み": "weaknesses",
    "市場": "market", "市場動向": "market",
    "目標": "goal", "やりたいこと": "goal", "今後": "goal",
    "申請枠": "frame", "枠": "frame",
    "特例": "specials",
    "補助事業": "project.idea", "取組": "project.idea", "アイデア": "project.idea",
    "ターゲット": "project.target_customer",
    "備考": "free_notes", "メモ": "free_notes", "面談メモ": "free_notes",
}

_YEAR_SALES = re.compile(r"売上.*?(\d{4})|(\d{4}).*?売上")
_YEAR_PROFIT = re.compile(r"(?:営業)?利益.*?(\d{4})|(\d{4}).*?(?:営業)?利益")


def _assign(data: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    node = data
    for p in parts[:-1]:
        node = node.setdefault(p, {})
    node[parts[-1]] = value


def _parse_money(v: Any) -> int | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).replace(",", "").replace("円", "").strip()
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(万|億)?$", s)
    if not m:
        return None
    n = float(m.group(1))
    unit = {"万": 10_000, "億": 100_000_000}.get(m.group(2) or "", 1)
    return int(n * unit)


def _rows_to_dict(rows: list[tuple[str, Any]], project_id: str) -> dict[str, Any]:
    """(見出し, 値) の並びをヒアリングシート dict に変換する."""
    data: dict[str, Any] = {"project_id": project_id, "company": {}, "project": {}}
    notes: list[str] = []

    for label, value in rows:
        label = (label or "").strip()
        if not label or value in (None, ""):
            continue

        # 年度付きの売上・利益
        m = _YEAR_SALES.search(label)
        if m and "利益" not in label:
            year = m.group(1) or m.group(2)
            amount = _parse_money(value)
            if year and amount is not None:
                data["company"].setdefault("sales", {})[year] = amount
                continue
        m = _YEAR_PROFIT.search(label)
        if m:
            year = m.group(1) or m.group(2)
            amount = _parse_money(value)
            if year and amount is not None:
                data["company"].setdefault("profit", {})[year] = amount
                continue

        path = _ALIASES.get(label)
        if path is None:
            # 部分一致で救う
            path = next((v for k, v in _ALIASES.items() if k in label), None)
        if path is None:
            notes.append(f"{label}: {value}")
            continue

        if path in ("company.employees", "company.capital"):
            parsed = _parse_money(value)
            if parsed is not None:
                _assign(data, path, parsed)
        else:
            _assign(data, path, value)

    if notes:
        existing = data.get("free_notes") or ""
        data["free_notes"] = (existing + "\n" + "\n".join(notes)).strip()
    return data


def from_yaml(path: Path) -> HearingSheet:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw.setdefault("project_id", path.stem)
    return HearingSheet.model_validate(raw)


def from_json(path: Path) -> HearingSheet:
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw.setdefault("project_id", path.stem)
    return HearingSheet.model_validate(raw)


def from_csv(path: Path) -> HearingSheet:
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = [(r[0], r[1] if len(r) > 1 else "") for r in csv.reader(f) if r]
    return HearingSheet.model_validate(_rows_to_dict(rows, path.stem))


def from_xlsx(path: Path) -> HearingSheet:
    from openpyxl import load_workbook

    wb = load_workbook(path, data_only=True)
    rows: list[tuple[str, Any]] = []
    for ws in wb.worksheets:
        for row in ws.iter_rows(values_only=True):
            cells = [c for c in row if c is not None and str(c).strip()]
            if len(cells) >= 2:
                rows.append((str(cells[0]), cells[1]))
    return HearingSheet.model_validate(_rows_to_dict(rows, path.stem))


_LOADERS = {
    ".yaml": from_yaml, ".yml": from_yaml,
    ".json": from_json,
    ".csv": from_csv,
    ".xlsx": from_xlsx, ".xlsm": from_xlsx,
}


def load(path: str | Path) -> HearingSheet:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"ヒアリングシートが見つかりません: {p}")
    loader = _LOADERS.get(p.suffix.lower())
    if loader is None:
        raise ValueError(
            f"未対応の形式です: {p.suffix}（対応: {', '.join(sorted(_LOADERS))}）"
        )
    return loader(p)
