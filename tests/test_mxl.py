"""Unit-тесты модуля M3 (MXL) без живого EDT.

Покрывают: парсер .mxl → DSL, генератор DSL → .mxl, round-trip
(parse→write→parse), tolerant-режим и инструменты decompile/info/compile
(файловая стратегия, dryRun, запрет мутаций). Ядро edt_bridge
(config/safety/proxy) при отсутствии заменяется stub'ами.
"""

import asyncio
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from edt_bridge.mxl.mxl_parser import parse_mxl  # noqa: E402
from edt_bridge.mxl.mxl_writer import MxlWriteError, write_mxl  # noqa: E402
from edt_bridge.mxl import mxl as mxl_tools  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "sample.mxl"


def _dsl(text: str) -> dict:
    """DSL без служебных полей (warnings/raw) — для сравнения round-trip."""
    result = parse_mxl(text)
    result.pop("warnings", None)
    result.pop("raw", None)
    return result


def _run(coro):
    return asyncio.run(coro)


# ----------------------------------------------------------------- парсер
def test_parse_fixture_areas_and_columns():
    dsl = _dsl(FIXTURE.read_text(encoding="utf-8"))
    assert dsl["columns"] == 3
    assert [a["name"] for a in dsl["areas"]] == ["Заголовок", "Таблица", "Подвал"]
    assert dsl["columnWidths"] == {"1": 20}
    assert dsl["pageSetup"] == {"paperSize": "A4", "orientation": "Portrait"}


def test_parse_fixture_cells():
    dsl = _dsl(FIXTURE.read_text(encoding="utf-8"))
    header = dsl["areas"][0]["rows"][0]
    assert header["height"] == 25
    cell = header["cells"][0]
    assert cell["col"] == 1
    assert cell["param"] == "ТекстЗаголовка"
    assert cell["span"] == 3
    assert dsl["styles"][cell["style"]]["align"] == "center"
    assert dsl["styles"][cell["style"]]["font"] == "bold"

    row = dsl["areas"][1]["rows"][1]
    assert row["rowStyle"] == "bordered"
    tcell = row["cells"][0]
    assert tcell["param"] == "Товар"
    assert tcell["detail"] == "Номенклатура"

    footer = dsl["areas"][2]["rows"][0]["cells"][0]
    assert footer["template"] == "Итого: [Всего]"
    assert dsl["styles"][footer["style"]]["backColor"] == "#FFEECC"


def test_parse_fixture_fonts_and_styles():
    dsl = _dsl(FIXTURE.read_text(encoding="utf-8"))
    assert dsl["fonts"]["default"] == {"face": "Arial", "size": 10}
    assert dsl["fonts"]["bold"]["bold"] is True
    assert dsl["styles"]["bordered"]["border"] == "all"


def test_parse_tolerant_not_xml():
    dsl = parse_mxl("{1,0,{0,0}}")
    assert dsl["areas"] == []
    assert dsl["warnings"]  # есть предупреждение о неподдерживаемом формате


def test_parse_tolerant_broken_xml():
    dsl = parse_mxl("<document><rowsItem>")
    assert dsl["areas"] == []
    assert any("ошибка разбора XML" in w for w in dsl["warnings"])


def test_parse_tolerant_unknown_elements():
    text = FIXTURE.read_text(encoding="utf-8").replace(
        "</document>",
        "\t<drawing><name>Картинка1</name></drawing>\n</document>",
    )
    dsl = parse_mxl(text)
    assert any("drawing" in w for w in dsl["warnings"])
    assert dsl["raw"] and dsl["raw"][0]["tag"] == "drawing"


def test_parse_index_to_repetition():
    text = FIXTURE.read_text(encoding="utf-8").replace(
        "<index>3</index>", "<index>3</index>\n\t\t<indexTo>4</indexTo>", 1,
    ).replace("<height>4</height>", "<height>5</height>").replace(
        "<vgRows>4</vgRows>", "<vgRows>5</vgRows>"
    ).replace("<endRow>3</endRow>\n\t\t\t<beginColumn>-1</beginColumn>\n"
              "\t\t\t<endColumn>-1</endColumn>\n\t\t</area>\n"
              "\t</namedItem>",
              "<endRow>4</endRow>\n\t\t\t<beginColumn>-1</beginColumn>\n"
              "\t\t\t<endColumn>-1</endColumn>\n\t\t</area>\n"
              "\t</namedItem>")
    dsl = _dsl(text)
    footer = dsl["areas"][2]
    assert len(footer["rows"]) == 2  # строка продублирована через indexTo


# ----------------------------------------------------------------- writer
def test_write_minimal_dsl():
    dsl = {
        "columns": 2,
        "areas": [{"name": "Шапка", "rows": [
            {"cells": [{"col": 1, "text": "Привет"},
                        {"col": 2, "param": "Значение"}]},
        ]}],
    }
    xml, warnings = write_mxl(dsl)
    assert 'fillType>Text<' in xml
    assert 'fillType>Parameter<' in xml
    assert "<name>Шапка</name>" in xml
    assert any("шрифт 'default'" in w or "шрифты не заданы" in w
               for w in warnings)


