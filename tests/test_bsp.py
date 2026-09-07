"""Unit-тесты модуля BSP: шаблоны EPF/ERF и добавление справки."""

import uuid
import xml.etree.ElementTree as ET

import pytest

from edt_bridge.bsp.bsp import (
    _render_form,
    _render_object_module,
    _render_root_mdo,
    edtb_epf_scaffold,
    edtb_help_add,
)


# --- генерация шаблонов -----------------------------------------------------


@pytest.mark.parametrize("kind", ["ExternalDataProcessor", "ExternalReport"])
def test_root_mdo_valid_xml(kind):
    """Корневой .mdo — валидный XML с producedTypes/containedObjects/uuid."""
    xml = _render_root_mdo("ТестОбъект", kind)
    root = ET.fromstring(xml)
    assert root.tag.endswith(kind)
    assert "producedTypes" in xml
    assert "containedObjects" in xml
    assert "<name>ТестОбъект</name>" in xml
    # Все uuid — корректные uuid4, плейсхолдеров не осталось.
    assert "{" not in xml
    uuid.UUID(root.attrib["uuid"])  # не должно бросить


def test_root_mdo_uuid_unique():
    """UUID в шаблоне генерируются заново при каждом вызове."""
    first = _render_root_mdo("А", "ExternalDataProcessor")
    second = _render_root_mdo("А", "ExternalDataProcessor")
    assert first != second


def test_object_module_bsp_dp():
    """Стаб БСП для обработки: ВидДополнительнаяОбработка, команды, назначение."""
    code = _render_object_module("ExternalDataProcessor", True)
    assert "Функция СведенияОВнешнейОбработке() Экспорт" in code
    assert "ВидОбработкиДополнительнаяОбработка()" in code
    assert "ПараметрыРегистрации.Команды.Добавить()" in code
    assert "СведенияОВнешнейОбработке(\"2.2.2.1\")" in code


def test_object_module_bsp_report():
    """Стаб БСП для отчёта: ВидДополнительныйОтчет."""
    code = _render_object_module("ExternalReport", True)
    assert "ВидОбработкиДополнительныйОтчет()" in code


def test_object_module_plain():
    """Без БСП — пустой каркас областей, без СведенияОВнешнейОбработке."""
    code = _render_object_module("ExternalDataProcessor", False)
    assert "СведенияОВнешнейОбработке" not in code
    assert "#Область" in code


def test_form_stub_valid_xml():
    """Stub формы — валидный XML с основным реквизитом Объект."""
    xml = _render_form("МояОбработка", "ExternalDataProcessor")
    ET.fromstring(xml)
    assert "ExternalDataProcessorObject.МояОбработка" in xml


# --- edtb_epf_scaffold ------------------------------------------------------


def _make_project(tmp_path):
    (tmp_path / "src" / "Configuration").mkdir(parents=True)
    (tmp_path / "src" / "Configuration" / "Configuration.mdo").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<mdclass:Configuration xmlns:mdclass="http://g5.1c.ru/v8/dt/metadata/mdclass">\n'
        "</mdclass:Configuration>\n",
        encoding="utf-8",
    )
    return tmp_path


def test_scaffold_dry_run(tmp_path):
    """dryRun возвращает план и не создаёт файлы."""
    project = _make_project(tmp_path)
    result = edtb_epf_scaffold("МояОбработка", projectPath=str(project), dryRun=True)
    assert result["ok"] and result["dryRun"]
    assert not (project / "src" / "ExternalDataProcessors").exists()
    assert any(p.endswith("МояОбработка.mdo") for p in result["plan"]["files"])


def test_scaffold_forbidden_without_env(tmp_path, monkeypatch):
    """Без EDTB_ALLOW_FILE_MUTATIONS=1 мутация запрещена."""
    monkeypatch.delenv("EDTB_ALLOW_FILE_MUTATIONS", raising=False)
    project = _make_project(tmp_path)
    result = edtb_epf_scaffold("МояОбработка", projectPath=str(project))
    assert not result["ok"]
    assert "EDTB_ALLOW_FILE_MUTATIONS" in result["error"]


def test_scaffold_files_created(tmp_path, monkeypatch):
    """Полный скаффолдинг: .mdo, ObjectModule.bsl, форма, регистрация."""
    monkeypatch.setenv("EDTB_ALLOW_FILE_MUTATIONS", "1")
    project = _make_project(tmp_path)
    result = edtb_epf_scaffold(
        "МойОтчет", kind="ExternalReport", projectPath=str(project)
    )
    assert result["ok"]
    base = project / "src" / "ExternalReports" / "МойОтчет"
    assert (project / "src" / "ExternalReports" / "МойОтчет.mdo").exists()
    assert (base / "ObjectModule.bsl").exists()
    assert (base / "Forms" / "Форма" / "Form.form").exists()
    assert (base / "Forms" / "Форма" / "Module.bsl").exists()
    config = (project / "src" / "Configuration" / "Configuration.mdo").read_text(
        encoding="utf-8"
    )
    assert "<externalObjects>ExternalReport.МойОтчет</externalObjects>" in config
    # EDT-MCP в тестах недоступен — warning о ручном resync.
    assert any("resync" in w.lower() for w in result["warnings"])


def test_scaffold_invalid_name(tmp_path):
    result = edtb_epf_scaffold("1невалидно!", projectPath=str(tmp_path))
    assert not result["ok"]


def test_scaffold_invalid_kind(tmp_path):
    result = edtb_epf_scaffold("Норм", kind="Catalog", projectPath=str(tmp_path))
    assert not result["ok"]


# --- edtb_help_add ----------------------------------------------------------


def test_help_add_creates_file(tmp_path, monkeypatch):
    """Справка создаёт Help/<lang>.html в каталоге объекта."""
    monkeypatch.setenv("EDTB_ALLOW_FILE_MUTATIONS", "1")
    project = _make_project(tmp_path)
    obj_dir = project / "src" / "Catalogs" / "МойСправочник"
    obj_dir.mkdir(parents=True)
    result = edtb_help_add(
        "Catalog.МойСправочник",
        lang="ru",
        html="<html><body><h1>Справка</h1></body></html>",
        projectPath=str(project),
    )
    assert result["ok"]
    help_file = obj_dir / "Help" / "ru.html"
    assert help_file.exists()
    assert "<h1>Справка</h1>" in help_file.read_text(encoding="utf-8")


def test_help_add_stub_html_and_dry_run(tmp_path):
    """Пустой html → заглушка + warning; dryRun ничего не пишет."""
    project = _make_project(tmp_path)
    result = edtb_help_add("Catalog.МойСправочник", projectPath=str(project), dryRun=True)
    assert result["ok"] and result["dryRun"]
    assert any("заглушка" in w for w in result["warnings"])
    assert not (project / "src" / "Catalogs").exists()


def test_help_add_bad_fqn(tmp_path):
    result = edtb_help_add("БезТочки", projectPath=str(tmp_path))
    assert not result["ok"]
