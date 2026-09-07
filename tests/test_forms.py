# -*- coding: utf-8 -*-
"""Unit-тесты M2: планировщик DSL и XML-патчи Form.form (без живого EDT)."""

from __future__ import annotations

import pytest

from edt_bridge.forms import form_dsl, form_xml
from edt_bridge.forms.forms import (
    edtb_form_compile,
    edtb_form_info,
    edtb_form_remove,
    form_file,
)


# ---------------------------------------------------------------------------
# Планировщик DSL
# ---------------------------------------------------------------------------

class TestCompileDsl:
    def test_basic_create_plan(self):
        dsl = {
            "title": "Загрузка",
            "properties": {"autoTitle": False},
            "events": {"OnCreateAtServer": "ПриСозданииНаСервере"},
            "elements": [
                {"input": "ИмяФайла", "path": "ИмяФайла",
                 "inputHint": "Выберите файл...", "on": ["StartChoice"]},
            ],
            "commands": [{"name": "Загрузить", "action": "ЗагрузитьОбработка"}],
        }
        res = form_dsl.compile_dsl("Catalog.Валюты", "ФормаЭлемента", dsl)
        assert res["ok"], res["errors"]
        edt = res["plan"]["viaEdtMcp"]
        assert edt[0]["tool"] == "create_metadata"
        assert edt[0]["args"]["fqn"] == "Catalog.Валюты.Form.ФормаЭлемента"
        # свойства формы
        modify_form = [c for c in edt if c["tool"] == "modify_metadata"
                       and c["args"]["fqn"].endswith("Form.ФормаЭлемента")]
        assert any(p["name"] == "autoTitle" and p["value"] is False
                   for c in modify_form for p in c["args"]["properties"])
        assert any(p["name"] == "synonym" and p["value"] == "Загрузка"
                   for c in modify_form for p in c["args"]["properties"])
        # обработчик события формы
        assert any(c["tool"] == "create_metadata" and c["args"]["fqn"].endswith(
            ".Handler.OnCreateAtServer") for c in edt)
        # команда + action
        assert any(c["args"]["fqn"].endswith(".Command.Загрузить") for c in edt)
        assert any(c["args"]["fqn"].endswith(
            ".Command.Загрузить.Handler.Action") for c in edt)
        # inputHint ушёл в файловый канал (GAP-FORM-PROPS)
        assert any(f["action"] == "setThinProperties"
                   and f["element"] == "ИмяФайла"
                   and "inputHint" in f["xml"]
                   for f in res["plan"]["viaFile"])

    def test_thin_props_to_file(self):
        dsl = {"elements": [
            {"input": "Поле", "path": "Поле", "titleLocation": "top"},
            {"pages": "Страницы", "pagesRepresentation": "None"},
        ]}
        res = form_dsl.compile_dsl("Catalog.Валюты", "Ф", dsl)
        assert res["ok"]
        file_actions = {(f["action"], f["element"])
                        for f in res["plan"]["viaFile"]}
        assert ("setThinProperties", "Поле") in file_actions
        assert ("setThinProperties", "Страницы") in file_actions

    def test_calltype_change_and_validate_file_only(self):
        dsl = {"events": {"OnOpen": {
            "handler": "Расш1_ПриОткрытии", "callType": "ChangeAndValidate"}}}
        res = form_dsl.compile_dsl("Document.Заказ", "ФормаДокумента", dsl)
        assert res["ok"]
        assert any(f["action"] == "setEventCallType"
                   and "ChangeAndValidate" in f["xml"]
                   for f in res["plan"]["viaFile"])
        assert not any("callType" in c["args"]
                       for c in res["plan"]["viaEdtMcp"])

    def test_calltype_after_via_edt(self):
        dsl = {"events": {"OnOpen": {
            "handler": "Расш1_ПриОткрытииПосле", "callType": "After"}}}
        res = form_dsl.compile_dsl("Document.Заказ", "ФормаДокумента", dsl)
        assert res["ok"]
        assert any(c["args"].get("callType") == "After"
                   for c in res["plan"]["viaEdtMcp"])

    def test_invalid_form_event(self):
        res = form_dsl.compile_dsl(
            "Catalog.Валюты", "Ф", {"events": {"NoSuchEvent": "Х"}})
        assert not res["ok"]
        assert any("NoSuchEvent" in e for e in res["errors"])

    def test_invalid_element_event(self):
        dsl = {"elements": [{"button": "Кнопка", "on": ["OnChange"]}]}
        res = form_dsl.compile_dsl("Catalog.Валюты", "Ф", dsl)
        assert not res["ok"]
        assert any("OnChange" in e and "button" in e for e in res["errors"])

    def test_event_autonaming(self):
        dsl = {"elements": [{"input": "Организация", "path": "Организация",
                             "on": ["OnChange"]}]}
        res = form_dsl.compile_dsl("Catalog.Валюты", "Ф", dsl)
        assert res["ok"]
        handler_call = [c for c in res["plan"]["viaEdtMcp"]
                        if c["args"]["fqn"].endswith(".Handler.OnChange")]
        assert handler_call
        assert handler_call[0]["args"]["properties"][0]["value"] == \
            "ОрганизацияOnChange"

    def test_preset_expansion(self):
        dsl = {"preset": "wizard", "title": "Мой мастер"}
        res = form_dsl.compile_dsl("DataProcessor.Мастер", "Форма", dsl)
        assert res["ok"]
        assert any("пресет" in w for w in res["warnings"])
        # элементы пресета: страницы + кнопки навигации
        fqns = [c["args"]["fqn"] for c in res["plan"]["viaEdtMcp"]]
        assert any(f.endswith(".Pages.СтраницыМастера") for f in fqns)
        assert any(f.endswith(".Button.Назад") for f in fqns)
        # явный title пользователя победил
        assert any(p["value"] == "Мой мастер"
                   for c in res["plan"]["viaEdtMcp"]
                   for p in c["args"].get("properties", []))

    def test_unknown_preset(self):
        res = form_dsl.compile_dsl("Catalog.Валюты", "Ф", {"preset": "nope"})
        assert not res["ok"]
        assert any("пресет" in e for e in res["errors"])

    def test_shorthand_keys(self):
        dsl = {"elements": [
            {"input": "Поле", "path": "Поле", "hidden": True},
            {"check": "Флаг", "path": "Флаг", "disabled": True},
        ]}
        res = form_dsl.compile_dsl("Catalog.Валюты", "Ф", dsl)
        assert res["ok"]
        assert any("hidden" in w for w in res["warnings"])
        props = {(c["args"]["fqn"], p["name"]): p["value"]
                 for c in res["plan"]["viaEdtMcp"]
                 for p in c["args"].get("properties", [])}
        assert props[("Catalog.Валюты.Form.Ф.Field.Поле", "visible")] is False
        assert props[("Catalog.Валюты.Form.Ф.Field.Флаг", "enabled")] is False

    def test_group_requires_name(self):
        res = form_dsl.compile_dsl(
            "Catalog.Валюты", "Ф",
            {"elements": [{"group": "horizontal"}]})
        assert not res["ok"]

    def test_unknown_key_warning(self):
        res = form_dsl.compile_dsl("Catalog.Валюты", "Ф", {"bogus": 1})
        assert res["ok"]
        assert any("bogus" in w for w in res["warnings"])

    def test_conditional_appearance_file(self):
        dsl = {"conditionalAppearance": [
            {"filter": "Сумма > 0", "fields": ["Сумма"],
             "appearance": {"textColor": "red"}},
        ]}
        res = form_dsl.compile_dsl("Catalog.Валюты", "Ф", dsl)
        assert res["ok"]
        frag = [f for f in res["plan"]["viaFile"]
                if f["action"] == "setConditionalAppearance"]
        assert frag and "conditionalAppearance" in frag[0]["xml"]

    def test_patch_mode_no_form_create(self):
        res = form_dsl.compile_dsl("Catalog.Валюты", "Ф", {}, mode="patch")
        assert res["ok"]
        assert not any(
            c["args"]["fqn"] == "Catalog.Валюты.Form.Ф"
            for c in res["plan"]["viaEdtMcp"])

    def test_params(self):
        dsl = {"params": [{"name": "Ключ", "key": True}]}
        res = form_dsl.compile_dsl("Catalog.Валюты", "Ф", dsl)
        assert res["ok"]
        assert any(c["args"]["fqn"].endswith(".Parameter.Ключ")
                   for c in res["plan"]["viaEdtMcp"])


