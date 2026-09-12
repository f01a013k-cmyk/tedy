"""設定. 環境変数とプロジェクトディレクトリの解決."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent
KNOWLEDGE_DIR = PKG_DIR / "knowledge"
PROMPT_DIR = PKG_DIR / "llm" / "prompts"
TEMPLATE_DIR = PKG_DIR / "templates"
EXAMPLE_DIR = PKG_DIR / "examples"
FORM_DIR = PKG_DIR / "forms"

# 用途別モデル。長文生成と厳密採点は上位モデル、抽出系は高速モデルに振る。
MODEL_DRAFT = os.environ.get("JIZOKUKA_MODEL_DRAFT", "claude-opus-5")
MODEL_REVIEW = os.environ.get("JIZOKUKA_MODEL_REVIEW", "claude-opus-5")
MODEL_EXTRACT = os.environ.get("JIZOKUKA_MODEL_EXTRACT", "claude-sonnet-5")


@dataclass
class Settings:
    workspace: Path = field(
        default_factory=lambda: Path(
            os.environ.get("JIZOKUKA_WORKSPACE", "./projects")
        ).resolve()
    )
    api_key: str | None = field(
        default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY")
    )
    max_revisions: int = int(os.environ.get("JIZOKUKA_MAX_REVISIONS", "3"))
    target_score: float = float(os.environ.get("JIZOKUKA_TARGET_SCORE", "80"))
    cache_dir: Path = field(
        default_factory=lambda: Path(
            os.environ.get("JIZOKUKA_CACHE", "./.jizokuka_cache")
        ).resolve()
    )

    def project_dir(self, project_id: str) -> Path:
        return self.workspace / project_id

    def ensure(self, project_id: str) -> Path:
        d = self.project_dir(project_id)
        (d / "outputs").mkdir(parents=True, exist_ok=True)
        return d


SETTINGS = Settings()
