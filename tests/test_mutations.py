"""Тесты пакетов мутаций (без живого EDT: фейк-клиент proxy)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from edt_bridge.mutations import apply_mutations, plan_mutations
from edt_bridge.mutations.core import delete_confirm_token, plan_hash
from edt_bridge.proxy.client import EdtMcpUnavailable

MDO = "src/Configuration/Configuration.mdo"


class FakeClient:
    """Фейк EDT-MCP: записывает вызовы, недоступность по флагу."""

    def __init__(self, unavailable: bool = False) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.unavailable = unavailable

    async def call_tool(self, name: str, args: dict | None = None) -> dict:
        self.calls.append((name, args or {}))
        if self.unavailable:
            raise EdtMcpUnavailable("сервер выключен (фейк)")
        return {"ok": True, "tool": name}


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def allow_mutations(monkeypatch):
    """Разрешить файловые мутации по умолчанию в тестах apply."""
    monkeypatch.setenv("EDTB_ALLOW_FILE_MUTATIONS", "1")


# --- plan ------------------------------------------------------------------


def test_plan_ok(edt_project):
    ops = [
        {"op": "patchFile", "path": MDO, "changes": [
            {"xpath": "/*[local-name()='Configuration']/name", "text": "NewName"}]},
        {"op": "modifyMetadata", "name": "Catalog.Товары", "properties": {}},
    ]
    plan = plan_mutations(ops, str(edt_project))
    assert plan["ok"] is True
    assert plan["hash"] == plan_hash(ops, str(edt_project))
    assert plan["ops"][0]["strategy"] == "file"
    assert plan["ops"][1]["strategy"] == "edt-mcp"


def test_plan_unknown_op(edt_project):
    plan = plan_mutations([{"op": "boom"}], str(edt_project))
    assert plan["ok"] is False
    assert "неизвестная операция" in plan["ops"][0]["warnings"][0]


def test_plan_missing_fields(edt_project):
    plan = plan_mutations([{"op": "modifyMetadata", "name": "X"}], str(edt_project))
    assert plan["ok"] is False
    assert "properties" in plan["ops"][0]["warnings"][0]


def test_plan_patchfile_missing_target(edt_project):
    ops = [{"op": "patchFile", "path": "нет/такого.xml", "changes": [
        {"xpath": "/a", "text": "1"}]}]
    plan = plan_mutations(ops, str(edt_project))
    assert plan["ok"] is False
    assert any("не существует" in w for w in plan["ops"][0]["warnings"])


def test_plan_delete_two_phase(edt_project):
    ops = [{"op": "deleteMetadata", "name": "Catalog.Старый"}]
    plan = plan_mutations(ops, str(edt_project))
    assert plan["ok"] is True
    token = plan["ops"][0]["confirmToken"]
    assert token == delete_confirm_token("Catalog.Старый", plan["hash"])


# --- apply -----------------------------------------------------------------


def test_apply_hash_mismatch(edt_project):
    ops = [{"op": "modifyMetadata", "name": "X", "properties": {}}]
    res = run(apply_mutations(ops, "неверный-hash", str(edt_project)))
    assert res["ok"] is False
    assert any("planHash" in w for w in res["warnings"])


def test_apply_delete_requires_confirm(edt_project):
    ops = [{"op": "deleteMetadata", "name": "Catalog.Старый"}]
    plan = plan_mutations(ops, str(edt_project))
    res = run(apply_mutations(ops, plan["hash"], str(edt_project), client=FakeClient()))
    assert res["ok"] is False
    assert any("confirm token" in w for w in res["warnings"])


def test_apply_delete_with_confirm(edt_project):
    ops = [{"op": "deleteMetadata", "name": "Catalog.Старый"}]
    plan = plan_mutations(ops, str(edt_project))
    ops[0]["confirm"] = plan["ops"][0]["confirmToken"]
    client = FakeClient()
    res = run(apply_mutations(ops, plan["hash"], str(edt_project), client=client))
    assert res["ok"] is True
    assert ("delete_metadata", {"name": "Catalog.Старый"}) in client.calls


def test_apply_dry_run_preview(edt_project):
    ops = [{"op": "patchFile", "path": MDO, "changes": [
        {"xpath": "/*[local-name()='Configuration']/name", "text": "NewName"}]}]
    plan = plan_mutations(ops, str(edt_project))
    before = (edt_project / MDO).read_text(encoding="utf-8")
    res = run(apply_mutations(ops, plan["hash"], str(edt_project), dry_run=True,
                              client=FakeClient()))
    assert res["ok"] is True and res["dryRun"] is True
    assert "NewName" in res["previews"][MDO]
    assert (edt_project / MDO).read_text(encoding="utf-8") == before  # не изменился


def test_apply_file_mutations_forbidden(edt_project, monkeypatch):
    """Без EDTB_ALLOW_FILE_MUTATIONS=1 файловые ops отклоняются."""
    monkeypatch.setenv("EDTB_ALLOW_FILE_MUTATIONS", "0")
    ops = [{"op": "patchFile", "path": MDO, "changes": [
        {"xpath": "/*[local-name()='Configuration']/name", "text": "X"}]}]
    plan = plan_mutations(ops, str(edt_project))
    res = run(apply_mutations(ops, plan["hash"], str(edt_project), client=FakeClient()))
    assert res["ok"] is False
    assert any("EDTB_ALLOW_FILE_MUTATIONS" in w for w in res["warnings"])


def test_apply_patchfile_and_resync(edt_project):
    """patchFile применяется, resync/revalidate вызываются через proxy."""
    ops = [{"op": "patchFile", "path": MDO, "changes": [
        {"xpath": "/*[local-name()='Configuration']/name", "text": "NewName"},
        {"xpath": "/*[local-name()='Configuration']/comment",
         "attribute": "uuid", "value": "abc"},
    ]}]
    plan = plan_mutations(ops, str(edt_project))
    client = FakeClient()
    res = run(apply_mutations(ops, plan["hash"], str(edt_project), client=client))
    assert res["ok"] is True
    text = (edt_project / MDO).read_text(encoding="utf-8")
    assert "<name>NewName</name>" in text
    assert 'uuid="abc"' in text
    assert res["resync"]["synced"] is True
    tools = [name for name, _ in client.calls]
    assert "resync_to_disk" in tools and "revalidate_objects" in tools
    assert Path(res["backupDir"]).is_dir()


def test_apply_proxy_unavailable_degrades(edt_project):
    """EDT-MCP недоступен: edt-mcp ops пропущены, resync — warning."""
    ops = [
        {"op": "patchFile", "path": MDO, "changes": [
            {"xpath": "/*[local-name()='Configuration']/name", "text": "N"}]},
        {"op": "writeModule", "objectName": "Configuration",
         "moduleType": "ManagedApplicationModule", "source": "// код"},
    ]
    plan = plan_mutations(ops, str(edt_project))
    res = run(apply_mutations(ops, plan["hash"], str(edt_project),
                              client=FakeClient(unavailable=True)))
    assert res["ok"] is True
    assert res["ops"][0]["status"] == "ok"
    assert res["ops"][1]["status"] == "skipped"
    assert any("resync вручную" in w for w in res["warnings"])
    assert "<name>N</name>" in (edt_project / MDO).read_text(encoding="utf-8")


def test_apply_rollback_on_mid_package_error(edt_project):
    """Ошибка во второй op: изменения первой откачены из backup'а."""
    ops = [
        {"op": "patchFile", "path": MDO, "changes": [
            {"xpath": "/*[local-name()='Configuration']/name", "text": "Changed"}]},
        {"op": "patchFile", "path": MDO, "changes": [
            {"xpath": "/несуществующий", "text": "boom"}]},
    ]
    plan = plan_mutations(ops, str(edt_project))
    original = (edt_project / MDO).read_text(encoding="utf-8")
    res = run(apply_mutations(ops, plan["hash"], str(edt_project),
                              client=FakeClient(unavailable=True)))
    assert res["ok"] is False
    assert res["ops"][0]["status"] == "ok"
    assert res["ops"][1]["status"] == "error"
    assert (edt_project / MDO).read_text(encoding="utf-8") == original
    assert any("откачены" in w or "rollback" in w for w in res["warnings"])
