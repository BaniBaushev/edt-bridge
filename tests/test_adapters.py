# -*- coding: utf-8 -*-
"""Интеграционные тесты адаптеров к РЕАЛЬНЫМ модулям ядра (anti-masking).

История: conftest-стабы маскировали рассинхрон API (ревью R1). Здесь
проверяем, что адаптеры модулей работают против настоящих
edt_bridge.config/safety/proxy.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest


class _FakeEdtMcpClient:
    """Подмена EdtMcpClient.call_tool без сети."""

    calls: list = []

    def __init__(self, *a, **kw):
        pass

    async def call_tool(self, name, args=None):
        type(self).calls.append((name, args))
        return {"ok": True, "tool": name}


def test_bsp_proxy_call_uses_real_client(monkeypatch):
    """bsp._core.proxy_call обязан работать через EdtMcpClient, а не
    искать несуществующую модульную call_tool (review high #1)."""
    from edt_bridge.bsp import _core
    from edt_bridge.proxy import client as real_client

    _FakeEdtMcpClient.calls = []
    monkeypatch.setattr(real_client, "EdtMcpClient", _FakeEdtMcpClient)
    ok, resp = _core.proxy_call("get_server_status", {})
    assert ok is True
    assert _FakeEdtMcpClient.calls == [("get_server_status", {})]
    assert resp["ok"] is True


def test_mxl_call_edt_mcp_uses_real_client(monkeypatch):
    """mxl._call_edt_mcp — то же через реальный proxy (review high #2)."""
    from edt_bridge.mxl import mxl
    from edt_bridge.proxy import client as real_client

    _FakeEdtMcpClient.calls = []
    monkeypatch.setattr(real_client, "EdtMcpClient", _FakeEdtMcpClient)
    warnings: list[str] = []
    res = asyncio.run(mxl._call_edt_mcp("modify_metadata", {"fqn": "X"}, warnings))
    assert res is not None and res["ok"] is True
    assert _FakeEdtMcpClient.calls == [("modify_metadata", {"fqn": "X"})]
    assert warnings == []


def test_patchfile_containment(tmp_path):
    """patchFile не должен выходить за пределы проекта (review high #5)."""
    from edt_bridge.mutations.core import resolve_target_path

    root = tmp_path / "proj"
    root.mkdir()
    assert resolve_target_path(root, "src/a.xml") == (root / "src/a.xml").resolve()
    with pytest.raises(ValueError):
        resolve_target_path(root, "../../etc/passwd")
    with pytest.raises(ValueError):
        resolve_target_path(root, "/etc/passwd")


def test_plan_rejects_external_path(tmp_path, monkeypatch):
    """plan_mutations помечает план ok=False для внешнего пути."""
    from edt_bridge.mutations.core import plan_mutations

    proj = tmp_path / "proj"
    (proj / "DT-INF").mkdir(parents=True)
    (proj / ".project").write_text("<x/>", encoding="utf-8")
    monkeypatch.setenv("EDTB_PROJECT_PATH", str(proj))
    res = plan_mutations(
        [{"op": "patchFile", "path": "../outside.xml",
          "changes": [{"xpath": "/a", "text": "b"}]}],
        str(proj),
    )
    assert res["ok"] is False
    assert any("пределы проекта" in w for w in res["ops"][0]["warnings"])


def test_plan_hash_binds_project_path():
    """planHash различается для разных projectPath (review medium #7)."""
    from edt_bridge.mutations.core import plan_hash

    ops = [{"op": "patchFile", "path": "a.xml",
            "changes": [{"xpath": "/a", "text": "b"}]}]
    assert plan_hash(ops, "/p1") != plan_hash(ops, "/p2")
