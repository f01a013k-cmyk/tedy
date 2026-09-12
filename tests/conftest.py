"""テスト共通のフィクスチャ.

LLM をスタブ化し、API キー無し・課金無しでパイプライン全体を検証できるようにする。
"""

from __future__ import annotations

import json

import pytest

from jizokuka.knowledge import load_koubo
from jizokuka.llm import LLM


class FakeLLM(LLM):
    """プロンプトの中身を見て、それらしい応答を返すスタブ."""

    def __init__(self) -> None:
        super().__init__(api_key="test", use_cache=False, dry_run=False)
        self.calls: list[str] = []

    def complete(self, prompt: str, **kw) -> str:  # type: ignore[override]
        self.calls.append(prompt[:60])

        if "ギャップ分析" in prompt:
            return json.dumps(
                {
                    "facts": [
                        {
                            "key": "商圏人口",
                            "value": "松本市の人口約24万人",
                            "confidence": "inferred",
                            "source": "address",
                            "rationale": "所在地から推定",
                        }
                    ],
                    "questions": [
                        {
                            "id": "q1",
                            "text": "最初に全国展開したい商品はどれですか",
                            "why": "補助事業計画の有効性",
                            "choices": ["どら焼き", "季節生菓子", "大福"],
                            "allow_free_text": True,
                            "impact": 5,
                            "section": "sales_channel",
                        }
                    ],
                    "blocking": [],
                },
                ensure_ascii=False,
            )

        if "経費明細表の構造化" in prompt:
            return json.dumps(
                {
                    "expenses": [
                        {
                            "category": "機械装置等費",
                            "item": "特殊冷凍機 1台",
                            "unit_price": 400000,
                            "quantity": 1,
                            "amount": 400000,
                            "basis": "A社見積による",
                            "linked_activity": "生菓子の冷凍保存",
                        },
                        {
                            "category": "ウェブサイト関連費",
                            "item": "ECサイト構築",
                            "unit_price": 100000,
                            "quantity": 1,
                            "amount": 100000,
                            "basis": "B社見積による",
                            "linked_activity": "全国向けEC販売",
                        },
                    ],
                    "warnings": ["パッケージデザインは広報費に振り替えを推奨"],
                },
                ensure_ascii=False,
            )

        if "審査員としての採点" in prompt:
            # リライト後は点が上がる（ループの終了条件を検証するため）
            improved = "書き直した" in prompt or self.calls.count("REVISED") > 0
            base = 85 if improved else 62
            return json.dumps(
                {
                    "axes": [
                        {
                            "axis": a["key"],
                            "score": base,
                            "findings": ["根拠となる数値が不足している"],
                            "improvements": ["客単価と想定客数から売上増を算出して示す"],
                        }
                        for a in load_koubo().review_axes
                    ]
                },
                ensure_ascii=False,
            )

        if "リライト" in prompt:
            self.calls.append("REVISED")
            return "改稿後の本文である。客単価2,800円×新規客数月80人×12か月＝年間268万円の増加を見込む。"

        if "の執筆" in prompt:
            if "補助事業で行う事業名" in prompt:
                return "特殊冷凍機導入による生菓子の全国EC販路開拓"
            return "生成された本文である。" * 12

        return "（不明なプロンプト）"

    def complete_json(self, prompt: str, **kw):  # type: ignore[override]
        from jizokuka.llm.client import extract_json

        return extract_json(self.complete(prompt))


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def koubo():
    return load_koubo("r17")


@pytest.fixture
def sheet():
    import yaml

    from jizokuka.models import HearingSheet

    from pathlib import Path

    from jizokuka.config import EXAMPLE_DIR

    path = EXAMPLE_DIR / "tanaka_seika.yaml"
    return HearingSheet.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