def test_write_errors():
    with pytest.raises(MxlWriteError):
        write_mxl({"columns": 0, "areas": []})
    with pytest.raises(MxlWriteError):
        write_mxl({"columns": 2, "areas": []})
    with pytest.raises(MxlWriteError):
        write_mxl({"columns": 2,
                   "areas": [{"name": "А", "rows": [
                       {"cells": [{"col": 5, "text": "x"}]}]}]})


def test_write_page_and_nx_widths():
    dsl = {
        "columns": 4, "page": "A4-portrait",
        "columnWidths": {"1-4": "1x"},
        "areas": [{"name": "А", "rows": [{"cells": [
            {"col": 1, "text": "x"}]}]}],
    }
    xml, _ = write_mxl(dsl)
    # 540 / 4 = 135 на колонку
    assert "<width>135</width>" in xml
    assert "<columnsItem>" in xml


def test_write_span_rowspan_merges():
    dsl = {
        "columns": 3,
        "areas": [{"name": "А", "rows": [
            {"cells": [{"col": 1, "span": 2, "rowspan": 2, "text": "x"}]},
            {"rowStyle": "default", "cells": [{"col": 3, "text": "y"}]},
        ]}],
    }
    xml, _ = write_mxl(dsl)
    assert "<merge>" in xml
    assert "<h>1</h>" in xml
    assert "<w>1</w>" in xml


# --------------------------------------------------------------- round-trip
def test_round_trip_fixture():
    text1 = FIXTURE.read_text(encoding="utf-8")
    dsl1 = _dsl(text1)
    xml2, _ = write_mxl(dsl1)
    dsl2 = _dsl(xml2)
    assert dsl1 == dsl2


def test_round_trip_spec_example():
    """Пример из mxl-dsl-spec: write → parse → write → parse."""
    dsl = {
        "columns": 10,
        "columnWidths": {"1": 15, "2-8": 40, "9-10": 50},
        "fonts": {
            "default": {"face": "Arial", "size": 10},
            "bold": {"face": "Arial", "size": 10, "bold": True},
            "header": {"face": "Arial", "size": 14, "bold": True},
        },
        "styles": {
            "default": {},
            "header": {"font": "header", "align": "center"},
            "bordered": {"border": "all"},
            "bordered-right": {"border": "all", "align": "right"},
            "total-right": {"font": "bold", "border": "top", "align": "right"},
        },
        "areas": [
            {"name": "Заголовок", "rows": [
                {"height": 20, "cells": [
                    {"col": 1, "span": 10, "style": "header",
                     "param": "ТекстЗаголовка"}]}]},
            {"name": "Строка", "rows": [
                {"rowStyle": "bordered", "cells": [
                    {"col": 2, "span": 6, "param": "Товар",
                     "detail": "Номенклатура"},
                    {"col": 9, "style": "bordered-right", "param": "Количество"},
                    {"col": 10, "style": "bordered-right", "param": "Сумма"}]}]},
            {"name": "Итого", "rows": [
                {"cells": [
                    {"col": 8, "span": 2, "style": "total-right",
                     "text": "Итого:"},
                    {"col": 10, "style": "total-right", "param": "Всего"}]}]},
        ],
    }
    xml1, w1 = write_mxl(dsl)
    assert not w1
    dsl2 = _dsl(xml1)
    xml2, _ = write_mxl(dsl2)
    assert _dsl(xml2) == dsl2
    # ключевые свойства сохранились
    row = dsl2["areas"][1]["rows"][0]
    assert row["rowStyle"] is not None
    params = {c.get("param") for c in row["cells"]}
    assert {"Товар", "Количество", "Сумма"} <= params
    total = dsl2["areas"][2]["rows"][0]["cells"][0]
    assert total["span"] == 2 and total["text"] == "Итого:"


