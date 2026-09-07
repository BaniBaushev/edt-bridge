"""Инструменты панелей интерфейса конфигурации (GAP-CF-PANELS).

Файловая правка ``Configuration.mdo``: секция ``clientApplicationInterface``
(раскладка панелей Taxi) и ``homePageWorkArea`` (начальная страница).
dryRun с unified diff, backup и resync — как в interface.py.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from lxml import etree

from .interface import (
    _backup_file,
    _git_dirty_warning,
    _mutations_allowed,
    _parse_xml,
    _project_path,
    _resync,
    _serialize,
    _tag,
    _unified_diff,
)

# Допустимые расположения панелей Taxi.
PANEL_LOCATIONS = {"Top", "Bottom", "Left", "Right", "Hidden"}

MDCLASS_NS = "http://v8.1c.ru/8.1/data/enterprise/current-config"


def _configuration_mdo(project: Path) -> Path:
    path = project / "src" / "Configuration" / "Configuration.mdo"
    if not path.is_file():
        raise FileNotFoundError(
            f"Файл конфигурации не найден: {path}. "
            "Проверьте, что projectPath указывает на корень EDT-проекта."
        )
    return path


def _find_or_create_section(root: etree._Element, name: str) -> etree._Element:
    section = root.find(_tag(root, name))
    if section is None:
        section = etree.SubElement(root, _tag(root, name))
        section.text = "\n  "
        section.tail = "\n"
    return section


def _find_named_child(section: etree._Element, tag: str, name: str) -> Optional[etree._Element]:
    root = section.getroottree().getroot()
    t_name = _tag(root, "name")
    for child in section.findall(_tag(root, tag)):
        name_el = child.find(t_name)
        if name_el is not None and (name_el.text or "").strip() == name:
            return child
    return None


def _apply_panels(root: etree._Element, panels: list[dict[str, Any]]) -> list[str]:
    """Установить расположение панелей Taxi (set-panels)."""
    applied: list[str] = []
    section = _find_or_create_section(root, "clientApplicationInterface")
    for item in panels:
        name = (item.get("panel") or "").strip()
        location = (item.get("location") or "").strip()
        if not name:
            raise ValueError("Операция panels: не указано поле 'panel' (имя панели).")
        if location not in PANEL_LOCATIONS:
            raise ValueError(
                f"Операция panels: недопустимое расположение {location!r} для панели "
                f"{name}. Допустимы: {', '.join(sorted(PANEL_LOCATIONS))}."
            )
        panel_el = _find_named_child(section, "panel", name)
        if panel_el is None:
            panel_el = etree.SubElement(section, _tag(root, "panel"))
            etree.SubElement(panel_el, _tag(root, "name")).text = name
            panel_el.tail = "\n"
        loc_el = panel_el.find(_tag(root, "location"))
        if loc_el is None:
            loc_el = etree.SubElement(panel_el, _tag(root, "location"))
        loc_el.text = location
        applied.append(f"panels: панель {name} → {location}")
    return applied


def _apply_home_page(root: etree._Element, home_page: dict[str, Any]) -> list[str]:
    """Установить состав начальной страницы (set-home-page)."""
    columns = home_page.get("columns")
    if not isinstance(columns, list) or not columns:
        raise ValueError(
            "Операция homePage: ожидается 'columns' — непустой список колонок "
            "вида {'forms': [<FQN формы>, ...]}."
        )
    applied: list[str] = []
    section = _find_or_create_section(root, "homePageWorkArea")
    # Полная замена колонок — детерминированный результат.
    for old in list(section):
        section.remove(old)
    for col in columns:
        forms = col.get("forms")
        if not isinstance(forms, list) or not forms:
            raise ValueError(
                "Операция homePage: каждая колонка должна содержать непустой список 'forms'."
            )
        col_el = etree.SubElement(section, _tag(root, "column"))
        col_el.tail = "\n"
        for form in forms:
            form_name = str(form).strip()
            if not form_name:
                raise ValueError("Операция homePage: пустое имя формы в колонке.")
            etree.SubElement(col_el, _tag(root, "form")).text = form_name
        applied.append(f"homePage: колонка с формами {', '.join(map(str, forms))}")
    return applied


def edtb_cf_panels(
    ops: dict[str, Any],
    projectPath: Optional[str] = None,
    dryRun: bool = False,
) -> dict[str, Any]:
    """Правка секций интерфейса в Configuration.mdo.

    ops:
      - "panels": [{"panel": "NavigationPanel"|"InformationPanel"|...,
                    "location": "Top"|"Bottom"|"Left"|"Right"|"Hidden"}, ...]
      - "homePage": {"columns": [{"forms": ["Catalog.X.Form.ListForm", ...]}, ...]}
    dryRun: вернуть unified diff без записи на диск.
    """
    warnings: list[str] = []
    if not isinstance(ops, dict) or not (ops.get("panels") or ops.get("homePage")):
        return {
            "ok": False,
            "error": (
                "Пустой набор операций: укажите хотя бы один из ключей "
                "'panels' (раскладка панелей) или 'homePage' (начальная страница)."
            ),
            "warnings": warnings,
            "applied": [],
        }

    try:
        project = _project_path(projectPath)
        mdo_path = _configuration_mdo(project)
    except (ValueError, FileNotFoundError) as exc:
        return {"ok": False, "error": str(exc), "warnings": warnings, "applied": []}

    _git_dirty_warning(project, warnings)

    old_text = mdo_path.read_text(encoding="utf-8")
    tree = _parse_xml(mdo_path)
    root = tree.getroot()

    applied: list[str] = []
    try:
        if ops.get("panels"):
            applied += _apply_panels(root, ops["panels"])
        if ops.get("homePage"):
            applied += _apply_home_page(root, ops["homePage"])
    except ValueError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "warnings": warnings,
            "applied": [],
            "file": str(mdo_path),
        }

    new_text = _serialize(tree)
    diff = _unified_diff(old_text, new_text, "Configuration/Configuration.mdo")

    if dryRun:
        return {
            "ok": True,
            "dryRun": True,
            "file": str(mdo_path),
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
            "file": str(mdo_path),
            "diff": diff,
            "applied": [],
        }

    backup = _backup_file(project, mdo_path, warnings)
    mdo_path.write_text(new_text, encoding="utf-8")
    _resync(project, warnings, objects=["Configuration"])

    return {
        "ok": True,
        "dryRun": False,
        "file": str(mdo_path),
        "backup": str(backup) if backup else None,
        "applied": applied,
        "warnings": warnings,
    }


def edtb_cf_panels_info(projectPath: Optional[str] = None) -> dict[str, Any]:
    """Текущая раскладка панелей и состав начальной страницы из Configuration.mdo."""
    warnings: list[str] = []
    try:
        project = _project_path(projectPath)
        mdo_path = _configuration_mdo(project)
    except (ValueError, FileNotFoundError) as exc:
        return {"ok": False, "error": str(exc), "warnings": warnings}

    root = _parse_xml(mdo_path).getroot()

    panels: dict[str, str] = {}
    section = root.find(_tag(root, "clientApplicationInterface"))
    if section is not None:
        for panel_el in section.findall(_tag(root, "panel")):
            name = (panel_el.findtext(_tag(root, "name")) or "").strip()
            location = (panel_el.findtext(_tag(root, "location")) or "").strip()
            if name:
                panels[name] = location

    home_page: list[list[str]] = []
    section = root.find(_tag(root, "homePageWorkArea"))
    if section is not None:
        for col_el in section.findall(_tag(root, "column")):
            forms = [
                (f.text or "").strip()
                for f in col_el.findall(_tag(root, "form"))
                if (f.text or "").strip()
            ]
            home_page.append(forms)

    return {
        "ok": True,
        "file": str(mdo_path),
        "panels": panels,
        "homePage": home_page,
        "warnings": warnings,
    }
