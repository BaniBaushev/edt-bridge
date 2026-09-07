"""Bootstrap MCP-сервера edt-bridge (fastmcp).

Регистрирует инструменты модуля mutations (M1). Остальные модули
(forms, mxl, bsp, extensions, interface, runtime) импортируются опционально:
отсутствие модуля не роняет сервер — выводится предупреждение в stderr.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any, Optional

from fastmcp import FastMCP

mcp = FastMCP("edt-bridge")


def _run(coro: Any) -> Any:
    """Синхронно выполнить корутину инструмента (fastmcp вызывает sync-func)."""
    return asyncio.run(coro)


# --- mutations (M1) --------------------------------------------------------

from .mutations import apply_mutations, plan_mutations  # noqa: E402


@mcp.tool(name="edtb_plan_mutations")
def tool_plan_mutations(
    ops: list[dict[str, Any]], projectPath: Optional[str] = None
) -> dict[str, Any]:
    """Провалидировать пакет мутаций БЕЗ применения.

    Для каждой op: проверка схемы, адресуемости цели, оценка риска.
    Возвращает план с hash для edtb_apply_mutations и confirmToken
    для deleteMetadata (двухфазное удаление).
    """
    return plan_mutations(ops, projectPath)


@mcp.tool(name="edtb_apply_mutations")
def tool_apply_mutations(
    ops: list[dict[str, Any]],
    planHash: str,
    projectPath: Optional[str] = None,
    dryRun: bool = False,
) -> dict[str, Any]:
    """Применить пакет мутаций по hash свежего плана.

    Стратегии: edt-mcp (через proxy) и file (правка XML + resync).
    dryRun=true возвращает diff-preview без применения. При ошибке
    середины пакета файловые изменения откатываются из backup'а.
    """
    return _run(apply_mutations(ops, planHash, projectPath, dryRun))


# --- опциональные модули (M2+) ---------------------------------------------

# Каждая функция модуля регистрируется как MCP-инструмент под своим именем.
_OPTIONAL_TOOLS = {
    "forms.forms": ("edtb_form_compile", "edtb_form_info", "edtb_form_remove"),
    "mxl.mxl": ("edtb_mxl_decompile", "edtb_mxl_compile", "edtb_mxl_info"),
    "bsp.bsp": ("edtb_epf_scaffold", "edtb_help_add"),
    "extensions.extensions": ("edtb_cfe_borrow_method", "edtb_cfe_init_role"),
    "interface.interface": ("edtb_interface_edit", "edtb_interface_info"),
    "interface.panels": ("edtb_cf_panels", "edtb_cf_panels_info"),
    "runtime.runtime": ("edtb_designer_check", "edtb_cf_artifact"),
}


def _register_optional() -> None:
    """Подключить инструменты модулей M2+, если они уже реализованы."""
    import importlib

    for module_name, tool_names in _OPTIONAL_TOOLS.items():
        try:
            module = importlib.import_module(f".{module_name}", __package__)
        except ModuleNotFoundError:
            continue  # модуль ещё не реализован — не падаем
        except Exception as exc:  # pragma: no cover
            print(f"edt-bridge: модуль {module_name} не загружен: {exc}",
                  file=sys.stderr)
            continue
        for tool_name in tool_names:
            func = getattr(module, tool_name, None)
            if callable(func):
                mcp.tool(func, name=tool_name)


_register_optional()


def main() -> None:
    """Точка входа: запуск MCP-сервера по stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