# ------------------------------------------------------------------ tools
@pytest.fixture()
def project(tmp_path, monkeypatch):
    """Мини-проект EDT с макетом из фикстуры."""
    tpl_dir = (tmp_path / "src" / "Reports" / "Продажи"
               / "Templates" / "Печать")
    tpl_dir.mkdir(parents=True)
    (tpl_dir / "Template.mxl").write_text(
        FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("EDTB_PROJECT_PATH", str(tmp_path))
    monkeypatch.delenv("EDTB_ALLOW_FILE_MUTATIONS", raising=False)
    # stub ядра: proxy отсутствует → fallback с warnings
    return tmp_path


def test_tool_info(project):
    res = _run(mxl_tools.edtb_mxl_info("Report.Продажи", "Печать"))
    assert res["ok"]
    assert res["columns"] == 3
    assert res["rows"] == 4
    assert [a["name"] for a in res["areas"]] == [
        "Заголовок", "Таблица", "Подвал"]
    assert "Товар" in res["params"] and "ТекстЗаголовка" in res["params"]
    assert "Номенклатура" in res["detailParams"]
    assert res["mergeCount"] == 1


def test_tool_info_not_found(project):
    res = _run(mxl_tools.edtb_mxl_info("Report.Продажи", "НетТакого"))
    assert not res["ok"]
    assert "не найден" in res["error"]


def test_tool_decompile(project):
    res = _run(mxl_tools.edtb_mxl_decompile("Report.Продажи", "Печать"))
    assert res["ok"]
    assert res["dsl"]["columns"] == 3
    assert res["dsl"]["areas"][0]["name"] == "Заголовок"


def test_tool_compile_dry_run(project):
    dsl = {"columns": 1, "areas": [{"name": "А", "rows": [
        {"cells": [{"col": 1, "text": "x"}]}]}]}
    res = _run(mxl_tools.edtb_mxl_compile(
        "Report.Продажи", "Печать", dsl, dryRun=True, strategy="file"))
    assert res["ok"] and res["dryRun"]
    assert "preview" in res


def test_tool_compile_file_mutations_forbidden(project):
    dsl = {"columns": 1, "areas": [{"name": "А", "rows": [
        {"cells": [{"col": 1, "text": "x"}]}]}]}
    res = _run(mxl_tools.edtb_mxl_compile(
        "Report.Продажи", "Печать", dsl, strategy="file"))
    assert not res["ok"]
    assert "EDTB_ALLOW_FILE_MUTATIONS" in res["error"]


def test_tool_compile_file_strategy(project, monkeypatch):
    monkeypatch.setenv("EDTB_ALLOW_FILE_MUTATIONS", "1")
    dsl = {
        "columns": 2,
        "styles": {"b": {"border": "all"}},
        "areas": [{"name": "Строка", "rows": [
            {"rowStyle": "b", "cells": [{"col": 1, "param": "Товар"}]}]}],
    }
    res = _run(mxl_tools.edtb_mxl_compile(
        "Report.Продажи", "Печать", dsl, strategy="file"))
    assert res["ok"] and res["applied"]
    assert res["strategy"] == "file"
    assert any("resync" in w for w in res["warnings"])  # proxy отсутствует
    written = Path(res["file"]).read_text(encoding="utf-8")
    assert "<name>Строка</name>" in written
    assert "bordered" in _dsl(written)["styles"]
    # backup создан
    assert res["backup"] is None or Path(res["backup"]).is_file()


def test_tool_compile_auto_subset_to_edt_mcp(project):
    """Простой DSL (только текст/параметры) → payload modify_metadata."""
    dsl = {"columns": 1, "areas": [{"name": "А", "rows": [
        {"cells": [{"col": 1, "text": "x"}, {"col": 1, "param": "П"}]}]}]}
    res = _run(mxl_tools.edtb_mxl_compile(
        "Report.Продажи", "Печать", dsl, dryRun=True))
    assert res["strategy"] == "edt-mcp"
    payload = res["payload"]
    assert payload["objectName"] == "Report.Продажи.Template.Печать"
    cells = payload["properties"]["template"]["areas"][0]["rows"][0]["cells"]
    assert cells[0]["fillType"] == "Text"
    assert cells[1]["fillType"] == "Parameter"


def test_tool_compile_auto_full_dsl_to_file(project):
    dsl = {"columns": 1, "styles": {"b": {"border": "all"}},
           "areas": [{"name": "А", "rows": [
               {"cells": [{"col": 1, "style": "b", "text": "x"}]}]}]}
    res = _run(mxl_tools.edtb_mxl_compile(
        "Report.Продажи", "Печать", dsl, dryRun=True))
    assert res["strategy"] == "file"
    assert res["subsetReasons"]


def test_tool_compile_with_stubbed_proxy(project, monkeypatch):
    """Stub proxy.client.call_tool — проверка пути modify_metadata."""
    calls = []

    async def call_tool(name, args):
        calls.append((name, args))
        return {"ok": True}

    proxy_client = types.ModuleType("edt_bridge.proxy.client")
    proxy_client.call_tool = call_tool
    proxy_pkg = types.ModuleType("edt_bridge.proxy")
    monkeypatch.setitem(sys.modules, "edt_bridge.proxy", proxy_pkg)
    monkeypatch.setitem(sys.modules, "edt_bridge.proxy.client", proxy_client)

    dsl = {"columns": 1, "areas": [{"name": "А", "rows": [
        {"cells": [{"col": 1, "text": "x"}]}]}]}
    res = _run(mxl_tools.edtb_mxl_compile("Report.Продажи", "Печать", dsl))
    assert res["ok"] and res["applied"]
    assert calls and calls[0][0] == "modify_metadata"
