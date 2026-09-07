# -*- coding: utf-8 -*-
"""Инструменты M2 (формы): edtb_form_compile / edtb_form_info / edtb_form_remove.

Интеграция с ядром по SPEC:
- ``edt_bridge.config`` — project discovery (EDTB_PROJECT_PATH / projectPath);
- ``edt_bridge.safety`` — git-checkpoint check, backup перед правкой;
- ``edt_bridge.proxy.client.call_tool`` — вызовы EDT-MCP (только канонические
  имена инструментов);
- ``edt_bridge.proxy.resync`` — resync_to_disk + revalidate_objects после
  файловых правок.

Правила безопасности: пишущие операции только при EDTB_ALLOW_FILE_MUTATIONS=1,
``dryRun`` возвращает план, ``warnings`` — в каждом ответе.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .form_dsl import compile_dsl
from .form_xml import (
    FormXmlError,
    apply_fragment,
    parse_form_info,
    parse_form_xml,
    serialize,
)

# Множественное число типов метаданных → каталог в EDT-проекте.
_TYPE_DIRS = {
    "Catalog": "Catalogs",
    "Document": "Documents",
    "DataProcessor": "DataProcessors",
    "Report": "Reports",
    "ExternalDataProcessor": "ExternalDataProcessors",
    "ExternalReport": "ExternalReports",
    "CommonForm": "CommonForms",
}


# ---------------------------------------------------------------------------
# Ленивые импорты ядра (core-модуль может быть ещё не смержен)
# ---------------------------------------------------------------------------

def _config():
    from edt_bridge import config  # noqa: PLC0415
    return config


def _safety():
    from edt_bridge import safety  # noqa: PLC0415
    return safety


def _git_dirty_warnings(project_path) -> list[str]:
    """Предупреждения о dirty git-дереве через реальный API ядра либо стаб."""
    safety = _safety()
    checker = getattr(safety, "git_dirty_check", None)
    if checker is not None:
        msg = checker(Path(str(project_path)))
        return [msg] if msg else []
    checker = getattr(safety, "check_git_dirty", None)
    if checker is not None:
        return list(checker(project_path))
    return []


async def _call_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    from edt_bridge.proxy.client import EdtMcpClient  # noqa: PLC0415
    return await EdtMcpClient().call_tool(name, args)


async def _resync(project: Any) -> dict[str, Any]:
    from edt_bridge.proxy import resync  # noqa: PLC0415
    runner = getattr(resync, "resync_after_file_changes", None)
    if runner is not None:
        return await runner(Path(str(project.path)))
    return await resync.resync_to_disk(project)


def _result(warnings: list[str] | None = None, **fields: Any) -> dict[str, Any]:
    out = {"warnings": warnings or []}
    out.update(fields)
    return out


# ---------------------------------------------------------------------------
# Пути
# ---------------------------------------------------------------------------

def form_dir(project_path: str | Path, object_fqn: str,
             form_name: str) -> Path:
    """Каталог формы: <project>/src/<TypePlural>/<Object>/Forms/<Form>."""
    type_token, _, object_name = object_fqn.partition(".")
    type_dir = _TYPE_DIRS.get(type_token, type_token + "s")
    if type_token == "CommonForm":
        return Path(project_path) / "src" / type_dir / form_name
    return (Path(project_path) / "src" / type_dir / object_name
            / "Forms" / form_name)


def form_file(project_path: str | Path, object_fqn: str,
              form_name: str) -> Path:
    return form_dir(project_path, object_fqn, form_name) / "Form.form"


def _mutations_allowed() -> bool:
    try:
        return bool(_config().file_mutations_allowed())
    except Exception:
        try:
            return bool(_config().get_config().allow_file_mutations)
        except Exception:
            import os
            return os.environ.get("EDTB_ALLOW_FILE_MUTATIONS") == "1"


def _resolve_project(project_path):
    """Корень проекта через реальный API ядра (resolve_project_path) либо стаб."""
    cfg = _config()
    resolver = getattr(cfg, "resolve_project_path", None)
    if resolver is not None:
        class _P:
            def __init__(self, path):
                self.path = str(path)
                self.name = Path(str(path)).name
        return _P(resolver(project_path))
    return cfg.get_project(project_path)


# ---------------------------------------------------------------------------
# edtb_form_compile
# ---------------------------------------------------------------------------

async def edtb_form_compile(
    objectName: str,
    dsl: dict[str, Any],
    formName: str | None = None,
    mode: str = "create",
    dryRun: bool = True,
    projectPath: str | None = None,
) -> dict[str, Any]:
    """Компиляция формы из JSON-DSL (GAP-FORM-DSL/BATCH/PRESETS).

    :param objectName: FQN владельца (``Catalog.Валюты``).
    :param formName: имя формы; при отсутствии — ``dsl["formName"]`` или
        ``Форма``.
    :param dsl: JSON-DSL (elements/commands/params/conditionalAppearance,
        пресеты, shorthand-ключи).
    :param mode: ``create`` | ``patch``.
    :param dryRun: True — вернуть план без применения.
    """
    warnings: list[str] = []
    form_name = formName or dsl.get("formName") or "Форма"

    compiled = compile_dsl(objectName, form_name, dsl, mode=mode)
    warnings.extend(compiled["warnings"])
    if not compiled["ok"]:
        return _result(warnings, applied=False, ok=False,
                       errors=compiled["errors"],
                       viaEdtMcp=compiled["plan"]["viaEdtMcp"],
                       viaFile=compiled["plan"]["viaFile"])

    plan = compiled["plan"]
    plan_hash = hashlib.sha256(
        json.dumps(plan, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]

    if dryRun:
        return _result(warnings, applied=False, ok=True, dryRun=True,
                       planHash=plan_hash,
                       viaEdtMcp=plan["viaEdtMcp"], viaFile=plan["viaFile"])

    # --- применение --------------------------------------------------------
    project = _resolve_project(projectPath)
    safety = _safety()
    warnings.extend(_git_dirty_warnings(project.path))

    applied_edt: list[dict[str, Any]] = []
    for call in plan["viaEdtMcp"]:
        try:
            res = await _call_tool(call["tool"], dict(call["args"]))
            applied_edt.append({"tool": call["tool"],
                                "fqn": call["args"].get("fqn"), "ok": True,
                                "result": res})
        except Exception as exc:  # EDT-MCP недоступен/отклонил
            applied_edt.append({"tool": call["tool"],
                                "fqn": call["args"].get("fqn"), "ok": False,
                                "error": str(exc)})
            warnings.append(
                f"EDT-MCP {call['tool']} ({call['args'].get('fqn')}): {exc}"
            )

    applied_file: list[dict[str, Any]] = []
    if plan["viaFile"]:
        if not _mutations_allowed():
            warnings.append(
                "Файловые фрагменты пропущены: EDTB_ALLOW_FILE_MUTATIONS != 1."
            )
        else:
            path = form_file(project.path, objectName, form_name)
            if not path.exists() and mode == "patch":
                return _result(
                    warnings, applied=False, ok=False,
                    errors=[f"Файл формы не найден: {path}"],
                    viaEdtMcp=applied_edt, viaFile=applied_file,
                    planHash=plan_hash,
                )
            backup = None
            if path.exists():
                backup_fn = getattr(safety, "backup_file", None)
                if backup_fn is not None:
                    backup = backup_fn(path)
                else:
                    from edt_bridge.safety import create_backup  # noqa: PLC0415
                    backup = create_backup(Path(project.path), [path]).root
            if backup:
                warnings.append(f"Backup: {backup}")
            if path.exists():
                tree = parse_form_xml(path)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                tree = parse_form_xml(
                    '<?xml version="1.0" encoding="UTF-8"?>\n'
                    '<form:Form '
                    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
                    'xmlns:form="http://g5.1c.ru/v8/dt/form"/>'
                )
            try:
                for fragment in plan["viaFile"]:
                    res = apply_fragment(tree, fragment)
                    warnings.extend(res["warnings"])
                    applied_file.append({"action": fragment["action"],
                                         "element": fragment.get("element"),
                                         "changed": res["changed"]})
                path.write_text(serialize(tree), encoding="utf-8")
            except FormXmlError as exc:
                return _result(
                    warnings, applied=False, ok=False, errors=[str(exc)],
                    viaEdtMcp=applied_edt, viaFile=applied_file,
                    planHash=plan_hash,
                )
            try:
                await _resync(project)
            except Exception:
                warnings.append(
                    "EDT-MCP недоступен: выполните resync вручную "
                    "(resync_to_disk + revalidate_objects)."
                )

    return _result(warnings, applied=True, ok=True, planHash=plan_hash,
                   viaEdtMcp=applied_edt, viaFile=applied_file)


# ---------------------------------------------------------------------------
# edtb_form_info
# ---------------------------------------------------------------------------

async def edtb_form_info(
    objectName: str,
    formName: str,
    projectPath: str | None = None,
) -> dict[str, Any]:
    """Разбор Form.form → {elements, commands, params, events} (headless)."""
    warnings: list[str] = []
    try:
        project = _resolve_project(projectPath)
    except Exception as exc:
        return _result(warnings, ok=False, errors=[str(exc)])
    path = form_file(project.path, objectName, formName)
    if not path.exists():
        return _result(warnings, ok=False,
                       errors=[f"Файл формы не найден: {path}"])
    try:
        info = parse_form_info(parse_form_xml(path))
    except (FormXmlError, ValueError) as exc:
        return _result(warnings, ok=False, errors=[str(exc)])
    return _result(warnings, ok=True, **info)


# ---------------------------------------------------------------------------
# edtb_form_remove
# ---------------------------------------------------------------------------

async def edtb_form_remove(
    objectName: str,
    formName: str,
    confirmToken: str | None = None,
    dryRun: bool = True,
    projectPath: str | None = None,
) -> dict[str, Any]:
    """Удаление формы с пре-чеком BSL-ссылок (GAP-FORM-REMOVE-CASCADE).

    Фаза 1 (dryRun/default): ``search_in_code`` по имени формы через proxy,
    отчёт о ссылках и одноразовый ``confirmToken``.
    Фаза 2: удаление каталога формы (при EDTB_ALLOW_FILE_MUTATIONS=1) +
    resync; регистрация в .mdo родителя убирается файлово.
    """
    warnings: list[str] = []
    project = _resolve_project(projectPath)

    # Пре-чек ссылок (GAP-FORM-REMOVE-CASCADE).
    references: list[Any] = []
    try:
        res = await _call_tool("search_in_code", {
            "query": formName,
            "projectName": getattr(project, "name", None),
        })
        references = (res or {}).get("results", res if isinstance(res, list)
                                     else [])
    except Exception as exc:
        warnings.append(
            f"search_in_code недоступен ({exc}); пре-чек ссылок не выполнен."
        )
    if references:
        warnings.append(
            f"Найдено ссылок на форму {formName!r}: {len(references)}. "
            "Проверьте и перепишите BSL-обращения до удаления "
            "(каскад dataPath/команд не переписывается автоматически)."
        )

    fqn = f"{objectName}.Form.{formName}"
    token = hashlib.sha256(
        f"form-remove:{fqn}".encode("utf-8")
    ).hexdigest()[:12]

    if dryRun or confirmToken != token:
        return _result(
            warnings, removed=False, ok=True, dryRun=True,
            references=references, confirmToken=token,
            note="Повторите вызов с confirmToken для удаления "
                 "(двухфазное удаление, SPEC §5.6).",
        )

    # Фаза 2: удаление.
    try:
        await _call_tool("delete_metadata", {"fqn": fqn})
        return _result(warnings, removed=True, ok=True, via="edt-mcp",
                       references=references)
    except Exception as exc:
        warnings.append(f"delete_metadata недоступен/отклонён: {exc}; "
                        "пробуем файловое удаление.")

    if not _mutations_allowed():
        return _result(
            warnings, removed=False, ok=False,
            errors=["Файловое удаление запрещено: "
                    "EDTB_ALLOW_FILE_MUTATIONS != 1."],
            references=references,
        )

    warnings.extend(_git_dirty_warnings(project.path))
    target_dir = form_dir(project.path, objectName, formName)
    if not target_dir.exists():
        return _result(warnings, removed=False, ok=False,
                       errors=[f"Каталог формы не найден: {target_dir}"],
                       references=references)
    backup = safety.backup_file(target_dir)
    warnings.append(f"Backup: {backup}")

    import shutil
    shutil.rmtree(target_dir)
    try:
        await _resync(project)
    except Exception:
        warnings.append("EDT-MCP недоступен: выполните resync вручную.")
    return _result(warnings, removed=True, ok=True, via="file",
                   references=references)
