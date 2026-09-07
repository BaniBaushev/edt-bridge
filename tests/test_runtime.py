"""Тесты runtime-обёрток: ветка «CLI не найден» и валидация аргументов (без CLI)."""
import pytest

from edt_bridge.runtime import edtb_cf_artifact, edtb_designer_check

@pytest.fixture(autouse=True)
def _no_cli(monkeypatch, tmp_path):
    """Гарантированное отсутствие ring/1cedtcli/1cv8: пустой PATH, чистые env."""
    monkeypatch.delenv("EDTB_RING_CMD", raising=False)
    monkeypatch.delenv("EDTB_EDTCLI_CMD", raising=False)
    monkeypatch.delenv("EDTB_DESIGNER_CMD", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))


@pytest.mark.asyncio
async def test_designer_check_cli_not_found(project):
    res = await edtb_designer_check("project")
    assert not res["ok"]
    assert res["error"]["code"] == "CLI_NOT_FOUND"
    assert "EDTB_RING_CMD" in res["error"]["message"]
    assert "EDTB_EDTCLI_CMD" in res["error"]["message"]
    assert res["errors"] == [] and res["warnings"] == []


@pytest.mark.asyncio
async def test_designer_check_invalid_scope(project):
    res = await edtb_designer_check("module")
    assert not res["ok"]
    assert "scope" in res["error"]


@pytest.mark.asyncio
async def test_designer_check_no_project(monkeypatch):
    monkeypatch.delenv("EDTB_PROJECT_PATH", raising=False)
    res = await edtb_designer_check("project")
    assert not res["ok"]
    assert "EDTB_PROJECT_PATH" in res["error"]


@pytest.mark.asyncio
async def test_cf_artifact_invalid_action():
    res = await edtb_cf_artifact("build", "/tmp/x.cf")
    assert not res["ok"]
    assert "dump" in res["error"]


@pytest.mark.asyncio
async def test_cf_artifact_load_missing_file():
    res = await edtb_cf_artifact("load", "/tmp/несуществующий.cf")
    assert not res["ok"]
    assert "не найден" in res["error"]


@pytest.mark.asyncio
async def test_cf_artifact_no_infobase(tmp_path, monkeypatch):
    monkeypatch.delenv("EDTB_INFOBASE_PATH", raising=False)
    res = await edtb_cf_artifact("dump", str(tmp_path / "out.cf"))
    assert not res["ok"]
    assert "EDTB_INFOBASE_PATH" in res["error"]


@pytest.mark.asyncio
async def test_cf_artifact_designer_not_found(tmp_path, monkeypatch):
    monkeypatch.setenv("EDTB_INFOBASE_PATH", str(tmp_path / "ib"))
    res = await edtb_cf_artifact("dump", str(tmp_path / "out.cf"))
    assert not res["ok"]
    assert res["error"]["code"] == "CLI_NOT_FOUND"
    assert "EDTB_DESIGNER_CMD" in res["error"]["message"]
