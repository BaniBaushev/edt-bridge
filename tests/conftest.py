# -*- coding: utf-8 -*-
"""Общие фикстуры/стабы для тестов M2 без живого EDT.

Core-модули (edt_bridge.config/safety/proxy) могут быть ещё не смержены —
здесь минимальные стабы в sys.modules, только для тестов.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

import sys as _sys
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in _sys.path:
    _sys.path.insert(0, str(_SRC))

FIXTURES = Path(__file__).parent / "fixtures"


class _Config:
    def __init__(self, path: Path, allow: bool = True) -> None:
        self.allow_file_mutations = allow
        self.project_path = str(path)


class _Project:
    def __init__(self, path: Path) -> None:
        self.path = str(path)
        self.name = "TestProject"


class _ConfigModule(types.ModuleType):
    current_path: Path = FIXTURES
    allow_mutations = True

    def get_config(self):
        return _Config(self.current_path, self.allow_mutations)

    def get_project(self, project_path=None):
        return _Project(Path(project_path) if project_path
                        else self.current_path)


class _SafetyModule(types.ModuleType):
    def check_git_dirty(self, project_path):
        return []

    def backup_file(self, path):
        path = Path(path)
        backup = path.with_name(path.name + ".bak")
        if path.is_dir():
            import shutil
            shutil.copytree(path, backup)
        elif path.exists():
            backup.write_bytes(path.read_bytes())
        return str(backup)


class _ClientModule(types.ModuleType):
    calls: list = []
    search_results: list = []

    async def call_tool(self, name, args):
        self.calls.append({"tool": name, "args": args})
        if name == "search_in_code":
            return {"results": list(self.search_results)}
        return {"ok": True}


class _ResyncModule(types.ModuleType):
    called = 0

    async def resync_to_disk(self, project):
        type(self).called += 1
        return {"ok": True}


def _install_stubs() -> dict[str, types.ModuleType]:
    """Установить стабы core-модулей, если реальных ещё нет."""
    stubs: dict[str, types.ModuleType] = {}
    pkg = sys.modules.get("edt_bridge")

    def ensure(name, cls):
        full = f"edt_bridge.{name}"
        try:
            __import__(full)
            return sys.modules[full]
        except ImportError:
            module = cls(full)
            sys.modules[full] = module
            if pkg is not None:
                setattr(pkg, name, module)
            return module

    stubs["config"] = ensure("config", _ConfigModule)
    stubs["safety"] = ensure("safety", _SafetyModule)

    proxy = ensure("proxy", types.ModuleType)
    try:
        __import__("edt_bridge.proxy.client")
        client = sys.modules["edt_bridge.proxy.client"]
    except ImportError:
        client = _ClientModule("edt_bridge.proxy.client")
        sys.modules["edt_bridge.proxy.client"] = client
        setattr(proxy, "client", client)
    try:
        __import__("edt_bridge.proxy.resync")
        resync = sys.modules["edt_bridge.proxy.resync"]
    except ImportError:
        resync = _ResyncModule("edt_bridge.proxy.resync")
        sys.modules["edt_bridge.proxy.resync"] = resync
        setattr(proxy, "resync", resync)
    stubs["client"] = client
    stubs["resync"] = resync
    return stubs


@pytest.fixture()
def core_stubs(tmp_path, monkeypatch):
    """Стабы ядра с project root = tmp_path."""
    stubs = _install_stubs()
    monkeypatch.setenv("EDTB_ALLOW_FILE_MUTATIONS", "1")

    client = stubs["client"]
    if not isinstance(client, _ClientModule):
        # Реальный клиент сетевой — подменяем call_tool стабом на время теста.
        client.calls = []
        client.search_results = []

        async def _fake_call_tool(self, name, args=None):
            client.calls.append({"tool": name, "args": args})
            if name == "search_in_code":
                return {"results": list(client.search_results)}
            return {"ok": True}

        monkeypatch.setattr(client.EdtMcpClient, "call_tool", _fake_call_tool)

    resync = stubs["resync"]
    if not isinstance(resync, _ResyncModule):
        resync.called = 0

        async def _fake_resync(path):
            resync.called += 1
            return {"synced": True}

        monkeypatch.setattr("edt_bridge.forms.forms._resync", _fake_resync)

    if isinstance(stubs["config"], _ConfigModule):
        stubs["config"].current_path = tmp_path
        stubs["config"].allow_mutations = True
    if isinstance(stubs["client"], _ClientModule):
        stubs["client"].calls = []
        stubs["client"].search_results = []
    return stubs


@pytest.fixture()
def form_xml_text() -> str:
    return (FIXTURES / "Form.form").read_text(encoding="utf-8")


@pytest.fixture()
def project_with_form(tmp_path, form_xml_text):
    """Минимальный EDT-проект с формой Catalog.Товары/ФормаЭлемента."""
    from edt_bridge.forms.forms import form_file
    (tmp_path / ".project").write_text("<projectDescription/>", encoding="utf-8")
    (tmp_path / "DT-INF").mkdir(exist_ok=True)
    (tmp_path / "DT-INF" / "PROJECT.PMF").write_text("", encoding="utf-8")
    path = form_file(tmp_path, "Catalog.Товары", "ФормаЭлемента")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(form_xml_text, encoding="utf-8")
    return tmp_path


# --- Фикстуры M1 (core) и M5 (interface-runtime), добавлены при мерже ---
import shutil as _shutil

FIXTURE_PROJECT = Path(__file__).parent / "fixtures" / "fake-edt-project"


@pytest.fixture()
def edt_project(tmp_path: Path) -> Path:
    """Изолированная копия фейк-EDT-проекта (можно мутировать в тесте)."""
    dest = tmp_path / "project"
    _shutil.copytree(FIXTURE_PROJECT, dest)
    return dest


@pytest.fixture()
def project(tmp_path, monkeypatch):
    """Минимальный проект: подсистема Продажи + Configuration.mdo."""
    proj = tmp_path / "project"
    (proj / "src" / "Subsystems" / "Продажи").mkdir(parents=True)
    _shutil.copy(FIXTURES / "CommandInterface.xml",
                 proj / "src" / "Subsystems" / "Продажи" / "CommandInterface.xml")
    (proj / "src" / "Configuration").mkdir(parents=True)
    _shutil.copy(FIXTURES / "Configuration.mdo",
                 proj / "src" / "Configuration" / "Configuration.mdo")
    monkeypatch.setenv("EDTB_PROJECT_PATH", str(proj))
    monkeypatch.setenv("EDTB_ALLOW_FILE_MUTATIONS", "1")
    return proj
