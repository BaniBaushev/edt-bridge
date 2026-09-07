"""Async MCP-клиент к EDT-MCP по Streamable HTTP.

Протокол: POST JSON-RPC на ``EDTB_EDT_MCP_URL`` с заголовком
``Accept: application/json, text/event-stream``. Ответ может быть как
обычным JSON, так и SSE-потоком (``data: {...}\\n\\n``) — поддерживаются оба.

Graceful degrade: если сервер недоступен, выбрасывается
:class:`EdtMcpUnavailable`; вызывающий код обязан превратить её в warning.
"""

from __future__ import annotations

import itertools
import json
from typing import Any

import httpx

from ..config import edt_mcp_url

#: Заголовки Streamable HTTP по MCP-спецификации.
MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


class EdtMcpUnavailable(Exception):
    """EDT-MCP сервер недоступен (сеть, таймаут, не-JSON-RPC ответ)."""


class EdtMcpError(Exception):
    """JSON-RPC ошибка от EDT-MCP (сервер доступен, но вызов отклонён)."""


def parse_sse_json(text: str) -> dict[str, Any]:
    """Извлечь последний JSON-объект из SSE-потока (``data: ...`` строки)."""
    last: dict[str, Any] | None = None
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if not payload or payload == "[DONE]":
            continue
        last = json.loads(payload)
    if last is None:
        raise EdtMcpUnavailable("SSE-поток не содержит JSON-RPC ответа")
    return last


class EdtMcpClient:
    """Минимальный async-клиент JSON-RPC к EDT-MCP."""

    def __init__(self, base_url: str | None = None, timeout: float = 30.0) -> None:
        self.base_url = (base_url or edt_mcp_url()).rstrip("/")
        self.timeout = timeout
        self._ids = itertools.count(1)
        self.session_id: str | None = None

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Выполнить POST JSON-RPC и разобрать ответ (JSON или SSE)."""
        headers = dict(MCP_HEADERS)
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(self.base_url, json=payload, headers=headers)
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            raise EdtMcpUnavailable(
                f"EDT-MCP недоступен ({self.base_url}): {exc}"
            ) from exc
        if resp.status_code >= 400:
            raise EdtMcpUnavailable(
                f"EDT-MCP вернул HTTP {resp.status_code}: {resp.text[:200]}"
            )
        if "Mcp-Session-Id" in resp.headers:
            self.session_id = resp.headers["Mcp-Session-Id"]
        content_type = resp.headers.get("Content-Type", "")
        try:
            if "text/event-stream" in content_type:
                return parse_sse_json(resp.text)
            return resp.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise EdtMcpUnavailable(
                f"Некорректный ответ EDT-MCP: {exc}"
            ) from exc

    async def _rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Вызвать JSON-RPC метод и вернуть result (или выбросить ошибку)."""
        request = {
            "jsonrpc": "2.0",
            "id": next(self._ids),
            "method": method,
            "params": params,
        }
        data = await self._post(request)
        if "error" in data:
            err = data["error"]
            raise EdtMcpError(
                f"JSON-RPC ошибка {err.get('code')}: {err.get('message')}"
            )
        return data.get("result", {})

    async def call_tool(self, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        """Вызвать инструмент EDT-MCP.

        Вызываются ТОЛЬКО канонические имена из
        ``reference/edt-mcp-tools.md`` (create_metadata, modify_metadata,
        write_module_source, resync_to_disk, revalidate_objects и др.).

        :return: содержимое ``result`` JSON-RPC ответа.
        :raises EdtMcpUnavailable: сервер недоступен — degrade в warning.
        :raises EdtMcpError: сервер отклонил вызов.
        """
        return await self._rpc("tools/call", {"name": name, "arguments": args or {}})

    async def ping(self) -> bool:
        """Проверить доступность сервера (get_server_status)."""
        try:
            await self.call_tool("get_server_status")
        except EdtMcpUnavailable:
            return False
        except EdtMcpError:
            return True  # сервер жив, хоть и отклонил вызов
        return True
