"""Тесты конфигурации и discovery EDT-проекта (без живого EDT)."""

from __future__ import annotations

import pytest

from edt_bridge.config import (
    DEFAULT_EDT_MCP_URL,
    Config,
    ConfigError,
    discover_project,
    has_configuration_mdo,
    resolve_project_path,
)


def test_discover_ok(edt_project):
    """Валидный фейк-проект распознаётся как EDT-проект."""
    assert discover_project(edt_project) == edt_project.resolve()
    assert has_configuration_mdo(edt_project)


def test_discover_missing_dir(tmp_path):
    """Несуществующий каталог — ConfigError."""
    with pytest.raises(ConfigError):
        discover_project(tmp_path / "nope")


def test_discover_missing_markers(tmp_path):
    """Каталог без маркеров .project/DT-INF — ConfigError."""
    (tmp_path / "some.txt").write_text("x")
    with pytest.raises(ConfigError, match="маркеры"):
        discover_project(tmp_path)


def test_discover_without_configuration_mdo(tmp_path):
    """Минимальные маркеры достаточны; Configuration.mdo опционален."""
    (tmp_path / ".project").write_text("<projectDescription/>")
    (tmp_path / "DT-INF").mkdir()
    assert discover_project(tmp_path) == tmp_path.resolve()
    assert not has_configuration_mdo(tmp_path)


def test_resolve_prefers_argument(edt_project, monkeypatch):
    """Аргумент projectPath важнее переменной окружения."""
    monkeypatch.delenv("EDTB_PROJECT_PATH", raising=False)
    assert resolve_project_path(str(edt_project)) == edt_project.resolve()


def test_resolve_from_env(edt_project, monkeypatch):
    """Путь берётся из EDTB_PROJECT_PATH, если аргумент не передан."""
    monkeypatch.setenv("EDTB_PROJECT_PATH", str(edt_project))
    assert resolve_project_path(None) == edt_project.resolve()


def test_resolve_not_set(monkeypatch):
    """Без пути — понятная ошибка."""
    monkeypatch.delenv("EDTB_PROJECT_PATH", raising=False)
    with pytest.raises(ConfigError, match="EDTB_PROJECT_PATH"):
        resolve_project_path(None)


def test_config_from_env_defaults(monkeypatch):
    """Дефолты: URL EDT-MCP, мутации запрещены."""
    monkeypatch.delenv("EDTB_PROJECT_PATH", raising=False)
    monkeypatch.delenv("EDTB_EDT_MCP_URL", raising=False)
    monkeypatch.delenv("EDTB_ALLOW_FILE_MUTATIONS", raising=False)
    cfg = Config.from_env()
    assert cfg.edt_mcp_url == DEFAULT_EDT_MCP_URL
    assert cfg.allow_file_mutations is False
    assert cfg.project_path is None


def test_config_from_env_invalid_project_warns(tmp_path):
    """Невалидный EDTB_PROJECT_PATH — warning, а не падение."""
    cfg = Config.from_env({"EDTB_PROJECT_PATH": str(tmp_path / "nope")})
    assert cfg.project_path is None
    assert cfg.warnings
