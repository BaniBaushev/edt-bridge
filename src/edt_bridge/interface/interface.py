"""Инструменты командного интерфейса подсистем (GAP-INTERFACE-COMMANDS,
GAP-INTERFACE-ORDER).

Файловая правка ``CommandInterface.xml`` подсистемы: операции hide/show/place/order
команд (commandsVisibility / commandsPlacement / commandsOrder), dryRun с
unified diff, backup перед записью и resync через EDT-MCP после записи.
"""
from __future__ import annotations

import difflib
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from lxml import etree

# ---------------------------------------------------------------------------
# Интеграция с ядром (config/safety/proxy) — ленивый импорт с fallback,
# чтобы модуль работал и до появления ядра (контракт по SPEC.md §2, §5).
# ---------------------------------------------------------------------------

XC_EXTRN = "http://v8.1c.ru/8.3/xcf/extrn"


def _core_config():
    """Модуль edt_bridge.config, если ядро доступно (иначе None)."""
    try:
        from edt_bridge import config  # type: ignore

        return config
    except Exception:
        return None


def _project_path(project_path: Optional[str] = None) -> Path:
    """Корень EDT-проекта: аргумент projectPath либо env EDTB_PROJECT_PATH."""
    cfg = _core_config()
    if cfg is not None and hasattr(cfg, "get_project_path"):
        return Path(cfg.get_project_path(project_path))
    raw = project_path or os.environ.get("EDTB_PROJECT_PATH")
    if not raw:
        raise ValueError(
            "Не задан путь к проекту: передайте projectPath или установите "
            "переменную окружения EDTB_PROJECT_PATH."
        )
    return Path(raw)


def _mutations_allowed() -> bool:
    """Признак разрешения файловых мутаций (EDTB_ALLOW_FILE_MUTATIONS=1)."""
    cfg = _core_config()
    if cfg is not None and hasattr(cfg, "allow_file_mutations"):
        return bool(cfg.allow_file_mutations())
    return os.environ.get("EDTB_ALLOW_FILE_MUTATIONS", "0") == "1"


def _backup_root(project: Path) -> Path:
    return project / ".edtb-backup" / datetime.now().strftime("%Y%m%d-%H%M%S")


def _backup_file(project: Path, path: Path, warnings: list[str]) -> Optional[Path]:
    """Backup файла в .edtb-backup/<timestamp>/ перед правкой (SPEC §5.3)."""
    try:
        from edt_bridge import safety  # type: ignore

        if hasattr(safety, "backup_file"):
            return Path(safety.backup_file(path))
    except Exception:
        pass
    backup_dir = _backup_root(project)
    backup_dir.mkdir(parents=True, exist_ok=True)
    dst = backup_dir / path.name
    shutil.copy2(path, dst)
    return dst


def _git_dirty_warning(project: Path, warnings: list[str]) -> None:
    """Warning, если проект под git и рабочее дерево «грязное» (SPEC §5.2)."""
    try:
        from edt_bridge import safety  # type: ignore

        if hasattr(safety, "git_dirty_warning"):
            msg = safety.git_dirty_warning(project)
            if msg:
                warnings.append(str(msg))
            return
    except Exception:
        pass
    if not (project / ".git").exists():
        return
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=project,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if out.stdout.strip():
            warnings.append(
                "Рабочее дерево git проекта содержит незакоммиченные изменения — "
                "рекомендуется сделать коммит или checkpoint перед мутациями."
            )
    except Exception:
        warnings.append("Не удалось проверить состояние git проекта.")


def _resync(project: Path, warnings: list[str], objects: Optional[list[str]] = None) -> None:
    """resync_to_disk + revalidate_objects через proxy; при недоступности — warning."""
    try:
        from edt_bridge.proxy import resync as _resync_mod  # type: ignore

        result = _resync_mod.resync(str(project), objects=objects)
        res_warnings = (
            result.get("warnings") if isinstance(result, dict)
            else getattr(result, "warnings", None)
        ) or []
        for w in res_warnings:
            warnings.append(str(w))
        return
    except Exception:
        pass
    try:
        from edt_bridge.proxy import client as _client  # type: ignore

        _client.call_tool("resync_to_disk", {"projectPath": str(project)})
        if objects:
            _client.call_tool("revalidate_objects", {"objects": objects})
        return
    except Exception:
        warnings.append(
            "EDT-MCP недоступен: resync_to_disk/revalidate_objects не выполнены. "
            "Выполните синхронизацию проекта с диском вручную в EDT."
        )


# ---------------------------------------------------------------------------
# Работа с CommandInterface.xml
# ---------------------------------------------------------------------------


