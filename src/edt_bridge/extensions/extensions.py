"""Модуль расширений конфигурации (M4).

Закрывает гэпы:
- GAP-CFE-BORROW-BSL — генерация перехватчиков методов (&Перед/&После/
  &Вместо/&ИзменениеИКонтроль) по исходнику базового модуля;
- GAP-CFE-NOROLE — создание «основной роли» расширения.

Интеграция с ядром edt-bridge — через edt_bridge.bsp._core (proxy.call_tool,
config, safety по контрактам SPEC); дублирования нет.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from edt_bridge.bsp import _core

# Аннотации перехвата по режимам SPEC 4.5.
_MODE_ANNOTATIONS = {
    "Before": "&Перед",
    "After": "&После",
    "Instead": "&Вместо",
    "ChangeAndValidate": "&ИзменениеИКонтроль",
}

# Пути модулей относительно каталога объекта: moduleType -> файл.
_MODULE_FILES = {
    "ObjectModule": "ObjectModule.bsl",
    "ManagerModule": "ManagerModule.bsl",
    "RecordSetModule": "RecordSetModule.bsl",
    "Module": "Module.bsl",  # общие модули
}

_PLURALS = {
    "Catalog": "Catalogs",
    "Document": "Documents",
    "DataProcessor": "DataProcessors",
    "Report": "Reports",
    "CommonModule": "CommonModules",
    "ExternalDataProcessor": "ExternalDataProcessors",
    "ExternalReport": "ExternalReports",
}

_NAME_RE = re.compile(r"^[A-Za-zА-Яа-я_][A-Za-zА-Яа-я0-9_]*$")

_METHOD_HEADER_RE = re.compile(
    r"^(?P<keyword>Процедура|Функция|Procedure|Function)\s+"
    r"(?P<name>[A-Za-zА-Яа-я_][A-Za-zА-Яа-я0-9_]*)\s*"
    r"\((?P<params>.*)",
    re.IGNORECASE,
)

_METHOD_END_RE = re.compile(
    r"^\s*(КонецПроцедуры|КонецФункции|EndProcedure|EndFunction)\b", re.IGNORECASE
)


@dataclass
class BslMethod:
    """Разобранный метод BSL-модуля."""

    name: str
    keyword: str  # Процедура | Функция
    params: str  # исходный текст параметров без скобок
    is_export: bool
    directives: list[str] = field(default_factory=list)  # &НаСервере и т.п.
    body: str = ""  # полный текст метода с заголовком и концом
    inner_body: str = ""  # тело без заголовка и строки конца

    @property
    def end_keyword(self) -> str:
        return "КонецФункции" if self.keyword == "Функция" else "КонецПроцедуры"


def _extract_method(source: str, method_name: str) -> BslMethod | None:
    """Найти метод в исходнике .bsl и разобрать сигнатуру.

    Захватывает предшествующие строки аннотаций/директив (&...).
    Параметры могут занимать несколько строк — склеиваются до закрывающей
    скобки заголовка.
    """
    lines = source.splitlines()
    for index, line in enumerate(lines):
        match = _METHOD_HEADER_RE.match(line.strip())
        if not match or match.group("name") != method_name:
            continue

        # Директивы компиляции непосредственно над заголовком.
        directives: list[str] = []
        cursor = index - 1
        while cursor >= 0 and lines[cursor].strip().startswith("&"):
            directives.insert(0, lines[cursor].strip())
            cursor -= 1

        # Склейка многострочного заголовка до баланса скобок.
        header = line.strip()
        balance = header.count("(") - header.count(")")
        end_header = index
        while balance > 0 and end_header + 1 < len(lines):
            end_header += 1
            header += " " + lines[end_header].strip()
            balance += lines[end_header].count("(") - lines[end_header].count(")")

        params_match = re.search(r"\((?P<params>.*)\)", header)
        params = params_match.group("params") if params_match else ""
        is_export = bool(re.search(r"\)\s*Экспорт\b|\)\s*Export\b", header))

        # Полное тело метода до конца.
        body_lines = list(lines[index : end_header + 1])
        tail = end_header + 1
        while tail < len(lines):
            body_lines.append(lines[tail])
            if _METHOD_END_RE.match(lines[tail]):
                break
            tail += 1
        inner_lines = lines[end_header + 1 : tail]

        return BslMethod(
            name=method_name,
            keyword="Функция"
            if match.group("keyword").lower() in ("функция", "function")
            else "Процедура",
            params=params.strip(),
            is_export=is_export,
            directives=directives,
            body="\n".join(body_lines),
            inner_body="\n".join(inner_lines),
        )
    return None


def render_interceptor(
    method: BslMethod,
    mode: str,
    new_name: str,
) -> str:
    """Сгенерировать текст перехватчика для заимствованного метода.

    mode: Before|After|Instead|ChangeAndValidate. Сигнатура (параметры и
    контекстные директивы) повторяет оригинал — требование платформы для
    &Перед/&После. Для &ИзменениеИКонтроль тело копируется целиком.
    """
    if mode not in _MODE_ANNOTATIONS:
        raise ValueError(
            f"Неизвестный режим перехвата: {mode!r}; допустимо: "
            f"{sorted(_MODE_ANNOTATIONS)}"
        )
    annotation = _MODE_ANNOTATIONS[mode]

    lines: list[str] = list(method.directives)
    lines.append(f'{annotation}("{method.name}")')
    export_suffix = " Экспорт" if method.is_export else ""
    lines.append(f"{method.keyword} {new_name}({method.params}){export_suffix}")
    lines.append("")
    if mode == "ChangeAndValidate":
        # Тело оригинала копируется в расширение для контроля изменений.
        inner = method.inner_body.splitlines() if method.inner_body else []
        lines.extend(inner if inner else ["\t// Тело оригинального метода не извлечено"])
    elif mode == "Instead":
        call_args = ", ".join(
            _param_names(method.params)
        )
        lines.append("\t// Полная замена оригинального метода.")
        lines.append(f"\t// Вызов оригинала: ПродолжитьВызов({call_args});")
    else:
        lines.append("\t// TODO: код перехватчика")
    lines.append("")
    lines.append(method.end_keyword)
    return "\n".join(lines) + "\n"


def _param_names(params: str) -> list[str]:
    """Извлечь имена параметров из сигнатуры (для ПродолжитьВызов)."""
    names: list[str] = []
    for chunk in params.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        chunk = re.sub(r"^(Знач|Val)\s+", "", chunk)
        name = chunk.split("=")[0].strip()
        if name:
            names.append(name)
    return names


def _module_file_path(
    project_root: Path, object_name: str, module_type: str, form_name: str | None
) -> Path:
    """Путь к файлу модуля в EDT-проекте."""
    parts = object_name.split(".")
    object_type, base_name = parts[0], parts[1]
    plural = _PLURALS.get(object_type, object_type + "s")
    object_dir = project_root / "src" / plural / base_name
    if module_type == "FormModule":
        if not form_name:
            raise ValueError("Для FormModule требуется formName")
        return object_dir / "Forms" / form_name / "Module.bsl"
    file_name = _MODULE_FILES.get(module_type)
    if file_name is None:
        raise ValueError(
            f"Неизвестный moduleType: {module_type!r}; допустимо: "
            f"{sorted(_MODULE_FILES)} или FormModule"
        )
    return object_dir / file_name


def edtb_cfe_borrow_method(
    baseObjectName: str,
    moduleType: str,
    methodName: str,
    mode: str = "After",
    newName: str | None = None,
    extProjectPath: str | None = None,
    baseProjectPath: str | None = None,
    formName: str | None = None,
    dryRun: bool = False,
) -> dict:
    """Заимствование BSL-метода: перехватчик в расширении (GAP-CFE-BORROW-BSL).

    Читает метод из .bsl базового проекта, генерирует перехватчик с
    корректной сигнатурой (&Перед/&После/&Вместо/&ИзменениеИКонтроль) и
    вставляет в модуль расширения: через write_module_source (proxy),
    при недоступности EDT-MCP — файлово (дописывание в .bsl) + resync.

    Параметры:
        baseObjectName: FQN объекта базовой конфигурации
            (``CommonModule.РаботаСФайлами``, ``Catalog.Контрагенты``).
        moduleType: ObjectModule|ManagerModule|RecordSetModule|Module|FormModule.
        methodName: имя перехватываемого метода.
        mode: Before|After|Instead|ChangeAndValidate.
        newName: имя перехватчика (по умолчанию ``Расш_<ИмяМетода>``).
        extProjectPath: корень проекта расширения (иначе EDTB_PROJECT_PATH).
        baseProjectPath: корень базового проекта (иначе = extProjectPath).
        formName: имя формы для moduleType=FormModule.
        dryRun: вернуть план и текст перехватчика без применения.

    Возвращает:
        dict с полями ok, interceptor, target, via, warnings.
    """
    warnings: list[str] = []
    if mode not in _MODE_ANNOTATIONS:
        return {
            "ok": False,
            "error": f"Неизвестный mode: {mode!r}; допустимо: {sorted(_MODE_ANNOTATIONS)}",
            "warnings": warnings,
        }
    if not _NAME_RE.match(methodName or ""):
        return {
            "ok": False,
            "error": f"Некорректное имя метода: {methodName!r}",
            "warnings": warnings,
        }
    new_name = newName or f"Расш_{methodName}"
    if not _NAME_RE.match(new_name):
        return {
            "ok": False,
            "error": f"Некорректное имя перехватчика: {new_name!r}",
            "warnings": warnings,
        }

    ext_root = _core.resolve_project_path(extProjectPath)
    if ext_root is None:
        return {
            "ok": False,
            "error": "Не задан путь проекта расширения (extProjectPath или EDTB_PROJECT_PATH)",
            "warnings": warnings,
        }
    base_root = _core.resolve_project_path(baseProjectPath) or ext_root
    if base_root == ext_root and not baseProjectPath:
        warnings.append(
            "baseProjectPath не задан — метод ищется в проекте расширения "
            "(ожидается заимствованный модуль)"
        )

    try:
        base_module = _module_file_path(base_root, baseObjectName, moduleType, formName)
        ext_module = _module_file_path(ext_root, baseObjectName, moduleType, formName)
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "warnings": warnings}

    if not base_module.exists():
        return {
            "ok": False,
            "error": f"Файл базового модуля не найден: {base_module}",
            "warnings": warnings,
        }
    method = _extract_method(base_module.read_text(encoding="utf-8"), methodName)
    if method is None:
        return {
            "ok": False,
            "error": (
                f"Метод {methodName!r} не найден в {base_module}; "
                "проверьте имя и moduleType"
            ),
            "warnings": warnings,
        }

    interceptor = render_interceptor(method, mode, new_name)

    plan = {
        "action": "cfe_borrow_method",
        "baseObjectName": baseObjectName,
        "moduleType": moduleType,
        "methodName": methodName,
        "mode": mode,
        "newName": new_name,
        "target": str(ext_module),
        "interceptor": interceptor,
    }
    if dryRun:
        return {"ok": True, "dryRun": True, "plan": plan, "warnings": warnings}

    # Стратегия 1: запись через EDT-MCP (write_module_source, mode=append).
    write_args = {
        "objectName": baseObjectName,
        "moduleType": moduleType,
        "mode": "append",
        "source": interceptor,
        "projectPath": str(ext_root),
    }
    if formName:
        write_args["formName"] = formName
    proxy_ok, response = _core.proxy_call("write_module_source", write_args)
    if proxy_ok and not response.get("error"):
        return {
            "ok": True,
            "via": "edt-mcp",
            "target": str(ext_module),
            "interceptor": interceptor,
            "warnings": warnings,
        }
    if proxy_ok:
        warnings.append(
            f"write_module_source вернул ошибку ({response.get('error')}) — "
            "переход на файловую запись"
        )
    else:
        warnings.append(
            "EDT-MCP недоступен — перехватчик вставлен файлово; "
            "выполните resync вручную при необходимости"
        )

    # Стратегия 2: файловое дописывание в .bsl расширения + resync.
    if not _core.file_mutations_allowed():
        return {
            "ok": False,
            "error": (
                "EDT-MCP недоступен, а файловые мутации запрещены: "
                "установите EDTB_ALLOW_FILE_MUTATIONS=1"
            ),
            "plan": plan,
            "warnings": warnings,
        }
    warnings.extend(_core.git_dirty_warnings(ext_root))
    _core.backup_file(ext_module, ext_root)
    ext_module.parent.mkdir(parents=True, exist_ok=True)
    existing = ext_module.read_text(encoding="utf-8") if ext_module.exists() else ""
    separator = "\n\n" if existing.strip() else ""
    ext_module.write_text(existing.rstrip("\n") + separator + interceptor, encoding="utf-8")
    warnings.extend(_core.resync_after_file_changes(ext_root))

    return {
        "ok": True,
        "via": "file",
        "target": str(ext_module),
        "interceptor": interceptor,
        "warnings": warnings,
    }


_ROLE_MDO_TMPL = """<?xml version="1.0" encoding="UTF-8"?>
<mdclass:Role xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:mdclass="http://g5.1c.ru/v8/dt/metadata/mdclass" uuid="{UUID_ROOT}">
  <name>{NAME}</name>
  <synonym>
    <key>ru</key>
    <value>{NAME}</value>
  </synonym>
  <comment></comment>
