# -*- coding: utf-8 -*-
"""Генерация и патч фрагментов ``Form.form`` (XML формата EDT).

Тонкие свойства, не покрытые EDT-MCP (GAP-FORM-PROPS, GAP-FORM-CALLTYPE):
``inputHint``, ``titleLocation``, ``pagesRepresentation``,
``callType="ChangeAndValidate"``, условное оформление.

Работа через lxml, аккуратное слияние с существующим файлом: фрагмент
применяется к узлу элемента (по ``<name>``), повторное применение идемпотентно.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lxml import etree

NS_FORM = "http://g5.1c.ru/v8/dt/form"
NS_XSI = "http://www.w3.org/2001/XMLSchema-instance"
NSMAP = {"form": NS_FORM, "xsi": NS_XSI}


class FormXmlError(ValueError):
    """Ошибка разбора или патча Form.form."""


# ---------------------------------------------------------------------------
# Базовые операции
# ---------------------------------------------------------------------------

def parse_form_xml(source: str | bytes | Path) -> etree._ElementTree:
    """Разобрать Form.form (путь или текст) в дерево lxml."""
    if isinstance(source, Path) or (
        isinstance(source, str) and not source.lstrip().startswith("<")
    ):
        parser = etree.XMLParser(remove_blank_text=False)
        return etree.parse(str(source), parser)
    data = source.encode("utf-8") if isinstance(source, str) else source
    parser = etree.XMLParser(remove_blank_text=False)
    return etree.fromstring(data, parser).getroottree()


def serialize(tree: etree._ElementTree) -> str:
    """Сериализовать дерево обратно в текст Form.form."""
    return etree.tostring(
        tree, xml_declaration=True, encoding="UTF-8",
        pretty_print=True,
    ).decode("utf-8")


def find_item(tree: etree._ElementTree, name: str) -> etree._Element | None:
    """Найти узел элемента формы (items/autoCommandBar) по имени."""
    for node in tree.getroot().iter(f"{{{NS_FORM}}}items", f"{{{NS_FORM}}}autoCommandBar", "items", "autoCommandBar"):
        child = node.find(f"{{{NS_FORM}}}name")
        if child is None:
            child = node.find("name")
        if child is not None and child.text == name:
            return node
    return None


def _child(parent: etree._Element, tag: str) -> etree._Element | None:
    node = parent.find(tag)
    if node is None:
        node = parent.find(f"{{{NS_FORM}}}{tag}")
    return node


def _new_tag(tree: etree._ElementTree, tag: str) -> str:
    """Имя тега в стиле документа: EDT пишет дочерние узлы без префикса."""
    root = tree.getroot()
    if root.find(f"{{{NS_FORM}}}items") is not None:
        return f"{{{NS_FORM}}}{tag}"
    return tag


def _ensure_child(parent: etree._Element, tag: str) -> etree._Element:
    node = _child(parent, tag)
    if node is None:
        node = etree.SubElement(parent, _new_tag(parent.getroottree(), tag))
    return node


def _set_scalar(parent: etree._Element, tag: str, value: Any) -> bool:
    """Установить скалярный подузел; вернуть True если значение изменилось."""
    node = _ensure_child(parent, tag)
    text = str(value) if not isinstance(value, bool) else str(value).lower()
    if node.text == text:
        return False
    node.text = text
    return True


def next_id(tree: etree._ElementTree) -> int:
    """Следующий свободный id элементов формы (max+1, min 1)."""
    max_id = 0
    for node in tree.getroot().iter():
        id_node = _child(node, "id") if node.tag.endswith("items") or node.tag == "items" else None
        if id_node is not None and id_node.text and id_node.text.lstrip("-").isdigit():
            max_id = max(max_id, int(id_node.text))
    return max_id + 1


# ---------------------------------------------------------------------------
# Тонкие свойства
# ---------------------------------------------------------------------------

def set_input_hint(tree: etree._ElementTree, element: str, hint: str) -> bool:
    """Установить ``inputHint`` на поле ввода (extInfo InputFieldExtInfo)."""
    node = find_item(tree, element)
    if node is None:
        raise FormXmlError(f"Элемент {element!r} не найден в Form.form.")
    ext_info = _child(node, "extInfo")
    if ext_info is None:
        ext_info = etree.SubElement(node, _new_tag(node.getroottree(), "extInfo"))
        ext_info.set(f"{{{NS_XSI}}}type", "form:InputFieldExtInfo")
    return _set_scalar(ext_info, "inputHint", hint)


def set_title_location(tree: etree._ElementTree, element: str,
                       location: str) -> bool:
    """Установить ``titleLocation`` элемента формы."""
    allowed = {"None", "Left", "Top", "Right", "Bottom", "Auto",
               "none", "left", "top", "right", "bottom", "auto"}
    if location not in allowed:
        raise FormXmlError(
            f"Недопустимый titleLocation {location!r}; допустимые: "
            "None/Left/Top/Right/Bottom/Auto."
        )
    node = find_item(tree, element)
    if node is None:
        raise FormXmlError(f"Элемент {element!r} не найден в Form.form.")
    canonical = location.capitalize()
    return _set_scalar(node, "titleLocation", canonical)


def set_pages_representation(tree: etree._ElementTree, element: str,
                             representation: str) -> bool:
    """Установить ``pagesRepresentation`` на элементе Pages."""
    node = find_item(tree, element)
    if node is None:
        raise FormXmlError(f"Элемент {element!r} не найден в Form.form.")
    return _set_scalar(node, "pagesRepresentation", representation)


# ---------------------------------------------------------------------------
# callType="ChangeAndValidate" (GAP-FORM-CALLTYPE)
# ---------------------------------------------------------------------------

def set_event_call_type(
    tree: etree._ElementTree,
    event: str,
    handler: str,
    call_type: str = "ChangeAndValidate",
    element: str | None = None,
) -> bool:
    """Добавить ``form:EventHandlerExtension`` с заданным callType.

    Идемпотентно: существующий handler с тем же (event, callType) лишь
    обновляет имя процедуры.
    """
    allowed = {"Before", "After", "Override", "ChangeAndValidate"}
    if call_type == "Instead":  # UI-метка; на диске — Override
        call_type = "Override"
    if call_type not in allowed:
        raise FormXmlError(f"Недопустимый callType {call_type!r}.")
    owner = find_item(tree, element) if element else tree.getroot()
    if owner is None:
        raise FormXmlError(f"Элемент {element!r} не найден в Form.form.")
    for node in owner.findall(f"{{{NS_FORM}}}handlers") + owner.findall("handlers"):
        ev = _child(node, "event")
        ct = _child(node, "callType")
        if ev is not None and ev.text == event and (
            ct is not None and ct.text == call_type
        ):
            return _set_scalar(node, "name", handler)
    tree = owner.getroottree()
    node = etree.SubElement(owner, _new_tag(tree, "handlers"))
    node.set(f"{{{NS_XSI}}}type", "form:EventHandlerExtension")
    etree.SubElement(node, _new_tag(tree, "event")).text = event
    etree.SubElement(node, _new_tag(tree, "name")).text = handler
    etree.SubElement(node, _new_tag(tree, "callType")).text = call_type
    return True


# ---------------------------------------------------------------------------
# Условное оформление
# ---------------------------------------------------------------------------

def set_conditional_appearance(
    tree: etree._ElementTree,
    entries: list[dict[str, Any]],
) -> bool:
    """Заменить/установить секцию ``conditionalAppearance`` формы."""
    root = tree.getroot()
    old = _child(root, "conditionalAppearance")
    if old is not None:
        root.remove(old)
    if not entries:
        return old is not None
    ca = etree.SubElement(root, _new_tag(tree, "conditionalAppearance"))
    for entry in entries:
        item = etree.SubElement(ca, _new_tag(tree, "items"))
        etree.SubElement(item, _new_tag(tree, "filter")).text = str(
            entry.get("filter", "")
        )
        for field in entry.get("fields", []):
            etree.SubElement(item, _new_tag(tree, "fields")).text = str(field)
        for key, value in (entry.get("appearance") or {}).items():
            ap = etree.SubElement(item, _new_tag(tree, "appearance"))
            ap.set("key", str(key))
            ap.set("value", str(value))
    return True


# ---------------------------------------------------------------------------
# Применение фрагментов плана (viaFile)
# ---------------------------------------------------------------------------

def apply_fragment(
    tree: etree._ElementTree, fragment: dict[str, Any],
) -> dict[str, Any]:
    """Применить один фрагмент плана (из form_dsl.compile_dsl plan.viaFile).

    :return: ``{changed, warnings}``.
    """
    warnings: list[str] = []
    action = fragment.get("action")
    element = fragment.get("element")
    changed = False

    if action == "setThinProperties":
        if not element:
            raise FormXmlError("setThinProperties: не указан элемент.")
        # Разобрать фрагмент вида <inputHint>..</inputHint>\n<...>
        wrapper = etree.fromstring(
            f'<w xmlns:form="{NS_FORM}">{fragment["xml"]}</w>'
        )
        for prop in wrapper:
            tag = etree.QName(prop).localname
            if tag == "inputHint":
                changed |= set_input_hint(tree, element, prop.text or "")
            elif tag == "titleLocation":
                changed |= set_title_location(tree, element, prop.text or "")
            elif tag == "pagesRepresentation":
                changed |= set_pages_representation(
                    tree, element, prop.text or ""
                )
            else:
                warnings.append(f"Неизвестное тонкое свойство {tag!r}.")
    elif action == "setEventCallType":
        wrapper = etree.fromstring(
            f'<w xmlns:form="{NS_FORM}" xmlns:xsi="{NS_XSI}">'
            f'{fragment["xml"]}</w>'
        )
        node = wrapper[0]
        event = node.findtext("event") or node.findtext(f"{{{NS_FORM}}}event")
        handler = node.findtext("name") or node.findtext(f"{{{NS_FORM}}}name")
        call_type = (node.findtext("callType")
                     or node.findtext(f"{{{NS_FORM}}}callType"))
        changed = set_event_call_type(tree, event, handler, call_type,
                                      element=element)
    elif action == "setConditionalAppearance":
        wrapper = etree.fromstring(
            f'<w xmlns:form="{NS_FORM}">{fragment["xml"]}</w>'
        )
        entries: list[dict[str, Any]] = []
        for item in wrapper[0]:
            entries.append({
                "filter": item.findtext("filter") or "",
                "fields": [f.text for f in item.findall("fields")],
                "appearance": {
                    ap.get("key"): ap.get("value")
                    for ap in item.findall("appearance")
                },
            })
        changed = set_conditional_appearance(tree, entries)
    else:
        raise FormXmlError(f"Неизвестное действие фрагмента: {action!r}.")

    return {"changed": changed, "warnings": warnings}


# ---------------------------------------------------------------------------
# Разбор формы (edtb_form_info, headless)
# ---------------------------------------------------------------------------

_XSI_TYPE = f"{{{NS_XSI}}}type"

# XML xsi:type -> (вид элемента для отчёта)
_ITEM_KINDS = {
    "form:FormField": "field",
    "form:FormGroup": "group",
    "form:Decoration": "decoration",
    "form:Table": "table",
    "form:Button": "button",
}


def parse_form_info(tree: etree._ElementTree) -> dict[str, Any]:
    """Разбор Form.form → {elements, commands, params, events}.

    Headless: работает только с файлом, без скриншотов/UI.
    """
    root = tree.getroot()
    elements: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []

    def walk(node: etree._Element, parent: str | None) -> None:
        name_node = _child(node, "name")
        name = name_node.text if name_node is not None else None
        xsi_type = node.get(_XSI_TYPE, "")
        entry: dict[str, Any] = {
            "name": name,
            "kind": _ITEM_KINDS.get(xsi_type, xsi_type or node.tag),
            "parent": parent,
        }
        dp = _child(node, "dataPath")
        if dp is not None:
            seg = _child(dp, "segments")
            entry["dataPath"] = seg.text if seg is not None else None
        title = _child(node, "title")
        if title is not None:
            value = _child(title, "value")
            if value is not None:
                entry["title"] = value.text
        for handler in list(node.findall(f"{{{NS_FORM}}}handlers")) + \
                list(node.findall("handlers")):
            ev = _child(handler, "event")
            nm = _child(handler, "name")
            ct = _child(handler, "callType")
            events.append({
                "element": name,
                "event": ev.text if ev is not None else None,
                "handler": nm.text if nm is not None else None,
                "callType": ct.text if ct is not None else None,
            })
        elements.append(entry)
        for child in list(node.findall(f"{{{NS_FORM}}}items")) + \
                list(node.findall("items")):
            walk(child, name)

    for top in root.findall(f"{{{NS_FORM}}}items") + root.findall("items"):
        walk(top, None)
    acb = _child(root, "autoCommandBar")
    if acb is not None:
        nm = _child(acb, "name")
        elements.append({
            "name": nm.text if nm is not None else "FormCommandBar",
            "kind": "autoCommandBar",
            "parent": None,
        })

    commands: list[dict[str, Any]] = []
    for cmd in root.findall(f"{{{NS_FORM}}}commands") + root.findall("commands"):
        nm = _child(cmd, "name")
        action = _child(cmd, "action")
        commands.append({
            "name": nm.text if nm is not None else None,
            "action": action.text if action is not None else None,
        })

    params: list[dict[str, Any]] = []
    for par in (root.findall(f"{{{NS_FORM}}}parameters")
                + root.findall("parameters")):
        nm = _child(par, "name")
        key = _child(par, "key")
        params.append({
            "name": nm.text if nm is not None else None,
            "key": key is not None and key.text == "true",
        })

    # События уровня формы (handlers на корне).
    for handler in list(root.findall(f"{{{NS_FORM}}}handlers")) + \
            list(root.findall("handlers")):
        ev = _child(handler, "event")
        nm = _child(handler, "name")
        ct = _child(handler, "callType")
        events.append({
            "element": None,
            "event": ev.text if ev is not None else None,
            "handler": nm.text if nm is not None else None,
            "callType": ct.text if ct is not None else None,
        })

    return {
        "elements": elements,
        "commands": commands,
        "params": params,
        "events": events,
    }