def _subsystem_file(project: Path, subsystem_fqn: str) -> Path:
    """Путь к CommandInterface.xml по FQN вида Subsystem.A[.Subsystem.B]."""
    parts = subsystem_fqn.split(".")
    if (
        len(parts) < 2
        or len(parts) % 2 != 0
        or parts[0] != "Subsystem"
        or any(p != "Subsystem" for p in parts[::2])
        or any(p == "Subsystem" for p in parts[1::2])
    ):
        raise ValueError(
            f"Некорректный FQN подсистемы: {subsystem_fqn!r}. "
            "Ожидается вид Subsystem.<Имя>[.Subsystem.<ВложеннаяИмя>]."
        )
    names = parts[1::2]
    path = project / "src" / "Subsystems"
    for i, name in enumerate(names):
        path = path / name
        if i < len(names) - 1:
            path = path / "Subsystems"
    return path / "CommandInterface.xml"


def _parse_xml(path: Path) -> etree._ElementTree:
    parser = etree.XMLParser(remove_blank_text=False, resolve_entities=False)
    return etree.parse(str(path), parser)


def _tag(root: etree._Element, name: str) -> str:
    ns = root.nsmap.get(None)
    return f"{{{ns}}}{name}" if ns else name


def _find_child(root: etree._Element, name: str, create: bool = False) -> Optional[etree._Element]:
    child = root.find(_tag(root, name))
    if child is None and create:
        child = etree.SubElement(root, _tag(root, name))
        child.text = "\n  "
        child.tail = "\n"
    return child


def _find_entry(section: etree._Element, entry_tag: str, command: str,
                group: Optional[str] = None) -> Optional[etree._Element]:
    root = section.getroottree().getroot()
    t_cmd, t_group = _tag(root, "command"), _tag(root, "commandGroup")
    for entry in section.findall(_tag(root, entry_tag)):
        cmd_el = entry.find(t_cmd)
        if cmd_el is None or (cmd_el.text or "").strip() != command:
            continue
        if group is not None:
            grp_el = entry.find(t_group)
            if grp_el is None or (grp_el.text or "").strip() != group:
                continue
        return entry
    return None


def _apply_op(root: etree._Element, op: dict[str, Any], warnings: list[str]) -> str:
    """Применить одну операцию к дереву. Возвращает описание изменения."""
    action = op.get("operation")
    command = (op.get("command") or "").strip()
    if action not in {"hide", "show", "place", "order"}:
        raise ValueError(
            f"Неизвестная операция интерфейса: {action!r}. "
            "Допустимы: hide, show, place, order."
        )
    if not command:
        raise ValueError(f"Операция {action}: не указано поле 'command'.")

    if action in {"hide", "show"}:
        section = _find_child(root, "commandsVisibility", create=True)
        entry = _find_entry(section, "visibility", command)
        if action == "hide":
            if entry is None:
                entry = etree.SubElement(section, _tag(root, "visibility"))
                etree.SubElement(entry, _tag(root, "command")).text = command
                entry.tail = "\n"
            vis = entry.find(_tag(root, "visible"))
            if vis is None:
                vis = etree.SubElement(entry, _tag(root, "visible"))
            common = vis.find(_tag(root, "common"))
            if common is None:
                common = etree.SubElement(vis, _tag(root, "common"))
            common.text = "false"
            return f"hide: команда {command} скрыта (commandsVisibility common=false)"
        # show: снимаем явное скрытие — удаляем запись visibility, если она была.
        if entry is None:
            warnings.append(f"show: команда {command} не была скрыта явно — без изменений.")
            return f"show: команда {command} уже видима (переопределения не было)"
        section.remove(entry)
        return f"show: снято переопределение видимости команды {command}"

    group = (op.get("group") or "").strip()
    if not group:
        raise ValueError(f"Операция {action}: не указано поле 'group' (группа команд панели).")

    if action == "place":
        section = _find_child(root, "commandsPlacement", create=True)
        entry = _find_entry(section, "placement", command)
        if entry is None:
            entry = etree.SubElement(section, _tag(root, "placement"))
            etree.SubElement(entry, _tag(root, "command")).text = command
            etree.SubElement(entry, _tag(root, "commandGroup")).text = group
            entry.tail = "\n"
        else:
            entry.find(_tag(root, "commandGroup")).text = group
        return f"place: команда {command} размещена в группе {group}"

    # order
    section = _find_child(root, "commandsOrder", create=True)
    index = op.get("index")
    if index is None:
        raise ValueError("Операция order: не указано поле 'index' (целое число).")
    try:
        index = int(index)
    except (TypeError, ValueError):
        raise ValueError(f"Операция order: index должен быть целым числом, получено {index!r}.")
    entry = _find_entry(section, "order", command, group=group)
    if entry is None:
        entry = etree.SubElement(section, _tag(root, "order"))
        etree.SubElement(entry, _tag(root, "command")).text = command
        etree.SubElement(entry, _tag(root, "commandGroup")).text = group
        entry.tail = "\n"
    idx_el = entry.find(_tag(root, "index"))
    if idx_el is None:
        idx_el = etree.SubElement(entry, _tag(root, "index"))
    idx_el.text = str(index)
    return f"order: команда {command} в группе {group} получила индекс {index}"


def _serialize(tree: etree._ElementTree) -> str:
    raw = etree.tostring(tree, encoding="UTF-8", xml_declaration=True, pretty_print=True)
    return raw.decode("utf-8").replace("encoding='UTF-8'", 'encoding="UTF-8"')


