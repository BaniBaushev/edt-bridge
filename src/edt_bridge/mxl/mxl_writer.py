"""Генератор файла макета табличного документа (.mxl, XML-формат EDT) из JSON-DSL.

Поддерживает полный DSL: палитры шрифтов/линий, стили с бордюрами, выравниванием,
переносом, форматом данных и цветами, fillType («Шаблон»/«Значение»/«Текст»),
параметры расшифровки (detail), объединения (span/rowspan), pageSetup,
ширины колонок (в т.ч. пропорции "Nx" и формат страницы page).
"""

from __future__ import annotations

from typing import Any
from xml.sax.saxutils import escape as _xml_escape

_ALIGN_FROM_DSL = {"left": "Left", "center": "Center", "right": "Right",
                   "justify": "Justify"}
_VALIGN_FROM_DSL = {"top": "Top", "center": "Center", "bottom": "Bottom"}
_PAGE_WIDTHS = {"A4-landscape": 780, "A4-portrait": 540}


def _esc_text(value: str) -> str:
    return _xml_escape(value).replace("\r", "&#13;")


def _esc_attr(value: str) -> str:
    return (_esc_text(value).replace("\t", "&#9;").replace("\n", "&#10;")
            .replace('"', "&quot;"))


class MxlWriteError(ValueError):
    """Ошибка структуры JSON-DSL макета."""


class _Palette:
    """Дедуплицирующая палитра форматов (1-based, 0 = формат по умолчанию)."""

    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []
        self._index: dict[tuple, int] = {}

    def register(self, entry: dict[str, Any]) -> int:
        key = tuple(sorted((k, str(v)) for k, v in entry.items()))
        if key in self._index:
            return self._index[key]
        self.entries.append(entry)
        self._index[key] = len(self.entries)
        return len(self.entries)


