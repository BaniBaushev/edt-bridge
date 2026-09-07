# -*- coding: utf-8 -*-
"""Валидатор и планировщик JSON-DSL управляемой формы (M2).

Закрывает GAP-FORM-DSL, GAP-FORM-PRESETS, частично GAP-FORM-PROPS/BATCH/CALLTYPE:
из JSON-DSL (синтаксис unica form-compile: elements/commands/params/
conditionalAppearance, пресеты, shorthand-ключи) строится план операций:

- ``viaEdtMcp`` — вызовы ``create_metadata``/``modify_metadata`` через proxy
  (assignable-свойства и узлы, которые EDT-MCP умеет создавать);
- ``viaFile`` — фрагменты XML для ``Form.form`` (тонкие свойства: inputHint,
  titleLocation, pagesRepresentation, callType="ChangeAndValidate",
  условное оформление).

Планировщик ничего не применяет — только валидирует и возвращает план.
"""

from __future__ import annotations

import copy
from typing import Any

# ---------------------------------------------------------------------------
# Реестр событий (по form-compile/SKILL.md)
# ---------------------------------------------------------------------------

FORM_EVENTS = {
    "OnCreateAtServer", "OnOpen", "BeforeClose", "OnClose",
    "NotificationProcessing", "ChoiceProcessing", "ExternalEvent", "OnReopen",
    "OnMainServerAvailabilityChange", "OnReadAtServer", "BeforeWrite",
    "NewWriteProcessing", "FillCheckProcessingAtServer", "BeforeWriteAtServer",
    "OnWriteAtServer", "AfterWriteAtServer", "AfterWrite",
    "BeforeLoadDataFromSettingsAtServer", "OnLoadDataFromSettingsAtServer",
    "OnSaveDataInSettingsAtServer", "BeforeLoadUserSettingsAtServer",
    "OnLoadUserSettingsAtServer", "OnSaveUserSettingsAtServer",
    "OnUpdateUserSettingSetAtServer", "BeforeLoadVariantAtServer",
    "OnLoadVariantAtServer", "OnSaveVariantAtServer", "OnChangeDisplaySettings",
    "URLProcessing", "URLListGetProcessing", "URLGetProcessing",
    "NavigationProcessing",
}

# События, требующие главного реквизита постоянного объекта/записи.
MAIN_OBJECT_EVENTS = {
    "OnReadAtServer", "BeforeWrite", "BeforeWriteAtServer",
    "OnWriteAtServer", "AfterWriteAtServer", "AfterWrite",
}

ELEMENT_EVENTS: dict[str, set[str]] = {
    "input": {
        "OnChange", "StartChoice", "Clearing", "ChoiceProcessing",
        "AutoComplete", "TextEditEnd", "Opening", "Creating",
        "EditTextChange", "Tuning", "StartListChoice", "MultipleValuesDelete",
    },
    "check": {"OnChange"},
    "label": {"Click", "URLProcessing"},
    "labelField": {"URLProcessing", "Click", "OnChange"},
    "table": {
        "Selection", "OnActivateRow", "BeforeAddRow", "BeforeDeleteRow",
        "OnStartEdit", "OnChange", "BeforeRowChange", "AfterDeleteRow",
        "OnEditEnd", "OnActivateCell", "OnGetDataAtServer", "Drag",
        "DragCheck", "ValueChoice", "ChoiceProcessing", "DragStart",
        "BeforeEditEnd", "BeforeExpand", "DragEnd",
        "OnUpdateUserSettingSetAtServer", "BeforeCollapse",
        "BeforeLoadUserSettingsAtServer", "OnActivateField",
        "RefreshRequestProcessing", "NewWriteProcessing",
        "OnLoadUserSettingsAtServer", "OnCurrentParentChange",
        "OnSaveUserSettingsAtServer", "URLGetProcessing",
    },
    "pages": {"OnCurrentPageChange"},
    "page": set(),
    "button": set(),
    "cmdBar": set(),
    "autoCmdBar": set(),
    "group": set(),
}