</mdclass:Role>
"""


def edtb_cfe_init_role(
    roleName: str | None = None,
    extProjectPath: str | None = None,
    projectName: str | None = None,
    dryRun: bool = False,
) -> dict:
    """Создать «основную роль» расширения (GAP-CFE-NOROLE).

    Основной путь — create_metadata Role через proxy (композиция после
    create_project). Fallback — файловый .mdo роли в
    ``src/Roles/<roleName>.mdo`` + регистрация в Configuration.mdo + resync.

    Параметры:
        roleName: имя роли (по умолчанию "ОсновнаяРоль").
        extProjectPath: корень проекта расширения (иначе EDTB_PROJECT_PATH).
        projectName: имя проекта в EDT для create_metadata.
        dryRun: вернуть план без применения.

    Возвращает:
        dict с полями ok, roleName, via, warnings.
    """
    warnings: list[str] = []
    role_name = roleName or "ОсновнаяРоль"
    if not _NAME_RE.match(role_name):
        return {
            "ok": False,
            "error": f"Некорректное имя роли: {role_name!r}",
            "warnings": warnings,
        }

    ext_root = _core.resolve_project_path(extProjectPath)
    plan = {
        "action": "cfe_init_role",
        "roleName": role_name,
        "strategies": ["edt-mcp:create_metadata", "file:src/Roles/<role>.mdo"],
    }
    if dryRun:
        return {"ok": True, "dryRun": True, "plan": plan, "warnings": warnings}

    # Стратегия 1: create_metadata через EDT-MCP.
    create_args: dict = {"type": "Role", "name": role_name}
    if projectName:
        create_args["projectName"] = projectName
    if ext_root is not None:
        create_args["projectPath"] = str(ext_root)
    proxy_ok, response = _core.proxy_call("create_metadata", create_args)
    if proxy_ok and not response.get("error"):
        return {
            "ok": True,
            "via": "edt-mcp",
            "roleName": role_name,
            "warnings": warnings,
        }
    if proxy_ok:
        warnings.append(
            f"create_metadata Role отклонён ({response.get('error')}) — "
            "файловый fallback"
        )
    else:
        warnings.append("EDT-MCP недоступен — файловое создание роли")

    # Стратегия 2: файловый .mdo роли.
    if ext_root is None:
        return {
            "ok": False,
            "error": (
                "Не задан путь проекта расширения (extProjectPath или "
                "EDTB_PROJECT_PATH) — файловый fallback невозможен"
            ),
            "warnings": warnings,
        }
    if not _core.file_mutations_allowed():
        return {
            "ok": False,
            "error": (
                "EDT-MCP недоступен, а файловые мутации запрещены: "
                "установите EDTB_ALLOW_FILE_MUTATIONS=1"
            ),
            "plan": plan,
            "warnings": warnings,
        }

    role_file = ext_root / "src" / "Roles" / f"{role_name}.mdo"
    if role_file.exists():
        warnings.append(f"Файл роли уже существует: {role_file} — будет перезаписан")
    warnings.extend(_core.git_dirty_warnings(ext_root))
    _core.backup_file(role_file, ext_root)
    role_file.parent.mkdir(parents=True, exist_ok=True)
    role_file.write_text(
        _ROLE_MDO_TMPL.replace("{UUID_ROOT}", str(uuid.uuid4())).replace(
            "{NAME}", role_name
        ),
        encoding="utf-8",
    )

    # Регистрация роли в Configuration.mdo.
    config_mdo = ext_root / "src" / "Configuration" / "Configuration.mdo"
    if not config_mdo.exists():
        config_mdo = ext_root / "src" / "Configuration.mdo"
    if config_mdo.exists():
        content = config_mdo.read_text(encoding="utf-8")
        entry = f"  <roles>Role.{role_name}</roles>\n"
        if f"<roles>Role.{role_name}</roles>" not in content:
            _core.backup_file(config_mdo, ext_root)
            if "</mdclass:Configuration>" in content:
                content = content.replace(
                    "</mdclass:Configuration>",
                    entry + "</mdclass:Configuration>",
                    1,
                )
            else:
                content = content.rstrip("\n") + "\n" + entry
            config_mdo.write_text(content, encoding="utf-8")
    else:
        warnings.append(
            "Configuration.mdo не найден — роль не зарегистрирована в "
            "конфигурации расширения; добавьте в EDT вручную"
        )

    warnings.extend(_core.resync_after_file_changes(ext_root))
    return {
        "ok": True,
        "via": "file",
        "roleName": role_name,
        "roleFile": str(role_file),
        "warnings": warnings,
    }
