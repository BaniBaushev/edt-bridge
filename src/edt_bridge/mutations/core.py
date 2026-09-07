"""Ядро пакетов мутаций: схемы ops, валидация, planHash, confirm-токены.

Ops (SPEC 4.1):
- ``createMetadata`` — создание объекта метаданных (стратегия edt-mcp);
- ``modifyMetadata`` — изменение свойств объекта (стратегия edt-mcp);
- ``deleteMetadata`` — удаление объекта, двухфазное (confirm token);
- ``writeModule``   — запись исходника модуля (стратегия edt-mcp);
- ``patchFile``     — xpath-подобный сеттер XML-файла (стратегия file).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..config import ConfigError, resolve_project_path

#: Обязательные поля и стратегии для каждого вида op.
OP_SCHEMAS: dict[str, dict[str, Any]] = {
    "createMetadata": {"required": ("type", "name"), "strategy": "edt-mcp"},
    "modifyMetadata": {"required": ("name", "properties"), "strategy": "edt-mcp"},
    "deleteMetadata": {"required": ("name",), "strategy": "edt-mcp"},
    "writeModule": {"required": ("objectName", "moduleType", "source"), "strategy": "edt-mcp"},
    "patchFile": {"required": ("path", "changes"), "strategy": "file"},
}

#: Инструменты EDT-MCP, используемые по стратегиям (только канонические имена).
EDT_MCP_TOOL_BY_OP = {
    "createMetadata": "create_metadata",
    "modifyMetadata": "modify_metadata",
    "deleteMetadata": "delete_metadata",
    "writeModule": "write_module_source",
}


def canonical_json(data: Any) -> str:
    """Каноничная JSON-сериализация (стабильный порядок ключей)."""
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def normalize_ops(ops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Нормализовать ops для hash: убрать служебное поле ``confirm``.

    Confirm-токен двухфазного удаления не должен менять hash плана.
    """
    return [
        {k: v for k, v in op.items() if k != "confirm"} if isinstance(op, dict) else op
        for op in ops
    ]


def plan_hash(ops: list[dict[str, Any]], project_path: str | None = None) -> str:
    """sha256 каноничного JSON плана (нормализованных ops) + projectPath.

    projectPath входит в hash: план с одного проекта нельзя применить
    к другому. ВНИМАНИЕ: hash НЕ защищает от изменения состояния проекта
    между plan и apply — применяйте план сразу после построения.
    """
    payload = {"ops": normalize_ops(ops), "projectPath": project_path or ""}
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def delete_confirm_token(name: str, p_hash: str) -> str:
    """Confirm-токен двухфазного удаления (SPEC 5.6)."""
    raw = f"confirm-delete::{name}::{p_hash}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _validate_patch_changes(changes: Any, warnings: list[str]) -> bool:
    """Проверить сетку изменений patchFile: xpath + действие."""
    if not isinstance(changes, list) or not changes:
        warnings.append("patchFile: changes должен быть непустым списком")
        return False
    ok = True
    for i, ch in enumerate(changes):
        if not isinstance(ch, dict) or not ch.get("xpath"):
            warnings.append(f"patchFile: changes[{i}] — требуется поле xpath")
            ok = False
            continue
        actions = [k for k in ("text", "attribute", "remove") if k in ch]
        if not actions:
            warnings.append(
                f"patchFile: changes[{i}] — задайте text, attribute или remove"
            )
            ok = False
        if "attribute" in ch and not ch.get("attribute"):
            warnings.append(f"patchFile: changes[{i}] — пустое имя атрибута")
            ok = False
    return ok


def resolve_target_path(project_root: Path, rel_path: str) -> Path:
    """Безопасно разрешить путь цели ВНУТРИ корня проекта.

    Отклоняет абсолютные пути и выход за пределы проекта (../../).
    """
    root = Path(project_root).resolve()
    target = (root / rel_path).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            f"Путь {rel_path!r} выходит за пределы проекта {root} — отклонено"
        ) from exc
    return target


def _target_exists(project_root: Path, op: dict[str, Any]) -> bool | None:
    """Адресуемость цели: None — не проверяется (только через EDT-MCP)."""
    if op["op"] == "patchFile":
        return resolve_target_path(project_root, op["path"]).exists()
    return None


def plan_mutations(
    ops: list[dict[str, Any]], project_path: str | None = None
) -> dict[str, Any]:
    """Провалидировать пакет операций БЕЗ применения (SPEC 4.1).

    :return: ``{ok, ops: [{index, op, target, strategy, warnings, ...}], hash}``.
             Для deleteMetadata в записи есть ``confirmToken``.
    """
    result: dict[str, Any] = {"ok": True, "ops": [], "hash": "", "warnings": []}
    if not isinstance(ops, list) or not ops:
        result["ok"] = False
        result["warnings"].append("ops должен быть непустым списком")
        return result

    project_root: Path | None = None
    try:
        project_root = resolve_project_path(project_path)
    except ConfigError as exc:
        result["warnings"].append(str(exc))

    for index, op in enumerate(ops):
        warnings: list[str] = []
        entry: dict[str, Any] = {
            "index": index,
            "op": op.get("op"),
            "target": None,
            "strategy": None,
            "warnings": warnings,
        }
        result["ops"].append(entry)
        if not isinstance(op, dict):
            warnings.append("операция должна быть объектом")
            result["ok"] = False
            continue
        kind = op.get("op")
        schema = OP_SCHEMAS.get(kind or "")
        if schema is None:
            warnings.append(
                f"неизвестная операция {kind!r}; допустимы: "
                + ", ".join(sorted(OP_SCHEMAS))
            )
            result["ok"] = False
            continue
        entry["strategy"] = schema["strategy"]
        missing = [f for f in schema["required"] if f not in op]
        if missing:
            warnings.append(f"отсутствуют обязательные поля: {', '.join(missing)}")
            result["ok"] = False
            continue
        entry["target"] = op.get("path") or op.get("name") or op.get("objectName")
        if kind == "patchFile":
            if not _validate_patch_changes(op.get("changes"), warnings):
                result["ok"] = False
            if project_root is not None:
                try:
                    exists = _target_exists(project_root, op)
                except ValueError as exc:
                    warnings.append(str(exc))
                    result["ok"] = False
                    exists = None
                if exists is False:
                    warnings.append(f"файл не существует: {op.get('path')}")
                    result["ok"] = False
        if kind == "deleteMetadata":
            entry["twoPhase"] = True

    result["hash"] = plan_hash(ops, project_path)
    # Confirm-токены привязаны к hash плана.
    for entry in result["ops"]:
        if entry.get("twoPhase"):
            name = ops[entry["index"]].get("name", "")
            entry["confirmToken"] = delete_confirm_token(name, result["hash"])
    return result
