"""xpath-подобный сеттер XML-файлов для op patchFile.

Поддерживаемые действия в changes:
- ``{xpath, text}``               — установить текст элемента(ов);
- ``{xpath, attribute, value}``   — установить атрибут элемента(ов);
- ``{xpath, remove: true}``       — удалить элемент(ы) или атрибут
  (``{xpath, attribute, remove: true}``).

xpath исполняется через lxml ``ElementTree.xpath``; для XML с namespace
используйте ``*[local-name()='Имя']``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lxml import etree


class PatchError(Exception):
    """Ошибка применения patchFile к XML-файлу."""


def apply_changes_to_xml(xml_bytes: bytes, changes: list[dict[str, Any]]) -> bytes:
    """Применить сетку изменений к XML-документу, вернуть новые байты."""
    parser = etree.XMLParser(remove_blank_text=False)
    try:
        tree = etree.fromstring(xml_bytes, parser)
    except etree.XMLSyntaxError as exc:
        raise PatchError(f"XML не парсится: {exc}") from exc
    root = tree if isinstance(tree, etree._Element) else tree.getroot()
    tree_obj = root.getroottree()

    for i, ch in enumerate(changes):
        xpath = ch["xpath"]
        try:
            nodes = tree_obj.xpath(xpath)
        except etree.XPathError as exc:
            raise PatchError(f"changes[{i}]: некорректный xpath {xpath!r}: {exc}") from exc
        nodes = [n for n in nodes if isinstance(n, etree._Element)]
        if not nodes:
            raise PatchError(f"changes[{i}]: xpath {xpath!r} не нашёл элементов")
        for node in nodes:
            if "attribute" in ch:
                attr = ch["attribute"]
                if ch.get("remove"):
                    node.attrib.pop(attr, None)
                else:
                    node.set(attr, str(ch.get("value", "")))
            elif ch.get("remove"):
                parent = node.getparent()
                if parent is None:
                    raise PatchError(f"changes[{i}]: нельзя удалить корневой элемент")
                parent.remove(node)
            elif "text" in ch:
                node.text = str(ch["text"])
            else:  # pragma: no cover - перехвачено валидацией плана
                raise PatchError(f"changes[{i}]: нет действия (text/attribute/remove)")
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8")


def patch_file(path: Path, changes: list[dict[str, Any]]) -> str:
    """Применить changes к файлу на диске. Возвращает новое содержимое."""
    if not path.exists():
        raise PatchError(f"файл не существует: {path}")
    new_bytes = apply_changes_to_xml(path.read_bytes(), changes)
    path.write_bytes(new_bytes)
    return new_bytes.decode("utf-8")


def patch_file_preview(path: Path, changes: list[dict[str, Any]]) -> str:
    """Спроецировать результат patchFile без записи на диск (dryRun)."""
    if not path.exists():
        raise PatchError(f"файл не существует: {path}")
    return apply_changes_to_xml(path.read_bytes(), changes).decode("utf-8")
