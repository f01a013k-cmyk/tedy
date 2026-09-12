"""公募要領ナレッジのロードとアクセス."""

from __future__ import annotations

import re
from dataclasses import dataclass
from fractions import Fraction
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .config import KNOWLEDGE_DIR


@dataclass
class Koubo:
    """公募要領1回次分のナレッジ."""

    raw: dict[str, Any]
    path: Path

    # -- メタ ---------------------------------------------------------------
    @property
    def id(self) -> str:
        return self.raw["meta"]["koubo_id"]

    @property
    def display_name(self) -> str:
        return self.raw["meta"]["display_name"]

    @property
    def verified(self) -> bool:
        return bool(self.raw["meta"].get("verified"))

    # -- 枠と金額 -----------------------------------------------------------
    def frame(self, name: str) -> dict[str, Any]:
        frames = self.raw["frames"]
        if name not in frames:
            raise KeyError(
                f"未知の申請枠 '{name}'。利用可能: {', '.join(frames)}"
            )
        return frames[name]

    def limit_yen(self, frame: str, specials: list[str] | None = None) -> int:
        """申請枠＋特例を加味した補助上限額(円)."""
        total = int(self.frame(frame)["limit_yen"])
        for s in specials or []:
            sp = self.raw.get("specials", {}).get(s)
            if sp:
                total += int(sp["add_limit_yen"])
        return total

    def rate(self, frame: str, is_deficit: bool | None = None) -> Fraction:
        """補助率を Fraction で返す.

        float だと 2/3 が 0.6667 に丸まり、600,000 × 2/3 が 400,020 円のような
        ありえない端数を生む。金額計算は必ず分数で行う。
        """
        f = self.frame(frame)
        raw = f["rate_if_deficit"] if (is_deficit and "rate_if_deficit" in f) else f["rate"]
        return Fraction(str(raw))

    def rate_label(self, frame: str) -> str:
        return str(self.frame(frame).get("rate_label", ""))

    # -- 経費 ---------------------------------------------------------------
    @property
    def expense_categories(self) -> list[dict[str, Any]]:
        return self.raw["expense_categories"]

    def category_names(self) -> list[str]:
        return [c["name"] for c in self.expense_categories]

    def category(self, name: str) -> dict[str, Any] | None:
        norm = (name or "").strip()
        for c in self.expense_categories:
            if c["name"] == norm or c["code"] == norm:
                return c
        # 表記ゆれ吸収: 部分一致
        for c in self.expense_categories:
            if norm and (norm in c["name"] or c["name"] in norm):
                return c
        return None

    def ineligible_hits(self, text: str) -> list[tuple[str, str]]:
        """対象外経費キーワードの検出. (ヒット語, 理由) を返す."""
        hits: list[tuple[str, str]] = []
        for rule in self.raw.get("ineligible_keywords", []):
            for kw in rule["keyword"]:
                if kw in text:
                    hits.append((kw, rule["reason"]))
                    break
        return hits

    @property
    def expense_rules(self) -> dict[str, Any]:
        return self.raw.get("expense_rules", {})

    # -- 様式 ---------------------------------------------------------------
    @property
    def sections(self) -> list[dict[str, Any]]:
        return self.raw["form_yoshiki2"]["sections"]

    @property
    def project_sections(self) -> list[dict[str, Any]]:
        return self.raw["form_yoshiki2"]["project_sections"]

    def all_section_specs(self) -> list[dict[str, Any]]:
        return [*self.sections, *self.project_sections]

    def section_spec(self, key: str) -> dict[str, Any] | None:
        return next((s for s in self.all_section_specs() if s["key"] == key), None)

    # -- 審査 ---------------------------------------------------------------
    @property
    def review_axes(self) -> list[dict[str, Any]]:
        return self.raw["review_axes"]

    def axis(self, key: str) -> dict[str, Any] | None:
        return next((a for a in self.review_axes if a["key"] == key), None)

    def forbidden_hits(self, text: str) -> list[tuple[str, str]]:
        """禁止表現の検出. (該当箇所, 理由) を返す."""
        hits: list[tuple[str, str]] = []
        for rule in self.raw.get("forbidden_expressions", []):
            for m in re.finditer(rule["pattern"], text):
                hits.append((m.group(0), rule["reason"]))
        return hits

    # -- プロンプト向けの要約 ------------------------------------------------
    def review_axes_prompt(self) -> str:
        lines = []
        for a in self.review_axes:
            lines.append(f"■ {a['key']}（配点{a['weight']}点）")
            lines.extend(f"  - {c}" for c in a["criteria"])
        return "\n".join(lines)

    def expense_categories_prompt(self) -> str:
        lines = []
        for c in self.expense_categories:
            line = f"{c['code']}. {c['name']}: 例）{'、'.join(c['examples'])}"
            if c.get("caution"):
                line += f" ※{c['caution']}"
            lines.append(line)
        return "\n".join(lines)


