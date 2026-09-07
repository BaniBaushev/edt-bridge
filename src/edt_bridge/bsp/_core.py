"""Адаптер интеграции с ядром edt-bridge.

Модули M4 (bsp, extensions) не дублируют ядро: они пробуют использовать
`edt_bridge.config`, `edt_bridge.safety`, `edt_bridge.proxy.*`. Если ядро
ещё не собрано (например, при unit-тестах изолированного worktree),
используются минимальные совместимые реализации по контрактам SPEC
(разделы 2 и 5), а в результат добавляется warning.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

_ENV_PROJECT_PATH = "EDTB_PROJECT_PATH"
_ENV_ALLOW_MUTATIONS = "EDTB_ALLOW_FILE_MUTATIONS"
_ENV_EDT_MCP_URL = "EDTB_EDT_MCP_URL"


def resolve_project_path(project_path: str | None = None) -> Path | None:
    """Вернуть корень EDT-проекта (аргумент или env), None если не задан."""
    try:
        from edt_bridge import config  # type: ignore

        resolver = getattr(config, "resolve_project_path", None) or getattr(
            config, "get_project_path", None
        )
        if resolver is not None:
            resolved = resolver(project_path)
            return Path(resolved) if resolved else None
    except Exception:
        pass
    raw = project_path or os.environ.get(_ENV_PROJECT_PATH)
    return Path(raw) if raw else None


def file_mutations_allowed() -> bool:
    """SPEC 5.1: файловые мутации только при EDTB_ALLOW_FILE_MUTATIONS=1."""
    try:
        from edt_bridge import config  # type: ignore

        checker = getattr(config, "file_mutations_allowed", None) or getattr(
            config, "allow_file_mutations", None
        )
        if checker is not None:
            return bool(checker())
    except Exception:
        pass
    return os.environ.get(_ENV_ALLOW_MUTATIONS) == "1"


def git_dirty_warnings(project_root: Path) -> list[str]:
    """SPEC 5.2: warning, если рабочее дерево проекта dirty."""
    try:
        from edt_bridge import safety  # type: ignore

        checker = getattr(safety, "git_dirty_warnings", None) or getattr(
            safety, "check_git_checkpoint", None
        )
        if checker is not None:
            result = checker(project_root)
            if isinstance(result, list):
                return [str(item) for item in result]
            if result:
                return ["git-дерево проекта содержит незакоммиченные изменения"]
            return []
    except Exception:
        pass
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return [
                "git-дерево проекта содержит незакоммиченные изменения "
                "(рекомендуется checkpoint перед мутациями)"
            ]
    except Exception:
        return ["не удалось проверить git status проекта"]
    return []


def backup_file(path: Path, project_root: Path) -> Path | None:
    """SPEC 5.3: backup файла в .edtb-backup/<timestamp>/ перед правкой."""
    try:
        from edt_bridge import safety  # type: ignore

        backup = getattr(safety, "backup_file", None)
        if backup is not None:
            return backup(path, project_root)
    except Exception:
        pass
    if not path.exists():
        return None
    import shutil
    from datetime import datetime

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = project_root / ".edtb-backup" / stamp
    try:
        relative = path.relative_to(project_root)
    except ValueError:
        relative = Path(path.name)
    target = backup_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, target)
    return target


def proxy_call(tool_name: str, arguments: dict) -> tuple[bool, dict]:
    """Вызов инструмента EDT-MCP через proxy ядра.

    Возвращает (доступен_ли_proxy, ответ). При недоступности —
    (False, {"error": ...}), вызывающий код обязан деградировать с warning.
    """
    try:
        from edt_bridge.proxy import client  # type: ignore

        caller = getattr(client, "call_tool", None)
        if caller is None:
            client_cls = getattr(client, "EdtMcpClient", None)
            if client_cls is None:
                raise RuntimeError("в proxy.client нет call_tool/EdtMcpClient")

            async def caller(name, args, _cls=client_cls):
                return await _cls().call_tool(name, args)

        import asyncio

        result = caller(tool_name, arguments)
        if asyncio.iscoroutine(result):
            result = asyncio.run(result)
        return True, result if isinstance(result, dict) else {"result": result}
    except Exception as exc:  # proxy недоступен — file-only режим
        return False, {"error": f"EDT-MCP недоступен: {exc}"}


def resync_after_file_changes(project_root: Path) -> list[str]:
    """SPEC 5.4: resync_to_disk + revalidate_objects; иначе warning."""
    try:
        from edt_bridge.proxy import resync  # type: ignore

        runner = getattr(resync, "resync_after_file_changes", None) or getattr(
            resync, "run", None
        )
        if runner is not None:
            import asyncio

            result = runner(project_root)
            if asyncio.iscoroutine(result):
                result = asyncio.run(result)
            if isinstance(result, list):
                return [str(item) for item in result]
            if isinstance(result, dict):
                if result.get("synced") is False or result.get("ok") is False:
                    return ["EDT-MCP недоступен: выполните resync_to_disk вручную в 1C:EDT"]
            return []
    except Exception:
        pass
    ok, _ = proxy_call("resync_to_disk", {"projectPath": str(project_root)})
    if not ok:
        return ["EDT-MCP недоступен: выполните resync_to_disk вручную в 1C:EDT"]
    proxy_call("revalidate_objects", {"projectPath": str(project_root)})
    return []