# DSL-ключ типа элемента -> (XML-тег, FQN-токен вида для handler-FQN)
ELEMENT_KINDS: dict[str, dict[str, str]] = {
    "input": {"xml": "InputField", "fqn": "Field"},
    "check": {"xml": "CheckBoxField", "fqn": "Field"},
    "label": {"xml": "LabelDecoration", "fqn": "Decoration"},
    "labelField": {"xml": "LabelField", "fqn": "Field"},
    "table": {"xml": "Table", "fqn": "Table"},
    "pages": {"xml": "Pages", "fqn": "Pages"},
    "page": {"xml": "Page", "fqn": "Page"},
    "button": {"xml": "Button", "fqn": "Button"},
    "cmdBar": {"xml": "CommandBar", "fqn": "CommandBar"},
    "autoCmdBar": {"xml": "AutoCommandBar", "fqn": "AutoCommandBar"},
    "group": {"xml": "UsualGroup", "fqn": "UsualGroup"},
}

# callType, который EDT-MCP отклоняет для событий форм (GAP-FORM-CALLTYPE) —
# уходит только файловым каналом.
FILE_ONLY_CALL_TYPE = "ChangeAndValidate"
ALLOWED_EDT_CALL_TYPES = {"Before", "After", "Instead"}

# Свойства формы, assignable через modify_metadata (по доке EDT-MCP).
ASSIGNABLE_FORM_PROPS = {
    "autoTitle": "autoTitle",
    "windowOpeningMode": "windowOpeningMode",
    "commandBarLocation": "commandBarLocation",
    "saveDataInSettings": "saveDataInSettings",
    "width": "width",
    "height": "height",
}

# Тонкие свойства элементов — только файловым каналом (GAP-FORM-PROPS).
THIN_ELEMENT_PROPS = {"inputHint", "titleLocation", "pagesRepresentation"}

# Shorthand-ключи (GAP-FORM-PRESETS): не исполняются нативно, транслируем или
# предупреждаем.
SHORTHAND_MAP: dict[str, tuple[str, Any]] = {
    "hidden": ("visible", False),          # hidden:true -> visible:false
    "disabled": ("enabled", False),        # disabled:true -> enabled:false
    "collapsed": ("collapsed", True),
    "showLeftMargin": ("showLeftMargin", True),
    "behavior": ("behavior", None),        # значение берётся из DSL
    "commandSource": ("commandSource", None),
}

# ---------------------------------------------------------------------------
# Пресеты (GAP-FORM-PRESETS)
# ---------------------------------------------------------------------------

PRESETS: dict[str, dict[str, Any]] = {
    "dialog": {
        "title": "Диалог",
        "properties": {"autoTitle": True},
        "elements": [
            {"group": "horizontal", "name": "ГруппаКнопок", "children": [
                {"button": "ОК", "stdCommand": "Close", "defaultButton": True},
            ]},
        ],
    },
    "list-with-filter": {
        "elements": [
            {"group": "horizontal", "name": "Фильтр", "children": []},
            {"table": "Данные", "path": "Данные", "changeRowSet": True,
             "columns": []},
        ],
        "attributes": [
            {"name": "Данные", "type": "ValueTable", "columns": []},
        ],
    },
    "wizard": {
        "properties": {"autoTitle": False},
        "elements": [
            {"pages": "СтраницыМастера", "pagesRepresentation": "None",
             "children": []},
            {"group": "horizontal", "name": "Навигация", "children": [
                {"button": "Назад", "command": "Назад", "title": "< Назад"},
                {"button": "Далее", "command": "Далее", "title": "Далее >"},
            ]},
        ],
        "commands": [
            {"name": "Назад", "action": "НазадОбработка"},
            {"name": "Далее", "action": "ДалееОбработка"},
        ],
    },
}


class DslError(ValueError):
    """Ошибка валидации DSL формы."""


