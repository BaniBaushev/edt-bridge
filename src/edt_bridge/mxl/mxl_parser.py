"""Парсер файла макета табличного документа 1С (.mxl/.mxlx, XML-формат EDT).

Преобразует XML (namespace http://v8.1c.ru/8.2/data/spreadsheet) в компактный
JSON-DSL (см. references mxl-dsl-spec): columns, columnWidths, fonts, styles,
areas, pageSetup. Нераспознанные конструкции не приводят к падению — они
складываются в ``raw`` с пояснением в ``warnings`` (tolerant-режим).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

NS_D = "http://v8.1c.ru/8.2/data/spreadsheet"  # документ
NS_V8 = "http://v8.1c.ru/8.1/data/core"
NS_V8UI = "http://v8.1c.ru/8.1/data/ui"

# Элементы верхнего уровня, которые парсер понимает. Остальное → raw + warning.
_KNOWN_ROOT_TAGS = {
    "languageSettings", "columns", "rowsItem", "templateMode",
    "defaultFormatIndex", "height", "vgRows", "merge", "namedItem",
    "line", "font", "format", "pageSetup", "columnsID",
}

_ALIGN_TO_DSL = {"Left": "left", "Center": "center", "Right": "right",
                 "Justify": "justify"}
_VALIGN_TO_DSL = {"Top": "top", "Center": "center", "Bottom": "bottom"}
_FILL_TYPE_TO_DSL = {"Parameter": "param", "Template": "template", "Text": "text"}


def _local(tag: str) -> str:
    """Имя тега без namespace."""
    return tag.rsplit("}", 1)[-1]


def _child(node: ET.Element, name: str) -> ET.Element | None:
    for ch in node:
        if _local(ch.tag) == name:
            return ch
    return None


def _child_text(node: ET.Element, name: str, default: str = "") -> str:
    ch = _child(node, name)
    if ch is None or ch.text is None:
        return default
    return ch.text


def _child_int(node: ET.Element, name: str) -> int | None:
    txt = _child_text(node, name).strip()
    if not txt:
        return None
    try:
        return int(txt)
    except ValueError:
        return None


def _bool_attr(node: ET.Element, name: str) -> bool:
    return (node.get(name) or "").lower() == "true"


def _serialize(node: ET.Element) -> str:
    return ET.tostring(node, encoding="unicode")


class MxlParser:
    """Tolerant-парсер MXL XML → JSON-DSL."""

    def __init__(self) -> None:
        self.warnings: list[str] = []
        self.raw: list[dict[str, str]] = []
        self.fonts_raw: list[dict[str, Any]] = []
        self.lines_raw: list[int] = []          # ширины линий палитры бордюров
        self.formats_raw: list[dict[str, Any]] = []
        self.rows: dict[int, dict[str, Any]] = {}  # global_row -> row-данные
        self.merges: list[dict[str, int]] = []
        self.named_items: list[dict[str, Any]] = []

    # ------------------------------------------------------------------ API
    def parse(self, text: str) -> dict[str, Any]:
        """Разобрать содержимое файла макета в JSON-DSL (dict)."""
        text = text.strip()
        if not text:
            return self._result_empty("пустое содержимое макета")
        if not text.startswith("<"):
            # Унаследованный бинарно-текстовый формат платформы не поддержан.
            return self._result_empty(
                "файл не является XML-форматом табличного документа EDT "
                "(возможно, унаследованный двоичный .mxl); "
                "поддерживается XML (Template.mxl/Template.mxlx)"
            )
        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            return self._result_empty(f"ошибка разбора XML: {exc}")
        if _local(root.tag) != "document":
            self.warnings.append(
                f"корневой элемент '{_local(root.tag)}' вместо 'document' — "
                "продолжаю в tolerant-режиме"
            )

        for ch in root:
            name = _local(ch.tag)
            if name not in _KNOWN_ROOT_TAGS:
                self.raw.append({"tag": name, "xml": _serialize(ch)})
                self.warnings.append(
                    f"нераспознанный элемент верхнего уровня '{name}' — "
                    "сохранён в raw"
                )

        self._parse_fonts(root)
        self._parse_lines(root)
        self._parse_formats(root)
        self._parse_rows(root)
        self._parse_merges(root)
        self._parse_named_items(root)

        return self._build_dsl(root)

    # ------------------------------------------------------- палитры
    def _parse_fonts(self, root: ET.Element) -> None:
        for f in root:
            if _local(f.tag) != "font":
                continue
            try:
                size = int(f.get("height") or "0")
            except ValueError:
                size = 0
                self.warnings.append("шрифт с нечисловым height — принят 0")
            self.fonts_raw.append({
                "face": f.get("faceName") or "Arial",
                "size": size,
                "bold": _bool_attr(f, "bold"),
                "italic": _bool_attr(f, "italic"),
                "underline": _bool_attr(f, "underline"),
                "strikeout": _bool_attr(f, "strikeout"),
            })

    def _parse_lines(self, root: ET.Element) -> None:
        for ln in root:
            if _local(ln.tag) != "line":
                continue
            try:
                self.lines_raw.append(int(ln.get("width") or "0"))
            except ValueError:
                self.lines_raw.append(0)
                self.warnings.append("линия бордюра с нечисловой width — принята 0")

    def _parse_formats(self, root: ET.Element) -> None:
        for fmt in root:
            if _local(fmt.tag) != "format":
                continue
            entry: dict[str, Any] = {
                "font": _child_int(fmt, "font"),
                "leftBorder": _child_int(fmt, "leftBorder"),
                "topBorder": _child_int(fmt, "topBorder"),
                "rightBorder": _child_int(fmt, "rightBorder"),
                "bottomBorder": _child_int(fmt, "bottomBorder"),
                "width": _child_int(fmt, "width") or 0,
                "height": _child_int(fmt, "height") or 0,
                "ha": _child_text(fmt, "horizontalAlignment"),
                "va": _child_text(fmt, "verticalAlignment"),
                "wrap": _child_text(fmt, "textPlacement") == "Wrap",
                "fillType": _child_text(fmt, "fillType"),
                "dataFormat": "",
                "backColor": _child_text(fmt, "backColor"),
                "textColor": _child_text(fmt, "textColor"),
            }
            inner = _child(fmt, "format")
            if inner is not None:
                for desc in inner.iter():
                    if _local(desc.tag) == "content" and desc.text:
                        entry["dataFormat"] = desc.text
                        break
            known = {"font", "leftBorder", "topBorder", "rightBorder",
                     "bottomBorder", "width", "height", "horizontalAlignment",
                     "verticalAlignment", "textPlacement", "fillType",
                     "format", "backColor", "textColor"}
            for ch in fmt:
                if _local(ch.tag) not in known:
                    self.raw.append({"tag": f"format/{_local(ch.tag)}",
                                     "xml": _serialize(ch)})
                    self.warnings.append(
                        f"нераспознанное свойство формата '{_local(ch.tag)}' — "
                        "сохранено в raw"
                    )
            self.formats_raw.append(entry)

    # ------------------------------------------------------- строки/ячейки
    def _parse_rows(self, root: ET.Element) -> None:
        for ri in root:
            if _local(ri.tag) != "rowsItem":
                continue
            index = _child_int(ri, "index")
            if index is None:
                self.warnings.append("rowsItem без index — пропущен")
                continue
            index_to = _child_int(ri, "indexTo")
            row_node = _child(ri, "row")
            if row_node is None:
                self.warnings.append(f"rowsItem index={index} без <row> — пропущен")
                continue
            row_data = self._parse_row(row_node, index)
            last = index_to if index_to is not None and index_to >= index else index
            for r in range(index, last + 1):
                self.rows[r] = row_data

    def _parse_row(self, row_node: ET.Element, index: int) -> dict[str, Any]:
        row: dict[str, Any] = {"formatIndex": 0, "cells": []}
        if _child_text(row_node, "empty") == "true":
            row["empty"] = True
            return row
        fmt_idx = _child_int(row_node, "formatIndex")
        if fmt_idx:
            row["formatIndex"] = fmt_idx
        if _child(row_node, "columnsID") is not None:
            self.warnings.append(
                f"строка {index}: columnsID (наборы колонок) не поддержан — "
                "игнорируется"
            )
        for c in row_node:
            if _local(c.tag) != "c":
                continue
            inner = _child(c, "c")
            if inner is None:
                continue
            col = _child_int(c, "i")
            cell: dict[str, Any] = {
                "col": col,  # None → следующая свободная позиция (0-based)
                "f": _child_int(inner, "f") or 0,
                "param": _child_text(inner, "parameter") or None,
                "detail": _child_text(inner, "detailParameter") or None,
                "text": None,
            }
            tl = _child(inner, "tl")
            if tl is not None:
                for desc in tl.iter():
                    if _local(desc.tag) == "content" and desc.text is not None:
                        cell["text"] = desc.text
                        break
            row["cells"].append(cell)
        return row

    def _parse_merges(self, root: ET.Element) -> None:
        for m in root:
            if _local(m.tag) != "merge":
                continue
            r = _child_int(m, "r")
            c = _child_int(m, "c")
            w = _child_int(m, "w")
            h = _child_int(m, "h")
            if r is None or c is None or w is None:
                self.warnings.append("merge без r/c/w — пропущен")
                continue
            self.merges.append({"r": r, "c": c, "w": w, "h": h or 0})

    def _parse_named_items(self, root: ET.Element) -> None:
        for ni in root:
            if _local(ni.tag) != "namedItem":
                continue
            name = _child_text(ni, "name")
            area = _child(ni, "area")
            if area is None:
                self.warnings.append(f"область '{name}' без <area> — пропущена")
                continue
            item = {
                "name": name,
                "type": _child_text(area, "type") or "Rows",
                "beginRow": _child_int(area, "beginRow") or 0,
                "endRow": _child_int(area, "endRow") or 0,
                "beginColumn": _child_int(area, "beginColumn"),
                "endColumn": _child_int(area, "endColumn"),
            }
            if item["type"] != "Rows":
                self.warnings.append(
                    f"область '{name}' типа '{item['type']}' — поддерживается "
                    "только Rows; область сохранена частично"
                )
            self.named_items.append(item)

    # ------------------------------------------------------- сборка DSL
    def _format(self, idx: int) -> dict[str, Any] | None:
        """Формат палитры по 1-based индексу (0 = формат по умолчанию)."""
        if idx <= 0 or idx > len(self.formats_raw):
            return None
        return self.formats_raw[idx - 1]

    def _name_fonts(self) -> tuple[dict[int, str], dict[str, Any]]:
        """Имена шрифтов: idx0 → default, далее по свойствам."""
        names: dict[int, str] = {}
        defs: dict[str, Any] = {}
        used: set[str] = set()
        for i, f in enumerate(self.fonts_raw):
            if i == 0:
                name = "default"
            else:
                parts: list[str] = []
                if f["bold"]:
                    parts.append("bold")
                if f["italic"]:
                    parts.append("italic")
                if f["underline"]:
                    parts.append("underline")
                if f["strikeout"]:
                    parts.append("strikeout")
                if f["size"] >= 14:
                    parts.append("header")
                elif 0 < f["size"] < 9:
                    parts.append("small")
                base = "-".join(parts) if parts else f"font{i}"
                name, n = base, 2
                while name in used:
                    name = f"{base}-{n}"
                    n += 1
            used.add(name)
            names[i] = name
            defs[name] = {k: v for k, v in f.items() if v not in (False, "", None)}
            if i == 0 and not self.fonts_raw:
                break
        return names, defs

    def _border_dsl(self, fmt: dict[str, Any]) -> tuple[str | None, str | None]:
        """(border, borderWidth) по индексам линий формата."""
        sides = []
        widths = []
        for key, side in (("leftBorder", "left"), ("topBorder", "top"),
                          ("rightBorder", "right"), ("bottomBorder", "bottom")):
            idx = fmt.get(key)
            if idx is not None and idx >= 0 and idx < len(self.lines_raw):
                sides.append(side)
                widths.append(self.lines_raw[idx])
        if not sides:
            return None, None
        border = "all" if len(sides) == 4 else ",".join(sides)
        width = "thick" if max(widths) >= 2 else "thin"
        return border, width

    def _style_from_format(
        self, fmt: dict[str, Any] | None, font_names: dict[int, str],
    ) -> dict[str, Any]:
        style: dict[str, Any] = {}
        if not fmt:
            return style
        if fmt.get("font") is not None and fmt["font"] in font_names \
                and font_names[fmt["font"]] != "default":
            style["font"] = font_names[fmt["font"]]
        border, bw = self._border_dsl(fmt)
        if border:
            style["border"] = border
            if bw and bw != "thin":
                style["borderWidth"] = bw
        if fmt["ha"] in _ALIGN_TO_DSL and fmt["ha"] != "Left":
            style["align"] = _ALIGN_TO_DSL[fmt["ha"]]
        if fmt["va"] in _VALIGN_TO_DSL and fmt["va"] != "Bottom":
            style["valign"] = _VALIGN_TO_DSL[fmt["va"]]
        if fmt["wrap"]:
            style["wrap"] = True
        if fmt["dataFormat"]:
            style["format"] = fmt["dataFormat"]
        if fmt["backColor"]:
            style["backColor"] = fmt["backColor"]
        if fmt["textColor"]:
            style["textColor"] = fmt["textColor"]
        return style

    @staticmethod
    def _style_name(style: dict[str, Any], used: set[str]) -> str:
        if not style:
            return "default"
        parts: list[str] = []
        border = style.get("border")
        if border == "all":
            parts.append("bordered")
        elif border:
            parts.append("border-" + border.replace(",", "-"))
        if style.get("font"):
            parts.append(str(style["font"]))
        if style.get("align"):
            parts.append(str(style["align"]))
        if style.get("valign"):
            parts.append(str(style["valign"]))
        if style.get("wrap"):
            parts.append("wrap")
        if style.get("format"):
            parts.append("fmt")
        if style.get("backColor"):
            parts.append("bg")
        if style.get("textColor"):
            parts.append("fg")
        base = "-".join(parts) if parts else "default"
        name, n = base, 2
        while name in used:
            name = f"{base}-{n}"
            n += 1
        used.add(name)
        return name

    def _build_dsl(self, root: ET.Element) -> dict[str, Any]:
        font_names, font_defs = self._name_fonts()

        # Собираем стили по всем использованным форматам ячеек/строк.
        style_by_fmt: dict[int, str] = {}
        styles: dict[str, Any] = {"default": {}}
        used_names = {"default"}
        name_by_style: dict[tuple, str] = {(): "default"}
        fmt_indices: set[int] = set()
        for row in self.rows.values():
            if row.get("formatIndex"):
                fmt_indices.add(row["formatIndex"])
            for cell in row["cells"]:
                if cell["f"]:
                    fmt_indices.add(cell["f"])
        for idx in sorted(fmt_indices):
            fmt = self._format(idx)
            if fmt is None:
                self.warnings.append(
                    f"ссылка на формат #{idx} вне палитры — игнорируется"
                )
                continue
            style = self._style_from_format(fmt, font_names)
            key = tuple(sorted(style.items()))
            if key in name_by_style:
                name = name_by_style[key]
            else:
                name = self._style_name(style, used_names)
                name_by_style[key] = name
            style_by_fmt[idx] = name
            if name != "default" and name not in styles:
                styles[name] = style

        max_row = max(self.rows) if self.rows else -1
        covered = [False] * (max_row + 1)

        areas: list[dict[str, Any]] = []
        for item in sorted(self.named_items, key=lambda x: x["beginRow"]):
            rows: list[dict[str, Any]] = []
            for r in range(item["beginRow"],
                           min(item["endRow"], max_row) + 1):
                covered[r] = True
                rows.append(self._row_dsl(r, style_by_fmt))
            areas.append({"name": item["name"], "rows": rows})

        uncovered = [r for r in range(max_row + 1) if not covered[r]]
        if uncovered:
            self.warnings.append(
                f"строки вне именованных областей: {uncovered} — "
                "в DSL не вошли"
            )

        dsl: dict[str, Any] = {
            "columns": self._columns_count(root),
            "fonts": font_defs,
            "styles": styles,
            "areas": areas,
        }
        widths = self._column_widths(root)
        if widths:
            dsl["columnWidths"] = widths
        page = self._page_setup(root)
        if page:
            dsl["pageSetup"] = page
        if self.raw:
            dsl["raw"] = self.raw
        dsl["warnings"] = self.warnings
        return dsl

    def _row_dsl(self, r: int, style_by_fmt: dict[int, str]) -> dict[str, Any]:
        row = self.rows.get(r, {"formatIndex": 0, "cells": []})
        out: dict[str, Any] = {}
        row_fmt = self._format(row.get("formatIndex", 0))
        if row_fmt and row_fmt.get("height"):
            out["height"] = row_fmt["height"]

        cells: list[dict[str, Any]] = []
        empty_styles: list[str] = []
        next_col = 0
        for cell in row["cells"]:
            col = cell["col"] if cell["col"] is not None else next_col
            next_col = col + 1
            fmt = self._format(cell["f"])
            fill = (fmt or {}).get("fillType", "")
            content_kind = _FILL_TYPE_TO_DSL.get(fill)
            if content_kind is None and cell["text"]:
                content_kind = "text"
            style_name = style_by_fmt.get(cell["f"], "default")
            if content_kind is None:
                # Пустая ячейка — кандидат в rowStyle.
                if style_name != "default":
                    empty_styles.append(style_name)
                continue
            entry: dict[str, Any] = {"col": col + 1}  # DSL 1-based
            if style_name != "default":
                entry["style"] = style_name
            if content_kind == "param":
                entry["param"] = cell["param"] or cell["text"] or ""
                if cell["detail"]:
                    entry["detail"] = cell["detail"]
            elif content_kind == "template":
                entry["template"] = cell["text"] or ""
            else:
                entry["text"] = cell["text"] or ""
            cells.append(entry)

        if cells:
            out["cells"] = cells
        if empty_styles and len(set(empty_styles)) == 1:
            out["rowStyle"] = empty_styles[0]
        elif len(set(empty_styles)) > 1:
            self.warnings.append(
                f"строка {r}: пустые ячейки с разными стилями — "
                "rowStyle не выведен"
            )
        # Объединения, начинающиеся в этой строке → span/rowspan ячеек.
        for m in self.merges:
            if m["r"] != r:
                continue
            for entry in cells:
                if entry["col"] == m["c"] + 1:
                    if m["w"]:
                        entry["span"] = m["w"] + 1
                    if m["h"]:
                        entry["rowspan"] = m["h"] + 1
        return out

    def _columns_count(self, root: ET.Element) -> int:
        cols = _child(root, "columns")
        if cols is None:
            return 0
        return _child_int(cols, "size") or 0

    def _column_widths(self, root: ET.Element) -> dict[str, int]:
        cols = _child(root, "columns")
        widths: dict[str, int] = {}
        if cols is None:
            return widths
        for item in cols:
            if _local(item.tag) != "columnsItem":
                continue
            idx = _child_int(item, "index")
            col_node = _child(item, "column")
            fmt_idx = _child_int(col_node, "formatIndex") if col_node is not None else None
            fmt = self._format(fmt_idx or 0)
            if idx is not None and fmt and fmt.get("width"):
                widths[str(idx + 1)] = fmt["width"]  # DSL 1-based
        return widths

    def _page_setup(self, root: ET.Element) -> dict[str, Any]:
        ps = _child(root, "pageSetup")
        if ps is None:
            return {}
        out: dict[str, Any] = {}
        for ch in ps:
            out[_local(ch.tag)] = (ch.text or "").strip()
        return out

    def _result_empty(self, warning: str) -> dict[str, Any]:
        self.warnings.append(warning)
        return {
            "columns": 0, "fonts": {}, "styles": {"default": {}},
            "areas": [], "raw": self.raw, "warnings": self.warnings,
        }


def parse_mxl(text: str) -> dict[str, Any]:
    """Разобрать содержимое .mxl (XML) в JSON-DSL.

    Возвращает dict с ключами columns/fonts/styles/areas (+опционально
    columnWidths/pageSetup/raw) и списком ``warnings``.
    """
    return MxlParser().parse(text)
