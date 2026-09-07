"""Unit-тесты расширений: разбор BSL-методов, перехватчики, роль."""

from pathlib import Path

import pytest

from edt_bridge.extensions.extensions import (
    _extract_method,
    _param_names,
    edtb_cfe_borrow_method,
    edtb_cfe_init_role,
    render_interceptor,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "base_module.bsl"


@pytest.fixture
def source():
    return FIXTURE.read_text(encoding="utf-8")


# --- разбор методов ---------------------------------------------------------


def test_extract_procedure(source):
    """Процедура с директивой, параметрами и Экспорт разбирается полностью."""
    method = _extract_method(source, "ОбновитьДанные")
    assert method is not None
    assert method.keyword == "Процедура"
    assert method.params == "Параметр1, Знач Параметр2 = 5"
    assert method.is_export
    assert method.directives == ["&НаСервере"]
    assert "ЗаписьЖурналаРегистрации(Параметр1);" in method.inner_body
    assert method.body.endswith("КонецПроцедуры")


def test_extract_function_multiline_header(source):
    """Многострочный заголовок функции склеивается, тело отделяется верно."""
    method = _extract_method(source, "ПолучитьВерсию")
    assert method is not None
    assert method.keyword == "Функция"
    assert 'Знач Режим = "Полный"' in method.params
    assert method.is_export
    assert method.directives == ["&НаСервереБезКонтекста"]
    assert method.inner_body.strip() == 'Возврат "1.0";'


def test_extract_missing(source):
    assert _extract_method(source, "НетТакогоМетода") is None


def test_param_names():
    assert _param_names("Параметр1, Знач Параметр2 = 5") == ["Параметр1", "Параметр2"]
    assert _param_names("") == []


# --- генерация перехватчиков ------------------------------------------------


def test_interceptor_after(source):
    """&После: сигнатура повторяет оригинал, имя с префиксом расширения."""
    method = _extract_method(source, "ОбновитьДанные")
    code = render_interceptor(method, "After", "Расш1_ОбновитьДанные")
    assert code.startswith("&НаСервере\n&После(\"ОбновитьДанные\")")
    assert "Процедура Расш1_ОбновитьДанные(Параметр1, Знач Параметр2 = 5) Экспорт" in code
    assert code.rstrip().endswith("КонецПроцедуры")


def test_interceptor_before_and_instead(source):
    method = _extract_method(source, "ОбновитьДанные")
    before = render_interceptor(method, "Before", "Расш1_ОбновитьДанные")
    assert '&Перед("ОбновитьДанные")' in before
    instead = render_interceptor(method, "Instead", "Расш1_ОбновитьДанные")
    assert '&Вместо("ОбновитьДанные")' in instead
    assert "ПродолжитьВызов(Параметр1, Параметр2)" in instead


def test_interceptor_change_and_validate(source):
    """&ИзменениеИКонтроль: тело оригинала копируется в перехватчик."""
    method = _extract_method(source, "ПолучитьВерсию")
    code = render_interceptor(method, "ChangeAndValidate", "Расш1_ПолучитьВерсию")
    assert '&ИзменениеИКонтроль("ПолучитьВерсию")' in code
    assert 'Возврат "1.0";' in code
    assert code.rstrip().endswith("КонецФункции")


def test_interceptor_bad_mode(source):
    method = _extract_method(source, "ОбновитьДанные")
    with pytest.raises(ValueError):
        render_interceptor(method, "Turbo", "Х")


# --- edtb_cfe_borrow_method -------------------------------------------------


def _make_projects(tmp_path, fixture):
    base = tmp_path / "base"
    ext = tmp_path / "ext"
    module_rel = Path("src/CommonModules/РаботаСФайлами/Module.bsl")
    (base / module_rel.parent).mkdir(parents=True)
    (base / module_rel).write_text(fixture, encoding="utf-8")
    (ext / module_rel.parent).mkdir(parents=True)
    (ext / module_rel).write_text(fixture, encoding="utf-8")
    return base, ext


def test_borrow_dry_run(tmp_path, source):
    """dryRun возвращает план и текст перехватчика без записи."""
    base, ext = _make_projects(tmp_path, source)
    result = edtb_cfe_borrow_method(
        "CommonModule.РаботаСФайлами",
        "Module",
        "ОбновитьДанные",
        mode="After",
        extProjectPath=str(ext),
        baseProjectPath=str(base),
        dryRun=True,
    )
    assert result["ok"] and result["dryRun"]
    interceptor = result["plan"]["interceptor"]
    assert '&После("ОбновитьДанные")' in interceptor
    # Файл расширения не изменился.
    ext_module = ext / "src/CommonModules/РаботаСФайлами/Module.bsl"
    assert "&После" not in ext_module.read_text(encoding="utf-8")


def test_borrow_file_fallback(tmp_path, source, monkeypatch):
    """Без EDT-MCP перехватчик дописывается файлово + warning о resync."""
    monkeypatch.setenv("EDTB_ALLOW_FILE_MUTATIONS", "1")
    base, ext = _make_projects(tmp_path, source)
    result = edtb_cfe_borrow_method(
        "CommonModule.РаботаСФайлами",
        "Module",
        "ОбновитьДанные",
        mode="Before",
        newName="Расш1_ОбновитьДанные",
        extProjectPath=str(ext),
        baseProjectPath=str(base),
    )
    assert result["ok"]
    assert result["via"] == "file"
    ext_module = ext / "src/CommonModules/РаботаСФайлами/Module.bsl"
    content = ext_module.read_text(encoding="utf-8")
    assert '&Перед("ОбновитьДанные")' in content
    assert "Процедура Расш1_ОбновитьДанные(Параметр1, Знач Параметр2 = 5) Экспорт" in content
    assert any("resync" in w.lower() or "недоступен" in w for w in result["warnings"])


def test_borrow_method_not_found(tmp_path, source):
    base, ext = _make_projects(tmp_path, source)
    result = edtb_cfe_borrow_method(
        "CommonModule.РаботаСФайлами",
        "Module",
        "Несуществующий",
        extProjectPath=str(ext),
        baseProjectPath=str(base),
    )
    assert not result["ok"]
    assert "не найден" in result["error"]


def test_borrow_missing_module_file(tmp_path):
    ext = tmp_path / "ext"
    ext.mkdir()
    result = edtb_cfe_borrow_method(
        "CommonModule.Нет",
        "Module",
        "Метод",
        extProjectPath=str(ext),
    )
    assert not result["ok"]
    assert "не найден" in result["error"]


# --- edtb_cfe_init_role -----------------------------------------------------


def test_role_dry_run(tmp_path):
    result = edtb_cfe_init_role(extProjectPath=str(tmp_path), dryRun=True)
    assert result["ok"] and result["dryRun"]
    assert result["plan"]["roleName"] == "ОсновнаяРоль"


def test_role_file_fallback(tmp_path, monkeypatch):
    """Без EDT-MCP: файловый .mdo роли + регистрация в Configuration.mdo."""
    monkeypatch.setenv("EDTB_ALLOW_FILE_MUTATIONS", "1")
    project = tmp_path
    (project / "src" / "Configuration").mkdir(parents=True)
    (project / "src" / "Configuration" / "Configuration.mdo").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<mdclass:Configuration xmlns:mdclass="http://g5.1c.ru/v8/dt/metadata/mdclass">\n'
        "</mdclass:Configuration>\n",
        encoding="utf-8",
    )
    result = edtb_cfe_init_role("ОсновнаяРоль", extProjectPath=str(project))
    assert result["ok"]
    assert result["via"] == "file"
    role_file = project / "src" / "Roles" / "ОсновнаяРоль.mdo"
    assert role_file.exists()
    import xml.etree.ElementTree as ET

    ET.parse(role_file)  # валидный XML
    config = (project / "src" / "Configuration" / "Configuration.mdo").read_text(
        encoding="utf-8"
    )
    assert "<roles>Role.ОсновнаяРоль</roles>" in config


def test_role_invalid_name(tmp_path):
    result = edtb_cfe_init_role("плохое имя!", extProjectPath=str(tmp_path))
    assert not result["ok"]