def _is_ident(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def expand_preset(dsl: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Раскрыть ``preset`` в DSL: слить фрагмент пресета под явные ключи.

    Возвращает (новый dsl, warnings). Явные ключи пользователя побеждают.
    """
    warnings: list[str] = []
    dsl = copy.deepcopy(dsl)
    preset_name = dsl.pop("preset", None)
    if preset_name is None:
        return dsl, warnings
    preset = PRESETS.get(preset_name)
    if preset is None:
        raise DslError(
            f"Неизвестный пресет {preset_name!r}. "
            f"Доступные: {', '.join(sorted(PRESETS))}."
        )
    merged = copy.deepcopy(preset)
    for key, value in dsl.items():
        if isinstance(value, list) and isinstance(merged.get(key), list):
            merged[key] = merged[key] + value
        elif isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    warnings.append(f"Применён пресет {preset_name!r}.")
    return merged, warnings


def _normalize_shorthands(
    item: dict[str, Any], warnings: list[str], where: str,
) -> None:
    """Транслировать shorthand-ключи элемента в канонические свойства."""
    for key in list(item):
        if key not in SHORTHAND_MAP:
            continue
        target, const = SHORTHAND_MAP[key]
        value = item.pop(key)
        if key in ("hidden", "disabled"):
            if value:  # hidden:true -> visible:false; false — без операции
                item[target] = const
        else:
            item[target] = value if const is None else bool(value)
        warnings.append(
            f"Shorthand-ключ {key!r} ({where}) транслирован в {target!r}."
        )


def _handler_name(element_name: str, event: str) -> str:
    """Автоимя обработчика: ОрганизацияПриИзменении."""
    return f"{element_name}{event}"


class _Planner:
    """Внутренний планировщик: собирает viaEdtMcp/viaFile из валидного DSL."""

    def __init__(self, object_fqn: str, form_name: str, mode: str) -> None:
        self.object_fqn = object_fqn
        self.form_name = form_name
        self.mode = mode
        self.form_fqn = f"{object_fqn}.Form.{form_name}"
        self.via_edt: list[dict[str, Any]] = []
        self.via_file: list[dict[str, Any]] = []
        self.warnings: list[str] = []
        self.errors: list[str] = []

    # -- helpers ------------------------------------------------------------

    def _create(self, fqn: str, **extra: Any) -> None:
        self.via_edt.append({"tool": "create_metadata",
                             "args": {"fqn": fqn, **extra}})

    def _modify(self, fqn: str, properties: dict[str, Any]) -> None:
        props = [{"name": k, "value": v} for k, v in properties.items()]
        self.via_edt.append({"tool": "modify_metadata",
                             "args": {"fqn": fqn, "properties": props}})

    def _file_fragment(self, action: str, element: str | None,
                       xml: str, note: str) -> None:
        self.via_file.append({
            "action": action,
            "element": element,
            "xml": xml,
            "note": note,
        })

    # -- секции DSL -----------------------------------------------------------

    def plan(self, dsl: dict[str, Any]) -> None:
        if self.mode == "create":
            self._create(self.form_fqn)

        props = dsl.get("properties") or {}
        assignable = {ASSIGNABLE_FORM_PROPS[k]: v
                      for k, v in props.items() if k in ASSIGNABLE_FORM_PROPS}
        if assignable:
            self._modify(self.form_fqn, assignable)
        title = dsl.get("title")
        if title:
            self._modify(self.form_fqn, {"synonym": title})
        for ev, handler in (dsl.get("events") or {}).items():
            self._plan_form_event(ev, handler)
        for cmd in dsl.get("commands") or []:
            self._plan_command(cmd)
        for param in (dsl.get("params") or dsl.get("parameters")) or []:
            self._plan_parameter(param)
        for el in dsl.get("elements") or []:
            self._plan_element(el, parent=None)
        ca = dsl.get("conditionalAppearance")
        if ca:
            self._file_fragment(
                "setConditionalAppearance", None,
                _render_conditional_appearance(ca),
                "Условное оформление — только файловый канал "
                "(GAP-FORM-PROPS).",
            )

    def _plan_form_event(self, event: str, handler: Any) -> None:
        if event not in FORM_EVENTS:
            self.errors.append(
                f"Неизвестное событие формы {event!r}. "
                f"Допустимые: {', '.join(sorted(FORM_EVENTS))}."
            )
            return
        call_type = None
        if isinstance(handler, dict):
            call_type = handler.get("callType")
            handler = handler.get("handler")
        if not _is_ident(handler):
            self.errors.append(f"Событие {event!r}: пустое имя обработчика.")
            return
        fqn = f"{self.form_fqn}.Handler.{event}"
        if call_type == FILE_ONLY_CALL_TYPE:
            self._file_fragment(
                "setEventCallType", None,
                _render_event_handler_ext(event, handler, call_type),
                "callType=ChangeAndValidate отклоняется EDT-MCP "
                "(GAP-FORM-CALLTYPE) — файловая правка Form.form.",
            )
        elif call_type:
            if call_type not in ALLOWED_EDT_CALL_TYPES:
                self.errors.append(f"Недопустимый callType {call_type!r}.")
                return
            self._create(fqn, callType=call_type,
                         properties=[{"name": "procedure", "value": handler}])
        else:
            self._create(fqn,
                         properties=[{"name": "procedure", "value": handler}])

    def _plan_command(self, cmd: dict[str, Any]) -> None:
        name = cmd.get("name")
        if not _is_ident(name):
            self.errors.append("Команда без имени.")
            return
        self._create(f"{self.form_fqn}.Command.{name}")
        props: dict[str, Any] = {}
        if cmd.get("title"):
            props["synonym"] = cmd["title"]
        if cmd.get("shortcut"):
            props["shortcut"] = cmd["shortcut"]
        if props:
            self._modify(f"{self.form_fqn}.Command.{name}", props)
        action = cmd.get("action")
        if action:
            self._create(
                f"{self.form_fqn}.Command.{name}.Handler.Action",
                properties=[{"name": "procedure", "value": action}],
            )

    def _plan_parameter(self, param: dict[str, Any]) -> None:
        name = param.get("name")
        if not _is_ident(name):
            self.errors.append("Параметр формы без имени.")
            return
        self._create(f"{self.form_fqn}.Parameter.{name}")
        if param.get("key"):
            self._modify(f"{self.form_fqn}.Parameter.{name}", {"key": True})

    def _plan_element(self, el: dict[str, Any], parent: str | None) -> None:
        _normalize_shorthands(el, self.warnings, parent or "корень")
        kind_key = next((k for k in el if k in ELEMENT_KINDS), None)
        if kind_key is None:
            self.errors.append(
                f"Элемент без типа (нет ключа из "
                f"{', '.join(sorted(ELEMENT_KINDS))}): {el!r}"
            )
            return
        raw = el[kind_key]
        name = el.get("name")
        if not _is_ident(name):
            if kind_key == "group" and raw in (
                "horizontal", "vertical", "alwaysHorizontal",
                "alwaysVertical", "collapsible",
            ):
                self.errors.append(
                    "Группа: ключ 'group' задаёт ориентацию, имя обязательно "
                    "через 'name'."
                )
                return
            if not _is_ident(raw):
                self.errors.append(f"Элемент {kind_key!r} без имени.")
                return
            name = raw
        kind = ELEMENT_KINDS[kind_key]
        fqn = f"{self.form_fqn}.{kind['fqn']}.{name}"

        # Создание элемента и assignable-свойства — через EDT-MCP.
        create_extra: dict[str, Any] = {}
        if parent:
            create_extra["properties"] = [
                {"name": "parent", "value": parent}
            ]
        self._create(fqn, **create_extra)
        props: dict[str, Any] = {}
        for key, prop in (
            ("visible", "visible"), ("enabled", "enabled"),
            ("readOnly", "readOnly"), ("title", "synonym"),
            ("width", "width"), ("height", "height"),
        ):
            if key in el:
                props[prop] = el[key]
        if el.get("path"):
            props["dataPath"] = el["path"]
        if props:
            self._modify(fqn, props)

        # Тонкие свойства — файловым каналом (GAP-FORM-PROPS).
        thin = {k: el[k] for k in THIN_ELEMENT_PROPS if k in el}
        if thin:
            self._file_fragment(
                "setThinProperties", name,
                _render_thin_properties(thin),
                f"Тонкие свойства {', '.join(sorted(thin))} — файловая "
                "правка Form.form (GAP-FORM-PROPS).",
            )

        # События элемента.
        handlers = dict(el.get("handlers") or {})
        for on in el.get("on") or []:
            if isinstance(on, dict):
                handlers[on["event"]] = {
                    "handler": _handler_name(name, on["event"]),
                    "callType": on.get("callType"),
                }
            else:
                handlers.setdefault(on, _handler_name(name, on))
        for event, handler in handlers.items():
            self._plan_element_event(fqn, kind_key, name, event, handler)

        # Кнопки: привязка к команде.
        if kind_key == "button":
            if el.get("command"):
                self._modify(fqn, {"commandName": el["command"]})
            elif el.get("stdCommand"):
                self._modify(fqn, {"commandName": el["stdCommand"]})

        for child in el.get("children") or []:
            self._plan_element(child, parent=name)
        for child in el.get("columns") or []:
            self._plan_element(child, parent=name)

    def _plan_element_event(self, fqn: str, kind_key: str, name: str,
                            event: str, handler: Any) -> None:
        allowed = ELEMENT_EVENTS[kind_key]
        if event not in allowed:
            self.errors.append(
                f"Событие {event!r} недопустимо для элемента {kind_key!r} "
                f"({name}). Допустимые: {', '.join(sorted(allowed)) or '—'}."
            )
            return
        call_type = None
        if isinstance(handler, dict):
            call_type = handler.get("callType")
            handler = handler.get("handler")
        hfqn = f"{fqn}.Handler.{event}"
        if call_type == FILE_ONLY_CALL_TYPE:
            self._file_fragment(
                "setEventCallType", name,
                _render_event_handler_ext(event, handler, call_type),
                "callType=ChangeAndValidate — файловая правка Form.form "
                "(GAP-FORM-CALLTYPE).",
            )
        elif call_type:
            if call_type not in ALLOWED_EDT_CALL_TYPES:
                self.errors.append(f"Недопустимый callType {call_type!r}.")
                return
            self._create(hfqn, callType=call_type,
                         properties=[{"name": "procedure", "value": handler}])
        else:
            self._create(hfqn,
                         properties=[{"name": "procedure", "value": handler}])


# ---------------------------------------------------------------------------
# Рендер XML-фрагментов (viaFile)
# ---------------------------------------------------------------------------

def _xml_escape(value: Any) -> str:
    return (str(value).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _render_thin_properties(props: dict[str, Any]) -> str:
    lines = []
    for key, value in sorted(props.items()):
        lines.append(f"<{key}>{_xml_escape(value)}</{key}>")
    return "\n".join(lines)


def _render_event_handler_ext(event: str, handler: str, call_type: str) -> str:
    return (
        '<handlers xsi:type="form:EventHandlerExtension">\n'
        f"  <event>{_xml_escape(event)}</event>\n"
        f"  <name>{_xml_escape(handler)}</name>\n"
        f"  <callType>{_xml_escape(call_type)}</callType>\n"
        "</handlers>"
    )


def _render_conditional_appearance(ca: list[dict[str, Any]]) -> str:
    """Условное оформление формы → фрагмент <conditionalAppearance>."""
    items = []
    for entry in ca:
        fields = "".join(
            f"\n      <fields>{_xml_escape(f)}</fields>"
            for f in entry.get("fields", [])
        )
        appearance = "".join(
            f'\n      <appearance key="{_xml_escape(k)}" '
            f'value="{_xml_escape(v)}"/>'
            for k, v in (entry.get("appearance") or {}).items()
        )
        items.append(
            "    <items>\n"
            f"      <filter>{_xml_escape(entry.get('filter', ''))}</filter>"
            f"{fields}{appearance}\n"
            "    </items>"
        )
    return ("<conditionalAppearance>\n" + "\n".join(items)
            + "\n</conditionalAppearance>")


# ---------------------------------------------------------------------------
# Публичный API
# ---------------------------------------------------------------------------

def compile_dsl(
    object_fqn: str,
    form_name: str,
    dsl: dict[str, Any],
    mode: str = "create",
) -> dict[str, Any]:
    """Провалидировать DSL и построить план операций.

    :param object_fqn: FQN владельца формы, например ``Catalog.Валюты``.
    :param form_name: имя формы.
    :param dsl: JSON-DSL (синтаксис unica form-compile).
    :param mode: ``create`` (форма создаётся) | ``patch`` (правка существующей).
    :return: ``{ok, errors, warnings, plan: {viaEdtMcp, viaFile}}``.
    """
    result: dict[str, Any] = {
        "ok": False,
        "errors": [],
        "warnings": [],
        "plan": {"viaEdtMcp": [], "viaFile": []},
    }
    if mode not in ("create", "patch"):
        result["errors"].append(f"Недопустимый mode {mode!r}: create|patch.")
        return result
    if not isinstance(dsl, dict):
        result["errors"].append("DSL должен быть JSON-объектом.")
        return result
    if not _is_ident(form_name):
        result["errors"].append("Пустое имя формы.")
        return result

    try:
        dsl, preset_warnings = expand_preset(dsl)
    except DslError as exc:
        result["errors"].append(str(exc))
        return result
    result["warnings"].extend(preset_warnings)

    known_keys = {
        "title", "properties", "events", "excludedCommands", "elements",
        "attributes", "commands", "params", "parameters",
        "conditionalAppearance", "preset",
    }
    for key in dsl:
        if key not in known_keys:
            result["warnings"].append(f"Нераспознанный ключ DSL: {key!r}.")

    planner = _Planner(object_fqn, form_name, mode)
    planner.plan(dsl)

    result["errors"].extend(planner.errors)
    result["warnings"].extend(planner.warnings)
    result["plan"]["viaEdtMcp"] = planner.via_edt
    result["plan"]["viaFile"] = planner.via_file
    result["ok"] = not planner.errors
    return result