def _unified_diff(old: str, new: str, filename: str) -> str:
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{filename}",
            tofile=f"b/{filename}",
        )
    )


def edtb_interface_edit(
    subsystemFQN: str,
    ops: list[dict[str, Any]],
    projectPath: Optional[str] = None,
    dryRun: bool = False,
) -> dict[str, Any]:
    """Правка CommandInterface.xml подсистемы: hide/show/place/order команд.

    ops: список операций:
      - {"operation": "hide",  "command": "<FQN команды>"}
      - {"operation": "show",  "command": "<FQN команды>"}
      - {"operation": "place", "command": ..., "group": "NavigationPanelOrdinary|..."}
      - {"operation": "order", "command": ..., "group": ..., "index": <int>}
    dryRun: вернуть unified diff без записи на диск.
    """
    warnings: list[str] = []
    try:
        project = _project_path(projectPath)
        xml_path = _subsystem_file(project, subsystemFQN)
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "warnings": warnings, "applied": []}

    if not xml_path.is_file():
        return {
            "ok": False,
            "error": (
                f"Файл командного интерфейса не найден: {xml_path}. "
                "Проверьте FQN подсистемы и путь к проекту."
            ),
            "warnings": warnings,
            "applied": [],
        }

    _git_dirty_warning(project, warnings)

    old_text = xml_path.read_text(encoding="utf-8")
    tree = _parse_xml(xml_path)
    root = tree.getroot()

    applied: list[str] = []
    try:
        for op in ops:
            applied.append(_apply_op(root, op, warnings))
    except ValueError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "warnings": warnings,
            "applied": [],
            "file": str(xml_path),
        }

    new_text = _serialize(tree)
    diff = _unified_diff(old_text, new_text, f"{subsystemFQN}/CommandInterface.xml")

    if dryRun:
        return {
            "ok": True,
            "dryRun": True,
            "file": str(xml_path),
            "diff": diff,
            "applied": applied,
            "warnings": warnings,
        }

    if not _mutations_allowed():
        return {
            "ok": False,
            "error": (
                "Файловые мутации запрещены: установите EDTB_ALLOW_FILE_MUTATIONS=1 "
                "или вызовите инструмент с dryRun=true для предпросмотра."
            ),
            "warnings": warnings,
            "file": str(xml_path),
            "diff": diff,
            "applied": [],
        }

    backup = _backup_file(project, xml_path, warnings)
    xml_path.write_text(new_text, encoding="utf-8")
    _resync(project, warnings, objects=[subsystemFQN])

    return {
        "ok": True,
        "dryRun": False,
        "file": str(xml_path),
        "backup": str(backup) if backup else None,
        "applied": applied,
        "warnings": warnings,
    }


def edtb_interface_info(
    subsystemFQN: str, projectPath: Optional[str] = None
) -> dict[str, Any]:
    """Текущая видимость, размещение и порядок команд из CommandInterface.xml."""
    warnings: list[str] = []
    try:
        project = _project_path(projectPath)
        xml_path = _subsystem_file(project, subsystemFQN)
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "warnings": warnings}

    if not xml_path.is_file():
        return {
            "ok": False,
            "error": f"Файл командного интерфейса не найден: {xml_path}.",
            "warnings": warnings,
        }

    root = _parse_xml(xml_path).getroot()

    visibility: dict[str, bool] = {}
    section = _find_child(root, "commandsVisibility")
    if section is not None:
        for entry in section.findall(_tag(root, "visibility")):
            cmd = (entry.findtext(_tag(root, "command")) or "").strip()
            common = entry.find(f"{_tag(root, 'visible')}/{_tag(root, 'common')}")
            if cmd:
                visibility[cmd] = (common is None) or (
                    (common.text or "").strip().lower() != "false"
                )

    placements: dict[str, str] = {}
    section = _find_child(root, "commandsPlacement")
    if section is not None:
        for entry in section.findall(_tag(root, "placement")):
            cmd = (entry.findtext(_tag(root, "command")) or "").strip()
            grp = (entry.findtext(_tag(root, "commandGroup")) or "").strip()
            if cmd:
                placements[cmd] = grp

    order: list[dict[str, Any]] = []
    section = _find_child(root, "commandsOrder")
    if section is not None:
        for entry in section.findall(_tag(root, "order")):
            cmd = (entry.findtext(_tag(root, "command")) or "").strip()
            grp = (entry.findtext(_tag(root, "commandGroup")) or "").strip()
            idx = (entry.findtext(_tag(root, "index")) or "").strip()
            if cmd:
                order.append(
                    {"command": cmd, "group": grp, "index": int(idx) if idx.isdigit() else idx}
                )
        order.sort(
            key=lambda e: (e["group"], e["index"] if isinstance(e["index"], int) else 10**9)
        )

    return {
        "ok": True,
        "subsystem": subsystemFQN,
        "file": str(xml_path),
        "commandsVisibility": visibility,
        "placements": placements,
        "order": order,
        "warnings": warnings,
    }
