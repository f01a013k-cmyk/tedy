"""案件の永続化. Enum や Fraction が混じると YAML/JSON 化で落ちるため回帰を張る."""

from __future__ import annotations

import pytest

from jizokuka import config
from jizokuka.generator import build
from jizokuka.projects import Project, list_projects


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(config.SETTINGS, "workspace", tmp_path)
    return tmp_path


def test_ヒアリングシートを保存して読み戻せる(workspace, sheet):
    proj = Project.open("tanaka")
    proj.save_hearing(sheet)

    loaded = proj.load_hearing()
    assert loaded.company.name == sheet.company.name
    assert loaded.company.sales == sheet.company.sales
    assert loaded.specials == ["インボイス特例"]


def test_ギャップ分析を保存して読み戻せる(workspace, sheet, fake_llm, koubo):
    """Confidence は Enum. mode='json' を忘れると safe_dump が落ちる."""
    from jizokuka.pipeline import gap as gap_mod

    g = gap_mod.analyze(sheet, koubo, fake_llm)
    proj = Project.open("tanaka")
    proj.save_gap(g)

    loaded = proj.load_gap()
    assert loaded is not None
    assert [f.key for f in loaded.facts] == [f.key for f in g.facts]
    assert loaded.questions[0].choices == g.questions[0].choices


def test_計画を保存して読み戻せる(workspace, sheet, fake_llm, koubo):
    result = build(sheet, fake_llm, koubo=koubo, max_revisions=1)
    proj = Project.open("tanaka")
    proj.save_hearing(sheet)
    proj.save_plan(result.plan)

    loaded = proj.load_plan()
    assert loaded.score.total == result.plan.score.total
    assert len(loaded.expenses) == len(result.plan.expenses)
    assert loaded.compliance is not None
    assert loaded.full_text() == result.plan.full_text()


def test_回答ファイルを読み込む(workspace):
    proj = Project.open("tanaka")
    proj.answers_path.write_text(
        'answers:\n  q1: "どら焼き"\n  q2: ""\n', encoding="utf-8"
    )
    answers = proj.load_answers()
    assert answers == {"q1": "どら焼き"}  # 空回答は無視する


def test_案件一覧はヒアリングシートのある案件だけ返す(workspace, sheet):
    Project.open("a").save_hearing(sheet)
    Project.open("b")  # フォルダだけ作る
    assert list_projects() == ["a"]


def test_ヒアリングシート無しで読むと案内付きで落ちる(workspace):
    with pytest.raises(FileNotFoundError, match="jizokuka new"):
        Project.open("nope").load_hearing()


def test_計画無しでcheckすると案内付きで落ちる(workspace):
    with pytest.raises(FileNotFoundError, match="jizokuka build"):
        Project.open("nope").load_plan()