class KouboSchemaError(ValueError):
    """公募要領ナレッジの定義不備."""


_REQUIRED_SECTION_KEYS = ("key", "number", "heading")
_REQUIRED_TOP_KEYS = ("meta", "frames", "expense_categories", "form_yoshiki2", "review_axes")


def _validate(raw: dict[str, Any], path: Path) -> None:
    """ナレッジ定義の静的検証.

    YAML 1.1 は `no:` `yes:` `on:` を真偽値として解釈するため、キー名が
    意図せず bool になる事故が起きる。起動時に検出して明示的に落とす。
    """
    for key in _REQUIRED_TOP_KEYS:
        if key not in raw:
            raise KouboSchemaError(f"{path}: 必須キー '{key}' がありません")

    def _check_bool_keys(node: Any, trail: str = "") -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(k, bool):
                    raise KouboSchemaError(
                        f"{path}: {trail} のキーが真偽値 {k} になっています。"
                        f"YAML では no/yes/on/off はクォートが必要です（例: \"no\"）。"
                    )
                _check_bool_keys(v, f"{trail}.{k}" if trail else str(k))
        elif isinstance(node, list):
            for i, v in enumerate(node):
                _check_bool_keys(v, f"{trail}[{i}]")

    _check_bool_keys(raw)

    form = raw["form_yoshiki2"]
    specs = [*form.get("sections", []), *form.get("project_sections", [])]
    if not specs:
        raise KouboSchemaError(f"{path}: form_yoshiki2 にセクション定義がありません")

    axis_keys = {a["key"] for a in raw["review_axes"]}
    seen: set[str] = set()
    for spec in specs:
        for key in _REQUIRED_SECTION_KEYS:
            if key not in spec:
                raise KouboSchemaError(
                    f"{path}: セクション定義 {spec.get('key', spec)} に '{key}' がありません"
                )
        if spec["key"] in seen:
            raise KouboSchemaError(f"{path}: セクションキー '{spec['key']}' が重複しています")
        seen.add(spec["key"])
        for axis in spec.get("review_axes", []):
            if axis not in axis_keys:
                raise KouboSchemaError(
                    f"{path}: セクション '{spec['key']}' が未定義の審査観点 '{axis}' を参照しています"
                )

    for name, f in raw["frames"].items():
        for key in ("limit_yen", "rate"):
            if key not in f:
                raise KouboSchemaError(f"{path}: 申請枠 '{name}' に '{key}' がありません")
        try:
            Fraction(str(f["rate"]))
        except (ValueError, ZeroDivisionError) as e:
            raise KouboSchemaError(
                f"{path}: 申請枠 '{name}' の補助率 '{f['rate']}' を解釈できません"
            ) from e


@lru_cache(maxsize=2)
def load_section_examples() -> dict[str, dict[str, str]]:
    """セクション別 few-shot（悪い例 / 良い例）を読み込む."""
    path = KNOWLEDGE_DIR / "section_examples.yaml"
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")).get("examples", {})


def examples_prompt(section_key: str) -> str:
    """該当セクションの few-shot だけをプロンプト用に整形する.

    全セクション分を入れるとトークンを浪費し、かつ関係ない例に引きずられる。
    """
    ex = load_section_examples().get(section_key)
    if not ex:
        return ""
    parts: list[str] = []
    if ex.get("bad"):
        parts.append(
            "### 不採択になる書き方（これを避ける）\n\n"
            f"> {ex['bad'].strip()}\n\n"
            f"**なぜ駄目か**: {ex.get('bad_why', '')}"
        )
    if ex.get("good"):
        parts.append(
            "### 採択レベルの書き方（この粒度を目指す）\n\n"
            f"> {ex['good'].strip()}\n\n"
            f"**なぜ良いか**: {ex.get('good_why', '')}\n\n"
            "※ 上の例は架空の事業者のものである。数値や固有名詞を流用してはならない。"
            "真似るのは**粒度と論理構成**だけである。"
        )
    return "\n\n".join(parts)


@lru_cache(maxsize=8)
def load_koubo(koubo_id: str = "r17") -> Koubo:
    path = KNOWLEDGE_DIR / f"koubo_{koubo_id}.yaml"
    if not path.exists():
        available = sorted(p.stem.replace("koubo_", "") for p in KNOWLEDGE_DIR.glob("koubo_*.yaml"))
        raise FileNotFoundError(
            f"公募要領ナレッジが見つかりません: {path}\n"
            f"利用可能: {', '.join(available) or '(なし)'}"
        )
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    _validate(raw, path)
    return Koubo(raw=raw, path=path)