class MxlWriter:
    """Компилятор JSON-DSL → XML табличного документа."""

    def __init__(self, dsl: dict[str, Any]) -> None:
        if not isinstance(dsl, dict):
            raise MxlWriteError("DSL макета должен быть объектом (dict)")
        self.dsl = dsl
        self.warnings: list[str] = []
        self.fonts: list[dict[str, Any]] = []
        self.font_index: dict[str, int] = {}
        self.line_widths: list[int] = []
        self.formats = _Palette()
        self.merges: list[dict[str, int]] = []
        self.named_items: list[dict[str, Any]] = []
        self.columns = int(dsl.get("columns") or 0)
        if self.columns <= 0:
            raise MxlWriteError("DSL: поле 'columns' обязательно и должно быть > 0")

    # ------------------------------------------------------------------ API
    def write(self) -> str:
        dsl = self.dsl
        self._prepare_fonts(dsl.get("fonts") or {})
        styles = dsl.get("styles") or {}
        column_widths = self._resolve_column_widths(dsl)

        rows_out: list[str] = []
        global_row = 0
        areas = dsl.get("areas")
        if not areas:
            raise MxlWriteError("DSL: поле 'areas' обязательно и не пустое")
        for area in areas:
            if not isinstance(area, dict) or not area.get("name"):
                raise MxlWriteError("DSL: каждая область требует имя ('name')")
            area_start = global_row
            active_rowspans: list[dict[str, int]] = []
            local_row = 0
            for row in area.get("rows") or []:
                row = row or {}
                if row.get("empty"):
                    for _ in range(int(row["empty"])):
                        rows_out.append(self._emit_empty_row(global_row))
                        global_row += 1
                        local_row += 1
                    continue
                occupied = set()
                for rs in active_rowspans:
                    if rs["start"] < local_row <= rs["end"]:
                        occupied.update(range(rs["c1"], rs["c2"] + 1))
                xml = self._emit_row(
                    row, global_row, local_row, styles, occupied,
                    active_rowspans,
                )
                rows_out.append(xml)
                global_row += 1
                local_row += 1
            if global_row == area_start:
                # Пустая область — одна пустая строка, чтобы область существовала.
                rows_out.append(self._emit_empty_row(global_row))
                global_row += 1
            self.named_items.append({
                "name": area["name"],
                "beginRow": area_start,
                "endRow": global_row - 1,
            })

        if global_row == 0:
            raise MxlWriteError(
                "DSL не содержит ни одной строки: нулевой макет не может "
                "сохранить колонки и области"
            )

        lines: list[str] = [_XML_HEADER, _LANG_SETTINGS]
        lines.append(self._emit_columns(column_widths))
        lines.extend(self._compress_rows(rows_out))
        lines.append("\t<templateMode>true</templateMode>")
        lines.append("\t<defaultFormatIndex>0</defaultFormatIndex>")
        lines.append(f"\t<height>{global_row}</height>")
        lines.append(f"\t<vgRows>{global_row}</vgRows>")
        for m in self.merges:
            lines.append("\t<merge>")
            lines.append(f"\t\t<r>{m['r']}</r>")
            lines.append(f"\t\t<c>{m['c']}</c>")
            if m.get("h"):
                lines.append(f"\t\t<h>{m['h']}</h>")
            lines.append(f"\t\t<w>{m['w']}</w>")
            lines.append("\t</merge>")
        for item in self.named_items:
            lines.append('\t<namedItem xsi:type="NamedItemCells">')
            lines.append(f"\t\t<name>{_esc_text(str(item['name']))}</name>")
            lines.append("\t\t<area>")
            lines.append("\t\t\t<type>Rows</type>")
            lines.append(f"\t\t\t<beginRow>{item['beginRow']}</beginRow>")
            lines.append(f"\t\t\t<endRow>{item['endRow']}</endRow>")
            lines.append("\t\t\t<beginColumn>-1</beginColumn>")
            lines.append("\t\t\t<endColumn>-1</endColumn>")
            lines.append("\t\t</area>")
            lines.append("\t</namedItem>")
        for width in self.line_widths:
            lines.append(f'\t<line width="{width}" gap="false">')
            lines.append(
                '\t\t<v8ui:style xsi:type='
                '"v8ui:SpreadsheetDocumentCellLineType">Solid</v8ui:style>'
            )
            lines.append("\t</line>")
        for f in self.fonts:
            lines.append(
                f'\t<font faceName="{_esc_attr(str(f.get("face", "Arial")))}" '
                f'height="{int(f.get("size", 10))}" '
                f'bold="{_b(f.get("bold"))}" italic="{_b(f.get("italic"))}" '
                f'underline="{_b(f.get("underline"))}" '
                f'strikeout="{_b(f.get("strikeout"))}" '
                f'kind="Absolute" scale="100"/>'
            )
        for entry in self.formats.entries:
            lines.append(self._emit_format(entry))
        page_setup = dsl.get("pageSetup")
        if isinstance(page_setup, dict) and page_setup:
            lines.append("\t<pageSetup>")
            for key, value in page_setup.items():
                lines.append(
                    f"\t\t<{key}>{_esc_text(str(value))}</{key}>"
                )
            lines.append("\t</pageSetup>")
        lines.append("</document>")
        return "\n".join(lines) + "\n"

    # ------------------------------------------------------- подготовка
    def _prepare_fonts(self, fonts: dict[str, Any]) -> None:
        if not fonts:
            fonts = {"default": {"face": "Arial", "size": 10}}
            self.warnings.append(
                "шрифты не заданы — создан шрифт 'default' (Arial 10)"
            )
        if "default" not in fonts:
            self.warnings.append(
                "шрифт 'default' не определён — добавлен в начало палитры"
            )
            self.fonts.append({"face": "Arial", "size": 10})
            self.font_index["default"] = 0
        for name, f in fonts.items():
            self.font_index[name] = len(self.fonts)
            self.fonts.append(dict(f or {}))

    def _line_index(self, width: int) -> int:
        if width in self.line_widths:
            return self.line_widths.index(width)
        self.line_widths.append(width)
        return len(self.line_widths) - 1

    def _resolve_column_widths(self, dsl: dict[str, Any]) -> dict[int, int]:
        """Ширины колонок (1-based → абсолют), включая пропорции "Nx" и page."""
        spec = dsl.get("columnWidths") or {}
        default_width = dsl.get("defaultWidth") or 10
        page = dsl.get("page")
        multipliers: dict[int, float] = {}
        result: dict[int, int] = {}
        for key, value in spec.items():
            cols = self._parse_col_key(str(key))
            if isinstance(value, str) and value.endswith("x"):
                try:
                    mult = float(value[:-1])
                except ValueError:
                    raise MxlWriteError(
                        f"columnWidths['{key}']: некорректный множитель '{value}'"
                    )
                for c in cols:
                    multipliers[c] = mult
            else:
                for c in cols:
                    result[c] = int(value)
        if multipliers:
            if page is not None:
                page_width = (_PAGE_WIDTHS.get(str(page))
                              if not isinstance(page, (int, float)) else int(page))
                if page_width is None:
                    raise MxlWriteError(
                        f"page: неизвестный формат страницы '{page}'"
                    )
                total = sum(multipliers.get(c, 1.0)
                            for c in range(1, self.columns + 1))
                if total <= 0:
                    raise MxlWriteError("page: сумма пропорций 'Nx' равна нулю")
                unit = page_width / total
                for c, mult in multipliers.items():
                    result[c] = round(unit * mult)
            else:
                for c, mult in multipliers.items():
                    result[c] = round(int(default_width) * mult)
        return result

    def _parse_col_key(self, key: str) -> list[int]:
        cols: list[int] = []
        for part in key.split(","):
            part = part.strip()
            if "-" in part:
                a, b = part.split("-", 1)
                cols.extend(range(int(a), int(b) + 1))
            elif part:
                cols.append(int(part))
        for c in cols:
            if not 1 <= c <= self.columns:
                raise MxlWriteError(
                    f"columnWidths: колонка {c} вне диапазона 1..{self.columns}"
                )
        return cols

    # ------------------------------------------------------- форматы/стили
    def _format_entry(self, style_name: str, styles: dict[str, Any],
                      fill_type: str) -> dict[str, Any]:
        style = styles.get(style_name)
        if style is None:
            if style_name not in ("default", ""):
                self.warnings.append(
                    f"стиль '{style_name}' не определён — применён 'default'"
                )
            style = {}
        entry: dict[str, Any] = {}
        font_name = style.get("font", "default")
        fidx = self.font_index.get(font_name)
        if fidx is None:
            self.warnings.append(
                f"шрифт '{font_name}' не определён — использован 'default'"
            )
            fidx = self.font_index.get("default", 0)
        if fidx:
            entry["font"] = fidx
        border_width = 2 if style.get("borderWidth") == "thick" else 1
        border = style.get("border")
        if border and border != "none":
            sides = (["left", "top", "right", "bottom"] if border == "all"
                     else [s.strip() for s in str(border).split(",")])
            line_idx = self._line_index(border_width)
            for side in sides:
                entry[f"{side}Border"] = line_idx
        if style.get("align"):
            entry["ha"] = _ALIGN_FROM_DSL.get(str(style["align"]), "")
        if style.get("valign"):
            entry["va"] = _VALIGN_FROM_DSL.get(str(style["valign"]), "")
        if style.get("wrap"):
            entry["wrap"] = True
        if style.get("format"):
            entry["dataFormat"] = str(style["format"])
        if style.get("backColor"):
            entry["backColor"] = str(style["backColor"])
        if style.get("textColor"):
            entry["textColor"] = str(style["textColor"])
        if fill_type:
            entry["fillType"] = fill_type
        return entry

    # ------------------------------------------------------- строки/ячейки
    def _emit_empty_row(self, index: int) -> str:
        return (f"\t<rowsItem>\n\t\t<index>{index}</index>\n\t\t<row>\n"
                "\t\t\t<empty>true</empty>\n\t\t</row>\n\t</rowsItem>")

    def _emit_row(
        self, row: dict[str, Any], global_row: int, local_row: int,
        styles: dict[str, Any], occupied: set[int],
        active_rowspans: list[dict[str, int]],
    ) -> str:
        row_style = row.get("rowStyle")
        cells = row.get("cells") or []
        emitted: list[tuple[int, str]] = []  # (col0, xml)

        row_fmt_idx = 0
        if row.get("height"):
            row_fmt_idx = self.formats.register({"height": int(row["height"])})

        for cell in cells:
            col = int(cell.get("col") or 0)
            if not 1 <= col <= self.columns:
                raise MxlWriteError(
                    f"ячейка: col={col} вне диапазона 1..{self.columns}"
                )
            span = int(cell.get("span") or 1)
            rowspan = int(cell.get("rowspan") or 1)
            style_name = cell.get("style") or row_style or "default"
            fill_type, param, detail, text = _cell_content(cell)
            fmt_idx = self.formats.register(
                self._format_entry(style_name, styles, fill_type)
            )
            emitted.append((col - 1, self._emit_cell(
                col - 1, fmt_idx, param, detail, text)))
            occupied.update(range(col, col + span))
            if rowspan > 1:
                active_rowspans.append({
                    "c1": col, "c2": col + span - 1,
                    "start": local_row, "end": local_row + rowspan - 1,
                })
            if span > 1 or rowspan > 1:
                merge = {"r": global_row, "c": col - 1, "w": span - 1}
                if rowspan > 1:
                    merge["h"] = rowspan - 1
                self.merges.append(merge)

        if row_style:
            gap_idx = self.formats.register(
                self._format_entry(row_style, styles, "")
            )
            for col in range(1, self.columns + 1):
                if col not in occupied:
                    emitted.append((col - 1, self._emit_cell(
                        col - 1, gap_idx, None, None, None)))

        emitted.sort(key=lambda x: x[0])
        parts = ["\t<rowsItem>", f"\t\t<index>{global_row}</index>", "\t\t<row>"]
        if row_fmt_idx:
            parts.append(f"\t\t\t<formatIndex>{row_fmt_idx}</formatIndex>")
        if emitted:
            prev = -1
            for col0, xml in emitted:
                parts.append(self._wrap_cell(xml, col0, prev))
                prev = col0
        else:
            parts.append("\t\t\t<empty>true</empty>")
        parts.append("\t\t</row>")
        parts.append("\t</rowsItem>")
        return "\n".join(parts)

    @staticmethod
    def _wrap_cell(inner: str, col0: int, prev: int) -> str:
        lines = ["\t\t\t<c>"]
        if col0 != prev + 1 and prev != -1 or (prev == -1 and col0 != 0):
            lines.append(f"\t\t\t\t<i>{col0}</i>")
        lines.append(inner)
        lines.append("\t\t\t</c>")
        return "\n".join(lines)

    @staticmethod
    def _emit_cell(col0: int, fmt_idx: int, param: str | None,
                   detail: str | None, text: str | None) -> str:
        lines = ["\t\t\t\t<c>", f"\t\t\t\t\t<f>{fmt_idx}</f>"]
        if param is not None:
            lines.append(f"\t\t\t\t\t<parameter>{_esc_text(param)}</parameter>")
            if detail:
                lines.append(
                    f"\t\t\t\t\t<detailParameter>{_esc_text(detail)}"
                    "</detailParameter>"
                )
        if text is not None:
            lines.append("\t\t\t\t\t<tl>")
            lines.append("\t\t\t\t\t\t<v8:item>")
            lines.append("\t\t\t\t\t\t\t<v8:lang>ru</v8:lang>")
            lines.append(
                f"\t\t\t\t\t\t\t<v8:content>{_esc_text(text)}</v8:content>"
            )
            lines.append("\t\t\t\t\t\t</v8:item>")
            lines.append("\t\t\t\t\t</tl>")
        lines.append("\t\t\t\t</c>")
        return "\n".join(lines)

    # ------------------------------------------------------- палитры → XML
    def _emit_format(self, entry: dict[str, Any]) -> str:
        lines = ["\t<format>"]
        if entry.get("font"):
            lines.append(f"\t\t<font>{entry['font']}</font>")
        for side in ("left", "top", "right", "bottom"):
            key = f"{side}Border"
            if key in entry:
                tag = f"{side}Border"
                lines.append(f"\t\t<{tag}>{entry[key]}</{tag}>")
        if entry.get("width"):
            lines.append(f"\t\t<width>{entry['width']}</width>")
        if entry.get("height"):
            lines.append(f"\t\t<height>{entry['height']}</height>")
        if entry.get("ha"):
            lines.append(
                f"\t\t<horizontalAlignment>{entry['ha']}</horizontalAlignment>"
            )
        if entry.get("va"):
            lines.append(
                f"\t\t<verticalAlignment>{entry['va']}</verticalAlignment>"
            )
        if entry.get("wrap"):
            lines.append("\t\t<textPlacement>Wrap</textPlacement>")
        if entry.get("fillType"):
            lines.append(f"\t\t<fillType>{entry['fillType']}</fillType>")
        if entry.get("dataFormat"):
            lines.append("\t\t<format>")
            lines.append("\t\t\t<v8:item>")
            lines.append("\t\t\t\t<v8:lang>ru</v8:lang>")
            lines.append(
                f"\t\t\t\t<v8:content>{_esc_text(entry['dataFormat'])}"
                "</v8:content>"
            )
            lines.append("\t\t\t</v8:item>")
            lines.append("\t\t</format>")
        if entry.get("backColor"):
            lines.append(
                f"\t\t<backColor>{_esc_text(entry['backColor'])}</backColor>"
            )
        if entry.get("textColor"):
            lines.append(
                f"\t\t<textColor>{_esc_text(entry['textColor'])}</textColor>"
            )
        lines.append("\t</format>")
        return "\n".join(lines)

    def _emit_columns(self, widths: dict[int, int]) -> str:
        lines = ["\t<columns>", f"\t\t<size>{self.columns}</size>"]
        for col in sorted(widths):
            fmt_idx = self.formats.register({"width": widths[col]})
            lines.append("\t\t<columnsItem>")
            lines.append(f"\t\t\t<index>{col - 1}</index>")
            lines.append("\t\t\t<column>")
            lines.append(f"\t\t\t\t<formatIndex>{fmt_idx}</formatIndex>")
            lines.append("\t\t\t</column>")
            lines.append("\t\t</columnsItem>")
        lines.append("\t</columns>")
        return "\n".join(lines)

    @staticmethod
    def _compress_rows(rows_xml: list[str]) -> list[str]:
        """Слить подряд идущие одинаковые пустые строки через indexTo."""
        out: list[str] = []
        i = 0
        while i < len(rows_xml):
            xml = rows_xml[i]
            if "<empty>true</empty>" in xml:
                j = i
                while (j + 1 < len(rows_xml)
                       and "<empty>true</empty>" in rows_xml[j + 1]):
                    j += 1
                if j > i:
                    xml = xml.replace(
                        f"<index>{i}</index>",
                        f"<index>{i}</index>\n\t\t<indexTo>{j}</indexTo>",
                        1,
                    )
                out.append(xml)
                i = j + 1
            else:
                out.append(xml)
                i += 1
        return out


