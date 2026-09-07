"""Модуль BSP (M4): скаффолдинг внешних обработок/отчётов и справка.

Закрывает гэпы GAP-EPF-ROOT (корневой .mdo внешнего объекта) и
GAP-HELP-ADD (Help/<lang>.html + IncludeHelpInContents).

Все пишущие операции следуют SPEC (раздел 5): файловые мутации только при
EDTB_ALLOW_FILE_MUTATIONS=1, dryRun возвращает план без применения, после
файловых правок выполняется resync через proxy (или warning).
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from edt_bridge.bsp import _core

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

_KIND_DIRS = {
    "ExternalDataProcessor": "ExternalDataProcessors",
    "ExternalReport": "ExternalReports",
}

_BSP_KIND_API = {
    "ExternalDataProcessor": "ВидОбработкиДополнительнаяОбработка()",
    "ExternalReport": "ВидОбработкиДополнительныйОтчет()",
}

_MAIN_ATTRIBUTE_TYPE = {
    "ExternalDataProcessor": "ExternalDataProcessorObject.{name}",
    "ExternalReport": "ExternalReportObject.{name}",
}

_FORM_NAME = "Форма"

_NAME_RE = re.compile(r"^[A-Za-zА-Яа-я_][A-Za-zА-Яа-я0-9_]*$")


def _load_template(template_name: str) -> str:
    """Прочитать шаблон из каталога templates/."""
    path = _TEMPLATES_DIR / template_name
    return path.read_text(encoding="utf-8")


def _render_root_mdo(name: str, kind: str) -> str:
    """Сгенерировать корневой .mdo внешнего объекта по шаблону (uuid4)."""
    template = _load_template(f"{kind}.mdo.tmpl")
    replacements = {
        "UUID_ROOT": str(uuid.uuid4()),
        "UUID_PT_ID": str(uuid.uuid4()),
        "UUID_PT_VT": str(uuid.uuid4()),
        "UUID_PM_ID": str(uuid.uuid4()),
        "UUID_PM_VT": str(uuid.uuid4()),
        "UUID_CO_CLASS": str(uuid.uuid4()),
        "UUID_CO_OBJECT": str(uuid.uuid4()),
        "UUID_FORM": str(uuid.uuid4()),
        "NAME": name,
    }
    for key, value in replacements.items():
        template = template.replace("{" + key + "}", value)
    return template


def _render_object_module(kind: str, with_bsp_modules: bool) -> str:
    """Сгенерировать ObjectModule.bsl: стаб БСП или пустой каркас областей."""
    if not with_bsp_modules:
        return _load_template("ObjectModule.plain.bsl.tmpl")
    template = _load_template("ObjectModule.bsp.bsl.tmpl")
    return template.replace("{BSP_KIND_API}", _BSP_KIND_API[kind])


def _render_form(name: str, kind: str) -> str:
    """Сгенерировать stub формы (Form.form) с основным реквизитом Объект."""
    template = _load_template("Form.form.tmpl")
    return template.replace(
        "{MAIN_ATTRIBUTE_TYPE}", _MAIN_ATTRIBUTE_TYPE[kind].format(name=name)
    )


def _register_in_configuration(project_root: Path, name: str, kind: str) -> str | None:
    """Зарегистрировать объект в Configuration.mdo (секция externalObjects).

    Возвращает warning или None. Правка текстовая: вставка
    ``<externalObjects>{kind}.{name}</externalObjects>``.
    """
    config_mdo = project_root / "src" / "Configuration" / "Configuration.mdo"
    if not config_mdo.exists():
        # Альтернативная раскладка: Configuration.mdo в корне src/
        config_mdo = project_root / "src" / "Configuration.mdo"
    if not config_mdo.exists():
        return (
            "Configuration.mdo не найден — регистрация в externalObjects "
            "пропущена; зарегистрируйте объект в EDT вручную"
        )
    content = config_mdo.read_text(encoding="utf-8")
    entry = f"  <externalObjects>{kind}.{name}</externalObjects>\n"
    if f"<externalObjects>{kind}.{name}</externalObjects>" in content:
        return None  # уже зарегистрирован
    _core.backup_file(config_mdo, project_root)
    if "</mdclass:Configuration>" in content:
        content = content.replace(
            "</mdclass:Configuration>",
            entry + "</mdclass:Configuration>",
            1,
        )
    else:
        content = content.rstrip("\n") + "\n" + entry
    config_mdo.write_text(content, encoding="utf-8")
    return None


def edtb_epf_scaffold(
    name: str,
    kind: str = "ExternalDataProcessor",
    withBspModules: bool = True,
    projectPath: str | None = None,
    dryRun: bool = False,
) -> dict:
    """Сгенерировать каркас внешней обработки/отчёта (GAP-EPF-ROOT).

    Создаёт в ``src/ExternalDataProcessors|ExternalReports/<name>/``:
    корневой ``<name>.mdo`` (валидный EDT XML, producedTypes/
    containedObjects, uuid4), ``ObjectModule.bsl`` (стабы БСП
    ``СведенияОВнешнейОбработке`` при withBspModules), stub формы
    ``Forms/Форма/Form.form`` + ``Module.bsl``. Регистрирует объект в
    Configuration.mdo (externalObjects), затем resync.

    Параметры:
        name: имя объекта (идентификатор 1С).
        kind: "ExternalDataProcessor" или "ExternalReport".
        withBspModules: добавить ли БСП-стабы в модуль объекта.
        projectPath: корень EDT-проекта (иначе EDTB_PROJECT_PATH).
        dryRun: вернуть план без применения.

    Возвращает:
        dict с полями ok, plan/files, warnings.
    """
    warnings: list[str] = []
    if not _NAME_RE.match(name or ""):
        return {
            "ok": False,
            "error": f"Некорректное имя объекта: {name!r} (нужен идентификатор 1С)",
            "warnings": warnings,
        }
    if kind not in _KIND_DIRS:
        return {
            "ok": False,
            "error": f"Неизвестный kind: {kind!r}; допустимо: {sorted(_KIND_DIRS)}",
            "warnings": warnings,
        }

    project_root = _core.resolve_project_path(projectPath)
    if project_root is None:
        return {
            "ok": False,
            "error": "Не задан путь проекта (projectPath или EDTB_PROJECT_PATH)",
            "warnings": warnings,
        }

    base_dir = project_root / "src" / _KIND_DIRS[kind]
    object_dir = base_dir / name
    form_dir = object_dir / "Forms" / _FORM_NAME

    files = {
        str(base_dir / f"{name}.mdo"): _render_root_mdo(name, kind),
        str(object_dir / "ObjectModule.bsl"): _render_object_module(
            kind, withBspModules
        ),
        str(form_dir / "Form.form"): _render_form(name, kind),
        str(form_dir / "Module.bsl"): _load_template("FormModule.bsl.tmpl"),
    }

    existing = [path for path in files if Path(path).exists()]
    if existing:
        warnings.append(f"Файлы уже существуют и будут перезаписаны: {existing}")

    plan = {
        "action": "epf_scaffold",
        "name": name,
        "kind": kind,
        "withBspModules": withBspModules,
        "files": sorted(files),
        "registerInConfigurationMdo": True,
    }
    if dryRun:
        return {"ok": True, "dryRun": True, "plan": plan, "warnings": warnings}

    if not _core.file_mutations_allowed():
        return {
            "ok": False,
            "error": (
                "Файловые мутации запрещены: установите "
                "EDTB_ALLOW_FILE_MUTATIONS=1 или используйте dryRun"
            ),
            "plan": plan,
            "warnings": warnings,
        }

    warnings.extend(_core.git_dirty_warnings(project_root))

    for path, content in files.items():
        target = Path(path)
        _core.backup_file(target, project_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    registration_warning = _register_in_configuration(project_root, name, kind)
    if registration_warning:
        warnings.append(registration_warning)

    warnings.extend(_core.resync_after_file_changes(project_root))

    return {"ok": True, "plan": plan, "warnings": warnings}


def edtb_help_add(
    objectName: str,
    lang: str = "ru",
    html: str = "",
    updateForms: bool = True,
    projectPath: str | None = None,
    dryRun: bool = False,
) -> dict:
    """Добавить встроенную справку объекту (GAP-HELP-ADD).

    Создаёт ``Help/<lang>.html`` в каталоге объекта проекта. При
    updateForms=True пробует выставить IncludeHelpInContents у форм объекта
    через modify_metadata (proxy); при недоступности EDT-MCP — warning с
    указанием установить флаг в EDT UI.

    Параметры:
        objectName: FQN объекта, например ``Catalog.МойСправочник`` или
            ``ExternalDataProcessor.МояОбработка``.
        lang: код языка справки (по умолчанию "ru").
        html: содержимое страницы справки; если пусто — генерируется stub.
        updateForms: обновлять ли IncludeHelpInContents у форм объекта.
        projectPath: корень EDT-проекта (иначе EDTB_PROJECT_PATH).
        dryRun: вернуть план без применения.

    Возвращает:
        dict с полями ok, helpFile, forms, warnings.
    """
    warnings: list[str] = []
    parts = (objectName or "").split(".")
    if len(parts) < 2:
        return {
            "ok": False,
            "error": (
                f"Некорректный objectName: {objectName!r}; ожидается FQN "
                "вида 'Catalog.МойСправочник'"
            ),
            "warnings": warnings,
        }
    object_type, object_name = parts[0], parts[1]
    plural = _KIND_DIRS.get(object_type, object_type + "s")

    project_root = _core.resolve_project_path(projectPath)
    if project_root is None:
        return {
            "ok": False,
            "error": "Не задан путь проекта (projectPath или EDTB_PROJECT_PATH)",
            "warnings": warnings,
        }

    object_dir = project_root / "src" / plural / object_name
    help_file = object_dir / "Help" / f"{lang}.html"
    if not html:
        html = (
            "<html><head><meta charset=\"utf-8\"></head><body>\n"
            f"<h1>{object_name}</h1>\n"
            "<p>TODO: описание объекта.</p>\n"
            "</body></html>\n"
        )
        warnings.append("html не задан — создана заглушка справки")

    plan = {
        "action": "help_add",
        "objectName": objectName,
        "helpFile": str(help_file),
        "lang": lang,
        "updateForms": updateForms,
    }
    if help_file.exists():
        warnings.append(f"Файл справки уже существует: {help_file} — будет перезаписан")
    if dryRun:
        return {"ok": True, "dryRun": True, "plan": plan, "warnings": warnings}

    if not _core.file_mutations_allowed():
        return {
            "ok": False,
            "error": (
                "Файловые мутации запрещены: установите "
                "EDTB_ALLOW_FILE_MUTATIONS=1 или используйте dryRun"
            ),
            "plan": plan,
            "warnings": warnings,
        }

    warnings.extend(_core.git_dirty_warnings(project_root))
    _core.backup_file(help_file, project_root)
    help_file.parent.mkdir(parents=True, exist_ok=True)
    help_file.write_text(html, encoding="utf-8")

    forms_updated: list[str] = []
    if updateForms:
        forms_dir = object_dir / "Forms"
        form_names = (
            sorted(p.name for p in forms_dir.iterdir() if p.is_dir())
            if forms_dir.is_dir()
            else []
        )
        for form_name in form_names:
            fqn = f"{objectName}.Form.{form_name}"
            ok, response = _core.proxy_call(
                "modify_metadata",
                {
                    "fqn": fqn,
                    "projectPath": str(project_root),
                    "properties": [
                        {"name": "includeHelpInContents", "value": True}
                    ],
                },
            )
            if ok:
                forms_updated.append(fqn)
            else:
                warnings.append(
                    f"IncludeHelpInContents для формы {fqn} не установлен "
                    f"через EDT-MCP ({response.get('error', 'нет ответа')}) — "
                    "установите флаг в EDT UI"
                )

    warnings.extend(_core.resync_after_file_changes(project_root))

    return {
        "ok": True,
        "helpFile": str(help_file),
        "forms": forms_updated,
        "warnings": warnings,
    }
