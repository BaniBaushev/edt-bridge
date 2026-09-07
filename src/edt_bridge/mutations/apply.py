"""Применение пакета мутаций: двухфазно, с backup/rollback и resync.

SPEC 4.1/5: planHash от свежего plan, git dirty-check, backup перед правкой,
dryRun с diff-preview, двухфазное удаление, rollback при ошибке середины
пакета, resync после файловых правок (warning-fallback).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import ConfigError, file_mutations_allowed, resolve_project_path
from ..proxy.client import EdtMcpClient, EdtMcpError, EdtMcpUnavailable
from ..proxy.resync import resync_after_file_changes
from ..safety import create_backup, git_dirty_check, unified_diff_preview
from .core import EDT_MCP_TOOL_BY_OP, delete_confirm_token, plan_mutations
from .patchfile import PatchError, patch_file, patch_file_preview


def _edt_mcp_arguments(op: dict[str, Any]) -> dict[str, Any]:
    """Собрать аргументы вызова EDT-MCP из полей op."""
    args = {k: v for k, v in op.items() if k not in ("op", "confirm")}
    return args


async def _apply_edt_mcp_op(
    op: dict[str, Any], client: EdtMcpClient
) -> dict[str, Any]:
    """Применить op через EDT-MCP proxy."""
    tool = EDT_MCP_TOOL_BY_OP[op["op"]]
    return await client.call_tool(tool, _edt_mcp_arguments(op))


async def apply_mutations(
    ops: list[dict[str, Any]],
    plan_hash: str,
    project_path: str | None = None,
    dry_run: bool = False,
    client: EdtMcpClient | None = None,
) -> dict[str, Any]:
    """Применить пакет мутаций (SPEC 4.1).

    :param ops: тот же список операций, что передавался в plan.
    :param plan_hash: hash из свежего :func:`plan_mutations` (анти-рассинхрон).
    :param project_path: опциональный projectPath.
    :param dry_run: True — вернуть план/diff без применения.
    :param client: опциональный клиент EDT-MCP (тесты/переиспользование).
    :return: ``{ok, dryRun, ops: [{index, op, status, detail}], previews,
             warnings, backupDir?, resync?}``.
    """
    warnings: list[str] = []
    result: dict[str, Any] = {
        "ok": True,
        "dryRun": dry_run,
        "ops": [],
        "previews": {},
        "warnings": warnings,
    }

    plan = plan_mutations(ops, project_path)
    if plan["hash"] != plan_hash:
        result["ok"] = False
        warnings.append(
            "planHash не совпадает с hash текущего пакета ops — "
            "выполните edtb_plan_mutations заново (защита от рассинхрона)"
        )
        return result
    if not plan["ok"]:
        result["ok"] = False
        warnings.extend(plan["warnings"])
        for entry in plan["ops"]:
            warnings.extend(f"op[{entry['index']}]: {w}" for w in entry["warnings"])
        return result
    warnings.extend(plan["warnings"])

    try:
        project_root = resolve_project_path(project_path)
    except ConfigError as exc:
        result["ok"] = False
        warnings.append(str(exc))
        return result

    # Двухфазное удаление: confirm token обязателен (SPEC 5.6).
    for entry in plan["ops"]:
        if entry.get("twoPhase") and not dry_run:
            op = ops[entry["index"]]
            expected = entry["confirmToken"]
            if op.get("confirm") != expected:
                result["ok"] = False
                warnings.append(
                    f"op[{entry['index']}] deleteMetadata '{op.get('name')}': "
                    f"требуется confirm token '{expected}' из свежего плана"
                )
                return result

    dirty = git_dirty_check(project_root)
    if dirty:
        warnings.append(dirty)

    file_ops = [
        (i, op) for i, op in enumerate(ops) if op.get("op") == "patchFile"
    ]
    if file_ops and not file_mutations_allowed() and not dry_run:
        result["ok"] = False
        warnings.append(
            "Файловые мутации запрещены: установите EDTB_ALLOW_FILE_MUTATIONS=1 "
            "или вызовите с dryRun=true для preview"
        )
        return result

    # dryRun: только preview.
    if dry_run:
        for i, op in file_ops:
            target = project_root / op["path"]
            try:
                new_content = patch_file_preview(target, op["changes"])
                result["previews"][op["path"]] = unified_diff_preview(
                    target, new_content
                )
            except PatchError as exc:
                warnings.append(f"op[{i}] patchFile: {exc}")
                result["ok"] = False
        for i, op in enumerate(ops):
            result["ops"].append(
                {"index": i, "op": op["op"], "status": "planned", "detail": None}
            )
        return result

    # Backup файлов, затрагиваемых file-стратегией.
    backup = None
    if file_ops:
        backup = create_backup(project_root, [project_root / op["path"] for _, op in file_ops])
        result["backupDir"] = str(backup.session_dir)

    client = client or EdtMcpClient()
    changed_files: list[Path] = []
    failed = False
    for i, op in enumerate(ops):
        status: dict[str, Any] = {"index": i, "op": op["op"], "status": "ok", "detail": None}
        result["ops"].append(status)
        try:
            if op["op"] == "patchFile":
                target = project_root / op["path"]
                patch_file(target, op["changes"])
                changed_files.append(target)
                status["detail"] = f"применено изменений: {len(op['changes'])}"
            else:
                try:
                    status["detail"] = await _apply_edt_mcp_op(op, client)
                except EdtMcpUnavailable as exc:
                    # Graceful degrade: op пропущена, пакет продолжается.
                    status["status"] = "skipped"
                    status["detail"] = str(exc)
                    warnings.append(f"op[{i}] {op['op']}: EDT-MCP недоступен — {exc}")
        except (PatchError, EdtMcpError, OSError) as exc:
            status["status"] = "error"
            status["detail"] = str(exc)
            failed = True
            break

    if failed:
        result["ok"] = False
        rollback_errors: list[str] = []
        if backup is not None and changed_files:
            rollback_errors = backup.restore_all()
        for err in rollback_errors:
            warnings.append(f"rollback: {err}")
        warnings.append(
            "Пакет прерван на ошибке: файловые изменения откачены из backup'а"
            if not rollback_errors and changed_files
            else "Пакет прерван на ошибке (см. статус ops)"
        )
        # resync после rollback, чтобы модель EDT не разошлась с диском.
        if changed_files:
            resync = await resync_after_file_changes(changed_files, client)
            warnings.extend(resync["warnings"])
        return result

    if changed_files:
        resync = await resync_after_file_changes(changed_files, client)
        result["resync"] = resync
        warnings.extend(resync["warnings"])
    return result
