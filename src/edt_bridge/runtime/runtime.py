"""Runtime-инструменты: обёртки внешних CLI (GAP-RUNTIME-EXECUTE, GAP-CF-ARTIFACTS).

- ``edtb_designer_check`` — запуск ``ring edt validate`` или ``1cedtcli``
  для проверки проекта/расширения без живого EDT; парсинг вывода в
  структуру {errors, warnings}.
- ``edtb_cf_artifact`` — выгрузка/загрузка артефактов поставки .cf/.cfe
  через 1cv8 DESIGNER (или ring).

Все запуски — asyncio subprocess, с таймаутом, без интерактива.
Если CLI не найден — структурированная ошибка с инструкцией по установке.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
from pathlib import Path
from typing import Any, Optional

DEFAULT_TIMEOUT_SEC = 300

INSTALL_HINT = (
    "Установите 1C:EDT CLI (https://releases.1c.ru/project/DevelopmentTools10) "
    "и/или утилиту ring из EDT, затем задайте пути в переменных окружения "
    "EDTB_RING_CMD и/или EDTB_EDTCLI_CMD (например: "
    "EDTB_RING_CMD=/opt/1C/1CE/components/ring/ring)."
)


def _timeout_sec() -> int:
    try:
        return int(os.environ.get("EDTB_CLI_TIMEOUT", DEFAULT_TIMEOUT_SEC))
    except ValueError:
        return DEFAULT_TIMEOUT_SEC


def _find_cli(env_var: str, default_names: list[str]) -> Optional[str]:
    """Путь к CLI: переменная окружения, затем поиск в PATH."""
    cmd = os.environ.get(env_var)
    if cmd:
        return cmd
    for name in default_names:
        found = shutil.which(name)
        if found:
            return found
    return None


def _cli_not_found(tool: str, env_vars: list[str]) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": "CLI_NOT_FOUND",
            "tool": tool,
            "message": (
                f"Не найден внешний CLI «{tool}» (проверены переменные "
                f"{', '.join(env_vars)} и PATH). {INSTALL_HINT}"
            ),
        },
        "errors": [],
        "warnings": [],
    }


# ---------------------------------------------------------------------------
# Парсинг вывода CLI
# ---------------------------------------------------------------------------

_ERROR_RE = re.compile(r"(?i)\b(error|ошибка)\b")
_WARNING_RE = re.compile(r"(?i)\b(warning|warn|предупреждение)\b")


def _parse_check_output(text: str) -> tuple[list[str], list[str]]:
    """Разбор вывода ring/1cedtcli на ошибки и предупреждения (по маркерам строк)."""
    errors: list[str] = []
    warnings: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if _ERROR_RE.search(line):
            errors.append(line)
        elif _WARNING_RE.search(line):
            warnings.append(line)
    return errors, warnings


async def _run_cli(argv: list[str], timeout: int) -> tuple[int, str, str]:
    """Запуск CLI через asyncio subprocess с таймаутом, без интерактива."""
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.DEVNULL,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise TimeoutError(
            f"Команда {' '.join(argv)!r} превысила таймаут {timeout} с "
            "(переменная EDTB_CLI_TIMEOUT)."
        )
    return (
        proc.returncode or 0,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


async def edtb_designer_check(
    scope: str = "project",
    projectPath: Optional[str] = None,
) -> dict[str, Any]:
    """Проверка проекта/расширения: ring edt validate или 1cedtcli.

    scope: "project" | "extension".
    Возвращает {ok, tool, errors, warnings, raw} либо структурированную
    ошибку CLI_NOT_FOUND с инструкцией по установке.
    """
    warnings: list[str] = []
    if scope not in {"project", "extension"}:
        return {
            "ok": False,
            "error": f"Недопустимый scope {scope!r}. Допустимы: project, extension.",
            "errors": [],
            "warnings": warnings,
        }

    project = projectPath or os.environ.get("EDTB_PROJECT_PATH")
    if not project:
        return {
            "ok": False,
            "error": (
                "Не задан путь к проекту: передайте projectPath или установите "
                "переменную окружения EDTB_PROJECT_PATH."
            ),
            "errors": [],
            "warnings": warnings,
        }
    project = str(Path(project).resolve())

    ring = _find_cli("EDTB_RING_CMD", ["ring"])
    edtcli = _find_cli("EDTB_EDTCLI_CMD", ["1cedtcli"])

    if ring:
        tool, argv = "ring", [ring, "edt", "validate", "--project", project]
    elif edtcli:
        tool = "1cedtcli"
        argv = [
            edtcli,
            "-data", project,
            "-command", "syntax-check" if scope == "project" else "syntax-check-extensions",
        ]
    else:
        return _cli_not_found("ring/1cedtcli", ["EDTB_RING_CMD", "EDTB_EDTCLI_CMD"])

    timeout = _timeout_sec()
    try:
        code, stdout, stderr = await _run_cli(argv, timeout)
    except TimeoutError as exc:
        return {
            "ok": False,
            "tool": tool,
            "error": {"code": "CLI_TIMEOUT", "message": str(exc)},
            "errors": [],
            "warnings": warnings,
        }
    except OSError as exc:
        return {
            "ok": False,
            "tool": tool,
            "error": {
                "code": "CLI_START_FAILED",
                "message": f"Не удалось запустить {tool}: {exc}. {INSTALL_HINT}",
            },
            "errors": [],
            "warnings": warnings,
        }

    raw = (stdout + "\n" + stderr).strip()
    errors, parse_warnings = _parse_check_output(raw)
    warnings += parse_warnings
    if code != 0 and not errors:
        errors.append(f"{tool} завершился с кодом {code}: {raw[-2000:]}")
    if stderr.strip() and code == 0:
        warnings.append(f"stderr {tool}: {stderr.strip()[-500:]}")

    return {
        "ok": code == 0 and not errors,
        "tool": tool,
        "scope": scope,
        "projectPath": project,
        "exitCode": code,
        "errors": errors,
        "warnings": warnings,
        "raw": raw,
    }


async def edtb_cf_artifact(
    action: str,
    path: str,
    projectPath: Optional[str] = None,
) -> dict[str, Any]:
    """Выгрузка/загрузка артефакта поставки .cf/.cfe.

    action: "dump" — выгрузить из ИБ в файл path; "load" — загрузить файл в ИБ.
    Используется 1cv8 DESIGNER (EDTB_DESIGNER_CMD) с параметрами ИБ из
    EDTB_INFOBASE_PATH (файловая ИБ); без интерактива, с таймаутом.
    """
    warnings: list[str] = []
    if action not in {"dump", "load"}:
        return {
            "ok": False,
            "error": f"Недопустимое действие {action!r}. Допустимы: dump, load.",
            "errors": [],
            "warnings": warnings,
        }

    artifact = Path(path)
    if action == "load" and not artifact.is_file():
        return {
            "ok": False,
            "error": f"Файл артефакта не найден: {artifact}.",
            "errors": [],
            "warnings": warnings,
        }
    if artifact.suffix.lower() not in {".cf", ".cfe"}:
        warnings.append(
            f"Необычное расширение артефакта {artifact.suffix!r}: ожидается .cf или .cfe."
        )

    infobase = os.environ.get("EDTB_INFOBASE_PATH")
    if not infobase:
        return {
            "ok": False,
            "error": (
                "Не задана информационная база: установите переменную окружения "
                "EDTB_INFOBASE_PATH (путь к файловой ИБ, с которой связан проект)."
            ),
            "errors": [],
            "warnings": warnings,
        }

    designer = _find_cli("EDTB_DESIGNER_CMD", ["1cv8"])
    if not designer:
        return {
            "ok": False,
            "error": {
                "code": "CLI_NOT_FOUND",
                "tool": "1cv8",
                "message": (
                    "Не найден исполняемый файл платформы 1cv8 (EDTB_DESIGNER_CMD / PATH). "
                    "Установите платформу 1С:Предприятие и задайте EDTB_DESIGNER_CMD, "
                    "например: /opt/1cv8/x86_64/<версия>/1cv8."
                ),
            },
            "errors": [],
            "warnings": warnings,
        }

    # /DisableStartupDialogs /DisableStartupMessages — без интерактива.
    op = "/DumpCfg" if action == "dump" else "/LoadCfg"
    argv = [
        designer, "DESIGNER",
        "/F", infobase,
        op, str(artifact.resolve()),
        "/DisableStartupDialogs", "/DisableStartupMessages",
    ]

    timeout = _timeout_sec()
    try:
        code, stdout, stderr = await _run_cli(argv, timeout)
    except TimeoutError as exc:
        return {
            "ok": False,
            "error": {"code": "CLI_TIMEOUT", "message": str(exc)},
            "errors": [],
            "warnings": warnings,
        }
    except OSError as exc:
        return {
            "ok": False,
            "error": {"code": "CLI_START_FAILED", "message": f"Не удалось запустить 1cv8: {exc}."},
            "errors": [],
            "warnings": warnings,
        }

    raw = (stdout + "\n" + stderr).strip()
    errors, parse_warnings = _parse_check_output(raw)
    warnings += parse_warnings
    if code != 0 and not errors:
        errors.append(f"1cv8 DESIGNER завершился с кодом {code}: {raw[-2000:]}")

    if action == "dump" and code == 0 and not artifact.is_file():
        errors.append(f"1cv8 завершился успешно, но файл {artifact} не создан.")

    return {
        "ok": code == 0 and not errors,
        "action": action,
        "path": str(artifact.resolve()),
        "infobase": infobase,
        "exitCode": code,
        "errors": errors,
        "warnings": warnings,
        "raw": raw,
    }
