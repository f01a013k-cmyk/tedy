"""Claude API ラッパ.

- 指数バックオフ付きリトライ
- ディスクキャッシュ（同一プロンプトの再実行で課金しない）
- JSON 応答の頑健な抽出（前置き・コードフェンス混入に耐える）
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from ..config import MODEL_DRAFT, SETTINGS


class LLMError(RuntimeError):
    pass


_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> Any:
    """モデル出力から JSON を取り出す.

    コードフェンス → 全体パース → 最外郭の括弧スキャン、の順に試す。
    """
    candidates: list[str] = []
    for m in _JSON_FENCE.finditer(text):
        candidates.append(m.group(1).strip())
    candidates.append(text.strip())

    for c in candidates:
        try:
            return json.loads(c)
        except json.JSONDecodeError:
            pass

    # 最外郭の { } / [ ] を括弧の対応を数えて切り出す
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = text.find(open_ch)
        if start < 0:
            continue
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if esc:
                esc = False
                continue
            if ch == "\\":
                esc = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
    raise LLMError(f"JSON を抽出できませんでした:\n{text[:600]}")


class LLM:
    def __init__(
        self,
        api_key: str | None = None,
        cache_dir: Path | None = None,
        use_cache: bool = True,
        dry_run: bool = False,
    ) -> None:
        self.dry_run = dry_run
        self.use_cache = use_cache
        self.cache_dir = cache_dir or SETTINGS.cache_dir
        self._client: Any = None
        self._api_key = api_key or SETTINGS.api_key
        if use_cache:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    # -- 内部 ---------------------------------------------------------------
    def _ensure_client(self) -> Any:
        if self._client is None:
            if not self._api_key:
                raise LLMError(
                    "ANTHROPIC_API_KEY が設定されていません。\n"
                    "  export ANTHROPIC_API_KEY=sk-ant-...\n"
                    "または --dry-run で LLM 呼び出しなしの動作確認ができます。"
                )
            try:
                import anthropic
            except ImportError as e:  # pragma: no cover
                raise LLMError("anthropic パッケージが必要です: pip install anthropic") from e
            self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    def _cache_key(self, model: str, system: str, prompt: str, temperature: float) -> Path:
        h = hashlib.sha256(
            json.dumps([model, system, prompt, temperature], ensure_ascii=False).encode()
        ).hexdigest()[:32]
        return self.cache_dir / f"{h}.json"

    # -- 公開 API -----------------------------------------------------------
    def complete(
        self,
        prompt: str,
        *,
        system: str = "",
        model: str = MODEL_DRAFT,
        max_tokens: int = 8000,
        temperature: float = 0.4,
        retries: int = 4,
    ) -> str:
        if self.dry_run:
            return f"[dry-run] model={model} prompt_head={prompt[:80]!r}"

        cache_path = self._cache_key(model, system, prompt, temperature) if self.use_cache else None
        if cache_path and cache_path.exists():
            return json.loads(cache_path.read_text(encoding="utf-8"))["text"]

        client = self._ensure_client()
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system

        last_err: Exception | None = None
        for attempt in range(retries):
            try:
                resp = client.messages.create(**kwargs)
                text = "".join(
                    b.text for b in resp.content if getattr(b, "type", "") == "text"
                )
                if cache_path:
                    cache_path.write_text(
                        json.dumps({"text": text, "model": model}, ensure_ascii=False),
                        encoding="utf-8",
                    )
                return text
            except Exception as e:  # noqa: BLE001 - SDK の例外型に依存しない
                last_err = e
                if attempt == retries - 1:
                    break
                time.sleep(2 ** attempt)
        raise LLMError(f"Claude API 呼び出しに失敗しました: {last_err}") from last_err

    def complete_json(
        self,
        prompt: str,
        *,
        system: str = "",
        model: str = MODEL_DRAFT,
        max_tokens: int = 8000,
        temperature: float = 0.2,
        fallback: Any = None,
    ) -> Any:
        """JSON を返させる。失敗時は fallback があればそれを返す."""
        if self.dry_run:
            return fallback if fallback is not None else {}
        system = (system + "\n\n出力は JSON のみ。説明文・前置きは一切書かないこと。").strip()
        text = self.complete(
            prompt, system=system, model=model, max_tokens=max_tokens, temperature=temperature
        )
        try:
            return extract_json(text)
        except LLMError:
            if fallback is not None:
                return fallback
            raise


def load_prompt(name: str) -> str:
    from ..config import PROMPT_DIR

    path = PROMPT_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"プロンプトが見つかりません: {path}")
    return path.read_text(encoding="utf-8")
