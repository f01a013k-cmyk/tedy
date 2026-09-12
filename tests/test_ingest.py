import pytest

from jizokuka.pipeline import ingest


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_csvから取り込む(tmp_path):
    """現場のシートは2列のCSVで来ることが多い."""
    p = _write(
        tmp_path,
        "tanaka.csv",
        "\n".join(
            [
                "事業者名,有限会社田中製菓",
                "所在地,長野県松本市",
                "従業員数,4",
                "主力商品,どら焼き・大福",
                "2023年度売上,4200万",
                "2025年度売上,3500万",
                "取組,冷凍機・EC",
            ]
        ),
    )
    s = ingest.load(p)
    assert s.company.name == "有限会社田中製菓"
    assert s.company.employees == 4
    assert s.company.products == ["どら焼き", "大福"]
    assert s.company.sales == {"2023": 42_000_000, "2025": 35_000_000}
    assert s.project.idea == ["冷凍機", "EC"]


def test_未知の見出しは備考に落ちて捨てられない(tmp_path):
    p = _write(tmp_path, "x.csv", "事業者名,A商店\n息子の意向,来年Uターン予定")
    s = ingest.load(p)
    assert "来年Uターン予定" in s.free_notes


def test_金額の単位表記を解釈する(tmp_path):
    p = _write(tmp_path, "x.csv", "資本金,300万\n従業員数,3")
    s = ingest.load(p)
    assert s.company.capital == 3_000_000


def test_xlsxから取り込む(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in [("事業者名", "B工房"), ("強み", "溶接技術・短納期"), ("2025年度売上", 12_000_000)]:
        ws.append(row)
    p = tmp_path / "b.xlsx"
    wb.save(p)

    s = ingest.load(p)
    assert s.company.name == "B工房"
    assert s.strengths == ["溶接技術", "短納期"]
    assert s.company.sales == {"2025": 12_000_000}


def test_未対応形式はエラー(tmp_path):
    p = _write(tmp_path, "x.txt", "hello")
    with pytest.raises(ValueError, match="未対応の形式"):
        ingest.load(p)


def test_存在しないファイルはエラー(tmp_path):
    with pytest.raises(FileNotFoundError):
        ingest.load(tmp_path / "nope.yaml")
