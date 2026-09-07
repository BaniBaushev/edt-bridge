"""Инструменты модуля M3 (макеты MXL) MCP-сервера edt-bridge.

- ``edtb_mxl_decompile`` — .mxl (XML) → JSON-DSL (GAP-MXL-DECOMPILE).
- ``edtb_mxl_compile`` — JSON-DSL → либо payload ``modify_metadata``
  (поддерживаемое EDT-MCP подмножество) через proxy, либо генерация
  .mxl-файла (полный DSL) + resync (GAP-MXL-FULL-DSL). Поддерживает dryRun.
- ``edtb_mxl_info`` — areas/params/mergeCount из файла без скриншота
  (GAP-MXL-INFO, GAP-MXL-HEADLESS).

Интеграция с ядром по интерфейсам SPEC: ``edt_bridge.proxy.client.call_tool``,
``edt_bridge.config``, ``edt_bridge.safety``. Ядро может отсутствовать
(ранние стадии проекта) — тогда используются локальные fallback'и с warnings.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any

from .mxl_parser import parse_mxl
from .mxl_writer import MxlWriteError, write_mxl

try:  # ядро: конфигурация проекта
    from edt_bridge import config as _config
except ImportError:  # pragma: no cover - ядро ещё не реализовано
    _config = None

try:  # ядро: backup/безопасность
    from edt_bridge import safety as _safety
except ImportError:  # pragma: no cover
    _safety = None


# Каталоги видов метаданных в EDT-проекте.
_KIND_DIRS = {
    "Catalog": "Catalogs", "Document": "Documents", "Report": "Reports",
    "DataProcessor": "DataProcessors", "CommonTemplate": "CommonTemplates",
    "InformationRegister": "InformationRegisters",
    "AccumulationRegister": "AccumulationRegisters",
    "ChartOfAccounts": "ChartsOfAccounts",
    "ChartOfCharacteristicTypes": "ChartsOfCharacteristicTypes",
    "ExchangePlan": "ExchangePlans", "BusinessProcess": "BusinessProcesses",
    "Task": "Tasks", "Enum": "Enums", "Constant": "Constants",
    "DocumentJournal": "DocumentJournals", "ExternalDataProcessor":
    "ExternalDataProcessors", "ExternalReport": "ExternalReports",
}

_TEMPLATE_FILE_NAMES = ("Template.mxl", "Template.mxlx")


# ------------------------------------------------------------------ helpers
def _project_root(project_path: str | None) -> Path:
    """Корень EDT-проекта: аргумент → config → env."""
    path = project_path
    if not path and _config is not None:
        getter = getattr(_config, "get_project_path", None)
        if callable(getter):
            path = getter()
    if not path:
        path = os.environ.get("EDTB_PROJECT_PATH", "")
    if not path:
        raise ValueError(
            "не задан путь к EDT-проекту: передайте projectPath или "
            "установите EDTB_PROJECT_PATH"
        )
    return Path(path)


def _mutations_allowed() -> bool:
    if _config is not None:
        getter = getattr(_config, "file_mutations_allowed", None)
        if callable(getter):
            return bool(getter())
    return os.environ.get("EDTB_ALLOW_FILE_MUTATIONS", "0") == "1"


def _find_template_file(root: Path, object_name: str,
                        template_name: str) -> Path | None:
    """Файл макета в проекте: прямой путь по виду метаданных, иначе glob."""
    candidates: list[Path] = []
    parts = object_name.split(".")
    if object_name == "CommonTemplate" or parts[0] == "CommonTemplate":
        # Общий макет: src/CommonTemplates/<Имя>/Template.mxl
        name = parts[1] if len(parts) > 1 else template_name
        candidates.append(Path("src") / "CommonTemplates" / name)
    elif len(parts) == 2 and parts[0] in _KIND_DIRS:
        candidates.append(
            Path("src") / _KIND_DIRS[parts[0]] / parts[1]
            / "Templates" / template_name
        )
    for base in candidates:
        for fname in _TEMPLATE_FILE_NAMES:
            p = root / base / fname
            if p.is_file():
                return p
    for fname in _TEMPLATE_FILE_NAMES:
        matches = sorted(root.glob(f"**/Templates/{template_name}/{fname}"))
        if matches:
            return matches[0]
        matches = sorted(root.glob(f"**/CommonTemplates/{template_name}/{fname}"))
        if matches:
            return matches[0]
    return None


def _target_template_path(root: Path, object_name: str,
                          template_name: str) -> Path:
    """Целевой путь для записи (даже если файл ещё не существует)."""
    existing = _find_template_file(root, object_name, template_name)
    if existing is not None:
        return existing
    parts = object_name.split(".")
    if parts[0] == "CommonTemplate":
        name = parts[1] if len(parts) > 1 else template_name
        return root / "src" / "CommonTemplates" / name / "Template.mxl"
    if len(parts) == 2 and parts[0] in _KIND_DIRS:
        return (root / "src" / _KIND_DIRS[parts[0]] / parts[1]
                / "Templates" / template_name / "Template.mxl")
    raise ValueError(
        f"не удалось адресовать макет '{object_name}.Template.{template_name}': "
        "укажите objectName вида '<Вид>.<Имя>' (например 'Report.Продажи')"
    )


def _backup(path: Path, root: Path, warnings: list[str]) -> str | None:
    """Backup файла через ядро safety; fallback — локальная копия."""
    if _safety is not None:
        backup_fn = getattr(_safety, "backup_file", None)
        if callable(backup_fn):
            return str(backup_fn(path))
    if not path.is_file():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = root / ".edtb-backup" / stamp / path.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(path.read_bytes())
    warnings.append(
        "ядро safety недоступно — backup выполнен локальным fallback'ом"
    )
    return str(dest)


async def _call_edt_mcp(tool: str, args: dict[str, Any],
                        warnings: list[str]) -> dict[str, Any] | None:
    """Вызов EDT-MCP через proxy; graceful-degrade при недоступности."""
    try:
        from edt_bridge.proxy import client as _client_mod
    except ImportError:
        warnings.append(
            "proxy к EDT-MCP недоступен (модуль edt_bridge.proxy.client "
            "отсутствует) — пропущен вызов " + tool
        )
        return None
    try:
        caller = getattr(_client_mod, "call_tool", None)
        if caller is not None:
            return await caller(tool, args)
        return await _client_mod.EdtMcpClient().call_tool(tool, args)
    except Exception as exc:  # noqa: BLE001 — degrade gracefully по SPEC
        warnings.append(f"EDT-MCP '{tool}' завершился ошибкой: {exc}")
        return None


async def _resync(root: Path, warnings: list[str]) -> None:
    """resync_to_disk + revalidate_objects после файловой правки."""
    res = await _call_edt_mcp("resync_to_disk",
                              {"projectPath": str(root)}, warnings)
    if res is None:
        warnings.append(
            "EDT-MCP недоступен — выполните resync проекта вручную"
        )
        return
    await _call_edt_mcp("revalidate_objects",
                        {"projectPath": str(root)}, warnings)


# ------------------------------------------------------------ DSL-стратегия
def _is_edt_mcp_subset(dsl: dict[str, Any]) -> tuple[bool, list[str]]:
    """Проверка: DSL укладывается в подмножество payload modify_metadata.

    Подмножество EDT-MCP (по GAP-MXL-FULL-DSL): простые области, текст и
    параметры без бордюров, цветов, палитр стилей, формата страницы,
    заполнения «Шаблон» и параметров расшифровки.
    """
    reasons: list[str] = []
    if dsl.get("pageSetup") or dsl.get("page"):
        reasons.append("формат страницы (pageSetup/page)")
    styles = dsl.get("styles") or {}
    for name, style in styles.items():
        if not isinstance(style, dict):
            continue
        extras = set(style) - {"font"}
        if extras:
            reasons.append(
                f"стиль '{name}' использует {sorted(extras)} "
                "(бордюры/цвета/выравнивание/формат)"
            )
    for area in dsl.get("areas") or []:
        for row in (area or {}).get("rows") or []:
            row = row or {}
            if row.get("rowStyle"):
                reasons.append("rowStyle (палитра стилей)")
            for cell in row.get("cells") or []:
                if cell.get("template") is not None:
                    reasons.append("заполнение «Шаблон» (template)")
                if cell.get("detail"):
                    reasons.append("параметр расшифровки (detail)")
                if cell.get("style") not in (None, "default"):
                    reasons.append("стили ячеек")
    uniq = sorted(set(reasons))
    return (not uniq), uniq


def _modify_metadata_payload(object_name: str, template_name: str,
                             dsl: dict[str, Any]) -> dict[str, Any]:
    """Payload modify_metadata для поддерживаемого подмножества DSL."""
    areas = []
    for area in dsl.get("areas") or []:
        rows = []
        for row in (area or {}).get("rows") or []:
            cells = []
            for cell in (row or {}).get("cells") or []:
                entry: dict[str, Any] = {"col": cell.get("col", 1)}
                if cell.get("param") is not None:
                    entry["fillType"] = "Parameter"
                    entry["parameter"] = cell["param"]
                else:
                    entry["fillType"] = "Text"
                    entry["text"] = cell.get("text") or ""
                cells.append(entry)
            rows.append({"cells": cells})
        areas.append({"name": area.get("name", ""), "rows": rows})
    return {
        "objectName": f"{object_name}.Template.{template_name}",
        "properties": {
            "template": {
                "columns": dsl.get("columns", 0),
                "areas": areas,
            }
        },
    }


# ------------------------------------------------------------------ tools
async def edtb_mxl_decompile(objectName: str, templateName: str,
                             projectPath: str | None = None) -> dict[str, Any]:
    """Декомпилировать макет .mxl проекта в JSON-DSL."""
    warnings: list[str] = []
    root = _project_root(projectPath)
    path = _find_template_file(root, objectName, templateName)
    if path is None:
        return {
            "ok": False,
            "error": f"файл макета '{templateName}' объекта '{objectName}' "
                     "не найден в проекте",
            "warnings": warnings,
        }
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    dsl = parse_mxl(text)
    warnings.extend(dsl.pop("warnings", []))
    return {
        "ok": True,
        "file": str(path),
        "dsl": dsl,
        "warnings": warnings,
    }


async def edtb_mxl_compile(objectName: str, templateName: str, dsl: dict,
                           projectPath: str | None = None,
                           dryRun: bool = False,
                           strategy: str = "auto") -> dict[str, Any]:
    """Скомпилировать JSON-DSL в макет.

    strategy: ``auto`` — подмножество → modify_metadata, полный DSL → файл;
    ``edt-mcp`` — принудительно через proxy; ``file`` — принудительно файл.
    """
    warnings: list[str] = []
    subset, reasons = _is_edt_mcp_subset(dsl)
    if strategy == "auto":
        strategy = "edt-mcp" if subset else "file"
    if strategy == "edt-mcp" and not subset:
        warnings.append(
            "стратегия 'edt-mcp' выбрана принудительно, но DSL выходит за "
            f"подмножество EDT-MCP: {reasons}"
        )

    result: dict[str, Any] = {
        "ok": True, "strategy": strategy, "dryRun": dryRun,
        "subsetReasons": reasons, "warnings": warnings,
    }

    if strategy == "edt-mcp":
        payload = _modify_metadata_payload(objectName, templateName, dsl)
        result["payload"] = payload
        if dryRun:
            result["plan"] = "modify_metadata через EDT-MCP (payload в поле)"
            return result
        res = await _call_edt_mcp("modify_metadata", payload, warnings)
        result["applied"] = res is not None
        result["response"] = res
        if res is None:
            result["ok"] = False
            result["error"] = "EDT-MCP недоступен — изменения не применены"
        return result

    # Файловая стратегия (полный DSL).
    try:
        xml_text, gen_warnings = write_mxl(dsl)
    except MxlWriteError as exc:
        return {"ok": False, "strategy": strategy, "error": str(exc),
                    "warnings": warnings}
    warnings.extend(gen_warnings)
    root = _project_root(projectPath)
    target = _target_template_path(root, objectName, templateName)
    result["file"] = str(target)
    result["preview"] = xml_text
    if dryRun:
        result["plan"] = (f"запись файла {target} + resync_to_disk "
                          "(EDTB_ALLOW_FILE_MUTATIONS=1)")
        return result
    if not _mutations_allowed():
        result["ok"] = False
        result["error"] = ("файловые мутации запрещены: установите "
                           "EDTB_ALLOW_FILE_MUTATIONS=1 или используйте "
                           "dryRun=true для preview")
        return result
    target.parent.mkdir(parents=True, exist_ok=True)
    result["backup"] = _backup(target, root, warnings)
    target.write_text(xml_text, encoding="utf-8")
    result["applied"] = True
    await _resync(root, warnings)
    return result


async def edtb_mxl_info(objectName: str, templateName: str,
                        projectPath: str | None = None) -> dict[str, Any]:
    """Структурная информация о макете из файла (без скриншота, headless)."""
    warnings: list[str] = []
    root = _project_root(projectPath)
    path = _find_template_file(root, objectName, templateName)
    if path is None:
        return {
            "ok": False,
            "error": f"файл макета '{templateName}' объекта '{objectName}' "
                     "не найден в проекте",
            "warnings": warnings,
        }
    dsl = parse_mxl(path.read_text(encoding="utf-8-sig", errors="replace"))
    warnings.extend(dsl.get("warnings", []))
    params: list[str] = []
    details: list[str] = []
    row_count = 0
    for area in dsl.get("areas") or []:
        for row in area.get("rows") or []:
            row_count += 1
            for cell in (row or {}).get("cells") or []:
                if cell.get("param") and cell["param"] not in params:
                    params.append(cell["param"])
                if cell.get("detail") and cell["detail"] not in details:
                    details.append(cell["detail"])
    merge_count = sum(
        1
        for area in dsl.get("areas") or []
        for row in area.get("rows") or []
        for cell in (row or {}).get("cells") or []
        if (cell.get("span") or 1) > 1 or (cell.get("rowspan") or 1) > 1
    )
    return {
        "ok": True,
        "file": str(path),
        "columns": dsl.get("columns", 0),
        "rows": row_count,
        "areas": [{"name": a.get("name"),
                   "rowCount": len(a.get("rows") or [])}
                  for a in dsl.get("areas") or []],
        "params": params,
        "detailParams": details,
        "mergeCount": merge_count,
        "warnings": warnings,
    }


def register(mcp: Any) -> None:
    """Регистрация инструментов модуля в fastmcp-сервере."""
    mcp.tool(edtb_mxl_decompile)
    mcp.tool(edtb_mxl_compile)
    mcp.tool(edtb_mxl_info)
