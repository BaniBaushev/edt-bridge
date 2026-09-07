"""Конфигурация edt-bridge: переменные окружения и discovery EDT-проекта.

Маркеры EDT-проекта (проверяются при discovery):
- ``.project`` — файл проекта Eclipse/EDT;
- ``DT-INF/`` — служебный каталог EDT;
- ``src/Configuration/Configuration.mdo`` — корневой объект конфигурации.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

#: Маркеры корня EDT-проекта: обязательные и опциональные.
PROJECT_MARKERS_REQUIRED = (".project", "DT-INF")
PROJECT_MARKERS_OPTIONAL = ("src/Configuration/Configuration.mdo",)

DEFAULT_EDT_MCP_URL = "http://localhost:8765/mcp"


class ConfigError(Exception):
    """Ошибка конфигурации или discovery EDT-проекта."""


@dataclass
class Config:
    """Снимок конфигурации edt-bridge."""

    project_path: Path | None = None
    edt_mcp_url: str = DEFAULT_EDT_MCP_URL
    allow_file_mutations: bool = False
    ring_cmd: str | None = None
    edtcli_cmd: str | None = None
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> "Config":
        """Прочитать конфигурацию из переменных окружения.

        :param environ: переопределение окружения (для тестов).
        """
        env = os.environ if environ is None else environ
        cfg = cls(
            edt_mcp_url=env.get("EDTB_EDT_MCP_URL", DEFAULT_EDT_MCP_URL),
            allow_file_mutations=env.get("EDTB_ALLOW_FILE_MUTATIONS", "0") == "1",
            ring_cmd=env.get("EDTB_RING_CMD") or None,
            edtcli_cmd=env.get("EDTB_EDTCLI_CMD") or None,
        )
        raw_path = env.get("EDTB_PROJECT_PATH")
        if raw_path:
            try:
                cfg.project_path = discover_project(raw_path)
            except ConfigError as exc:
                cfg.warnings.append(str(exc))
        return cfg


def discover_project(path: str | Path) -> Path:
    """Проверить, что ``path`` — корень EDT-проекта, и вернуть его.

    :raises ConfigError: если путь не существует или маркеры не найдены.
    """
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        raise ConfigError(f"Каталог проекта не существует: {root}")
    missing = [m for m in PROJECT_MARKERS_REQUIRED if not (root / m).exists()]
    if missing:
        raise ConfigError(
            f"Путь {root} не похож на корень EDT-проекта: "
            f"отсутствуют маркеры {', '.join(missing)}"
        )
    return root


def resolve_project_path(project_path: str | None = None) -> Path:
    """Определить корень проекта: аргумент инструмента или ``EDTB_PROJECT_PATH``.

    :param project_path: опциональный projectPath из вызова инструмента.
    :raises ConfigError: если путь не задан или невалиден.
    """
    raw = project_path or os.environ.get("EDTB_PROJECT_PATH")
    if not raw:
        raise ConfigError(
            "Путь к проекту не задан: передайте projectPath "
            "или установите EDTB_PROJECT_PATH"
        )
    return discover_project(raw)


def has_configuration_mdo(root: Path) -> bool:
    """Проверить наличие корневого Configuration.mdo (полный EDT-проект)."""
    return (root / "src/Configuration/Configuration.mdo").exists()


def file_mutations_allowed() -> bool:
    """Разрешены ли пишущие файловые операции (EDTB_ALLOW_FILE_MUTATIONS=1)."""
    return os.environ.get("EDTB_ALLOW_FILE_MUTATIONS", "0") == "1"


def edt_mcp_url() -> str:
    """URL EDT-MCP сервера (EDTB_EDT_MCP_URL или default)."""
    return os.environ.get("EDTB_EDT_MCP_URL", DEFAULT_EDT_MCP_URL)