# ---------------------------------------------------------------------------
# XML-патчи
# ---------------------------------------------------------------------------

class TestFormXml:
    def test_set_input_hint(self, form_xml_text):
        tree = form_xml.parse_form_xml(form_xml_text)
        assert form_xml.set_input_hint(tree, "Code", "Введите код...")
        assert form_xml.find_item(tree, "Code") is not None
        text = form_xml.serialize(tree)
        assert "<inputHint>Введите код...</inputHint>" in text
        # идемпотентно
        assert not form_xml.set_input_hint(tree, "Code", "Введите код...")

    def test_input_hint_missing_element(self, form_xml_text):
        tree = form_xml.parse_form_xml(form_xml_text)
        with pytest.raises(form_xml.FormXmlError):
            form_xml.set_input_hint(tree, "НетТакого", "x")

    def test_set_title_location(self, form_xml_text):
        tree = form_xml.parse_form_xml(form_xml_text)
        assert form_xml.set_title_location(tree, "Code", "top")
        text = form_xml.serialize(tree)
        assert "<titleLocation>Top</titleLocation>" in text
        with pytest.raises(form_xml.FormXmlError):
            form_xml.set_title_location(tree, "Code", "диагонально")

    def test_pages_representation(self, form_xml_text):
        tree = form_xml.parse_form_xml(form_xml_text)
        assert form_xml.set_pages_representation(tree, "GroupPages", "None")
        assert "<pagesRepresentation>None</pagesRepresentation>" in \
            form_xml.serialize(tree)

    def test_event_call_type(self, form_xml_text):
        tree = form_xml.parse_form_xml(form_xml_text)
        assert form_xml.set_event_call_type(
            tree, "OnOpen", "Расш1_ПриОткрытии", "ChangeAndValidate")
        text = form_xml.serialize(tree)
        assert "EventHandlerExtension" in text
        assert "<callType>ChangeAndValidate</callType>" in text
        # идемпотентно: тот же (event, callType) обновляет имя
        assert form_xml.set_event_call_type(
            tree, "OnOpen", "ДругойОбработчик", "ChangeAndValidate")
        assert not form_xml.set_event_call_type(
            tree, "OnOpen", "ДругойОбработчик", "ChangeAndValidate")

    def test_instead_maps_to_override(self, form_xml_text):
        tree = form_xml.parse_form_xml(form_xml_text)
        form_xml.set_event_call_type(tree, "OnClose", "Х", "Instead")
        assert "<callType>Override</callType>" in form_xml.serialize(tree)

    def test_conditional_appearance(self, form_xml_text):
        tree = form_xml.parse_form_xml(form_xml_text)
        assert form_xml.set_conditional_appearance(tree, [
            {"filter": "Object.Code = 1", "fields": ["Code"],
             "appearance": {"textColor": "red"}},
        ])
        text = form_xml.serialize(tree)
        assert "<conditionalAppearance>" in text
        assert 'key="textColor" value="red"' in text

    def test_apply_fragment_thin(self, form_xml_text):
        tree = form_xml.parse_form_xml(form_xml_text)
        res = form_xml.apply_fragment(tree, {
            "action": "setThinProperties", "element": "Code",
            "xml": "<inputHint>Подсказка</inputHint>\n"
                   "<titleLocation>Top</titleLocation>",
        })
        assert res["changed"]
        text = form_xml.serialize(tree)
        assert "<inputHint>Подсказка</inputHint>" in text
        assert "<titleLocation>Top</titleLocation>" in text

    def test_apply_fragment_calltype(self, form_xml_text):
        tree = form_xml.parse_form_xml(form_xml_text)
        res = form_xml.apply_fragment(tree, {
            "action": "setEventCallType", "element": None,
            "xml": '<handlers xsi:type="form:EventHandlerExtension">\n'
                   "  <event>OnClose</event>\n  <name>Х</name>\n"
                   "  <callType>ChangeAndValidate</callType>\n</handlers>",
        })
        assert res["changed"]

    def test_apply_fragment_unknown_action(self, form_xml_text):
        tree = form_xml.parse_form_xml(form_xml_text)
        with pytest.raises(form_xml.FormXmlError):
            form_xml.apply_fragment(tree, {"action": "bogus", "xml": ""})

    def test_parse_form_info(self, form_xml_text):
        info = form_xml.parse_form_info(form_xml.parse_form_xml(form_xml_text))
        names = {e["name"] for e in info["elements"]}
        assert {"Code", "GroupPages", "Page1"} <= names
        code = next(e for e in info["elements"] if e["name"] == "Code")
        assert code["dataPath"] == "Object.Code"
        assert any(c["name"] == "Load" and c["action"] == "LoadProcessing"
                   for c in info["commands"])
        assert any(p["name"] == "Key" and p["key"] for p in info["params"])
        assert any(e["event"] == "OnChange" and e["handler"] == "CodeOnChange"
                   and e["element"] == "Code" for e in info["events"])
        assert any(e["event"] == "OnOpen" and e["callType"] == "After"
                   and e["element"] is None for e in info["events"])