def _b(value: Any) -> str:
    return "true" if value else "false"


def _cell_content(cell: dict[str, Any]) -> tuple[str, str | None, str | None,
                                                 str | None]:
    """(fillType, param, detail, text) по содержимому ячейки DSL."""
    if cell.get("param") is not None:
        return "Parameter", str(cell["param"]), (
            str(cell["detail"]) if cell.get("detail") else None), None
    if cell.get("template") is not None:
        return "Template", None, None, str(cell["template"])
    if cell.get("text") is not None:
        return "Text", None, None, str(cell["text"])
    return "", None, None, None


_XML_HEADER = '<?xml version="1.0" encoding="UTF-8"?>\n' \
    '<document xmlns="http://v8.1c.ru/8.2/data/spreadsheet" ' \
    'xmlns:style="http://v8.1c.ru/8.1/data/ui/style" ' \
    'xmlns:v8="http://v8.1c.ru/8.1/data/core" ' \
    'xmlns:v8ui="http://v8.1c.ru/8.1/data/ui" ' \
    'xmlns:xs="http://www.w3.org/2001/XMLSchema" ' \
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'

_LANG_SETTINGS = "\t<languageSettings>\n\t\t<currentLanguage>ru" \
    "</currentLanguage>\n\t\t<defaultLanguage>ru</defaultLanguage>\n" \
    "\t\t<languageInfo>\n\t\t\t<id>ru</id>\n\t\t\t<code>Русский</code>\n" \
    "\t\t\t<description>Русский</description>\n\t\t</languageInfo>\n" \
    "\t</languageSettings>"


def write_mxl(dsl: dict[str, Any]) -> tuple[str, list[str]]:
    """Сгенерировать содержимое .mxl (XML) из JSON-DSL.

    Возвращает (xml_text, warnings).
    """
    writer = MxlWriter(dsl)
    return writer.write(), writer.warnings
