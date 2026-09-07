"""Resync после файловых правок: resync_to_disk + revalidate_objects через proxy.

SPEC 5.4: если EDT-MCP недоступен — warning «выполните resync вручную».
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .client import EdtMcpClient, EdtMcpError, EdtMcpUnavailable

MANUAL_RESYNC_WARNING = (
    "EDT-MCP недоступен — выполните resync вручную "
    "(Project → Update from File System в EDT) и запустите валидацию"
)


async def resync_after_file_changes(
    paths: list[Path | str],
    client: EdtMcpClient | None = None,
) -> dict[str, Any]:
    """Синхронизировать модель EDT с диском после файловых мутаций.

    Вызывает ``resync_to_disk`` и ``revalidate_objects`` (канонические
    инструменты EDT-MCP). При недоступности сервера возвращает
    ``{synced: False, warnings: [...]}`` вместо исключения.

    :param paths: изменённые файлы/каталоги (для отчёта и scope валидации).
    :param client: опциональный клиент (для тестов/переиспользования).
    :return: ``{synced, resynced, revalidated, warnings}``.
    """
    client = client or EdtMcpClient()
    result: dict[str, Any] = {
        "synced": False,
        "resynced": False,
        "revalidated": False,
        "warnings": [],
    }
    try:
        await client.call_tool("resync_to_disk", {"paths": [str(p) for p in paths]})
        result["resynced"] = True
        await client.call_tool(
            "revalidate_objects", {"paths": [str(p) for p in paths]}
        )
        result["revalidated"] = True
        result["synced"] = True
    except EdtMcpUnavailable as exc:
        result["warnings"].append(f"{MANUAL_RESYNC_WARNING} ({exc})")
    except EdtMcpError as exc:
        result["warnings"].append(
            f"EDT-MCP отклонил resync/валидацию: {exc}. {MANUAL_RESYNC_WARNING}"
        )
    return result
