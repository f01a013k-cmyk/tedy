from fractions import Fraction

import pytest


def test_補助率は分数で厳密に扱う(koubo):
    """float だと 600,000 × 2/3 が 400,020 円になる. 金額計算では許されない."""
    rate = koubo.rate("通常枠")
    assert rate == Fraction(2, 3)
    assert int(600_000 * rate) == 400_000


def test_赤字事業者の補助率(koubo):
    assert koubo.rate("賃金引上げ枠", is_deficit=True) == Fraction(3, 4)
    assert koubo.rate("賃金引上げ枠", is_deficit=False) == Fraction(2, 3)


def test_特例で上限が積み上がる(koubo):
    assert koubo.limit_yen("通常枠") == 500_000
    assert koubo.limit_yen("通常枠", ["インボイス特例"]) == 1_000_000


def test_未知の申請枠はエラーになる(koubo):
    with pytest.raises(KeyError):
        koubo.frame("存在しない枠")


def test_経費区分は表記ゆれを吸収する(koubo):
    assert koubo.category("ウェブサイト")["name"] == "ウェブサイト関連費"
    assert koubo.category("3")["name"] == "ウェブサイト関連費"
    assert koubo.category("存在しない費目") is None


def test_対象外経費を検出する(koubo):
    hits = dict(koubo.ineligible_hits("ノートパソコンと社用車"))
    assert "パソコン" in hits


def test_禁止表現を検出する(koubo):
    hits = [h for h, _ in koubo.forbidden_hits("絶対に成功します。日本一の味です。")]
    assert any("絶対に成功" in h for h in hits)
    assert "日本一" in hits


def test_審査観点の配点合計が100(koubo):
    assert sum(a["weight"] for a in koubo.review_axes) == 100


def test_YAMLの真偽値キー事故を検出する(tmp_path, monkeypatch):
    """`no:` は YAML 1.1 で False になる. 静かに壊れないよう起動時に落とす."""
    import yaml

    from jizokuka import knowledge
    from jizokuka.knowledge import KouboSchemaError, load_koubo

    raw = yaml.safe_load(
        (knowledge.KNOWLEDGE_DIR / "koubo_r17.yaml").read_text(encoding="utf-8")
    )
    raw["form_yoshiki2"]["sections"][0][False] = "1"
    del raw["form_yoshiki2"]["sections"][0]["number"]

    path = tmp_path / "koubo_broken.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(knowledge, "KNOWLEDGE_DIR", tmp_path)
    load_koubo.cache_clear()

    with pytest.raises(KouboSchemaError, match="真偽値"):
        load_koubo("broken")
    load_koubo.cache_clear()


def test_未定義の審査観点を参照したら落ちる(tmp_path, monkeypatch):
    import yaml

    from jizokuka import knowledge
    from jizokuka.knowledge import KouboSchemaError, load_koubo

    raw = yaml.safe_load(
        (knowledge.KNOWLEDGE_DIR / "koubo_r17.yaml").read_text(encoding="utf-8")
    )
    raw["form_yoshiki2"]["sections"][0]["review_axes"] = ["存在しない観点"]
    path = tmp_path / "koubo_badaxis.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(knowledge, "KNOWLEDGE_DIR", tmp_path)
    load_koubo.cache_clear()

    with pytest.raises(KouboSchemaError, match="未定義の審査観点"):
        load_koubo("badaxis")
    load_koubo.cache_clear()


def test_同梱ナレッジは全て検証を通る():
    """knowledge/ に置いた定義が壊れていないことを保証する."""
    from jizokuka.knowledge import KNOWLEDGE_DIR, load_koubo

    ids = [p.stem.replace("koubo_", "") for p in KNOWLEDGE_DIR.glob("koubo_*.yaml")]
    assert ids
    for kid in ids:
        assert load_koubo(kid).review_axes