# ---------------------------------------------------------------------------
# Инструменты (со стабами ядра)
# ---------------------------------------------------------------------------

class TestFormTools:
    @pytest.mark.asyncio
    async def test_compile_dry_run(self, core_stubs):
        dsl = {"elements": [{"input": "Поле", "path": "Поле",
                             "inputHint": "хинт"}]}
        res = await edtb_form_compile("Catalog.Валюты", dsl,
                                      formName="Форма", dryRun=True)
        assert res["ok"] and not res["applied"] and res["dryRun"]
        assert res["planHash"]
        assert res["viaEdtMcp"] and res["viaFile"]
        assert isinstance(res["warnings"], list)

    @pytest.mark.asyncio
    async def test_compile_apply(self, core_stubs, project_with_form):
        dsl = {"elements": [{"input": "Code", "path": "Object.Code",
                             "titleLocation": "top",
                             "inputHint": "Код товара"}]}
        res = await edtb_form_compile(
            "Catalog.Товары", dsl, formName="ФормаЭлемента",
            mode="patch", dryRun=False,
            projectPath=str(project_with_form))
        assert res["ok"] and res["applied"]
        text = form_file(project_with_form, "Catalog.Товары",
                         "ФормаЭлемента").read_text(encoding="utf-8")
        assert "<inputHint>Код товара</inputHint>" in text
        assert "<titleLocation>Top</titleLocation>" in text
        assert any(f["changed"] for f in res["viaFile"])
        # create/modify прошли через proxy
        assert core_stubs["client"].calls
        assert core_stubs["resync"].called >= 1

    @pytest.mark.asyncio
    async def test_info(self, core_stubs, project_with_form):
        res = await edtb_form_info("Catalog.Товары", "ФормаЭлемента",
                                   projectPath=str(project_with_form))
        assert res["ok"]
        assert {e["name"] for e in res["elements"]} >= {"Code", "GroupPages"}
        assert res["commands"] and res["params"] and res["events"]

    @pytest.mark.asyncio
    async def test_info_missing(self, core_stubs, tmp_path):
        res = await edtb_form_info("Catalog.Валюты", "Нет",
                                   projectPath=str(tmp_path))
        assert not res["ok"]
        assert res["warnings"] == []

    @pytest.mark.asyncio
    async def test_remove_two_phase(self, core_stubs, project_with_form):
        core_stubs["client"].search_results = [
            {"module": "Модуль", "line": 1}]
        phase1 = await edtb_form_remove("Catalog.Товары", "ФормаЭлемента",
                                        projectPath=str(project_with_form))
        assert phase1["ok"] and not phase1["removed"]
        assert phase1["references"]
        assert any("ссылок" in w for w in phase1["warnings"])
        token = phase1["confirmToken"]

        wrong = await edtb_form_remove("Catalog.Товары", "ФормаЭлемента",
                                       confirmToken="неверно", dryRun=False,
                                       projectPath=str(project_with_form))
        assert not wrong["removed"]

        phase2 = await edtb_form_remove("Catalog.Товары", "ФормаЭлемента",
                                        confirmToken=token, dryRun=False,
                                        projectPath=str(project_with_form))
        assert phase2["removed"]
