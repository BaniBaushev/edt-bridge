"""Безопасность файловых мутаций: git dirty-check, backup, preview-diff.

См. SPEC раздел 5:
- перед пакетом мутаций — проверка ``git status`` (warning если dirty);
- перед правкой файла — backup в ``.edtb-backup/<timestamp>/``;
- dryRun возвращает unified-diff без применения.
"""

from __future__ import annotations

import difflib
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

BACKUP_DIR_NAME = ".edtb-backup"


@dataclass
class Backup:
    """Сессия backup'а: каталог и реестр скопированных файлов."""

    root: Path
    session_dir: Path
    files: dict[Path, Path] = field(default_factory=dict)  # оригинал -> копия

    def restore_all(self) -> list[str]:
        """Восстановить все файлы из backup'а. Возвращает ошибки восстановления."""
        errors: list[str] = []
        for original, copy in self.files.items():
            try:
                if copy.exists():
                    original.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(copy, original)
                elif original.exists():
                    # Файл был создан мутацией — удалить при откате.
                    original.unlink()
            except OSError as exc:
                errors.append(f"Не удалось восстановить {original}: {exc}")
        return errors


def git_dirty_check(project_root: Path) -> str | None:
    """Проверить ``git status`` проекта.

    :return: текст warning'а, если рабочее дерево dirty или git недоступен;
             ``None``, если дерево чистое.
    """
    if not (project_root / ".git").exists():
        return None  # не git-репозиторий — проверка неприменима
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"Не удалось выполнить git status: {exc}"
    if proc.returncode != 0:
        return f"git status завершился с ошибкой: {proc.stderr.strip()}"
    if proc.stdout.strip():
        return (
            "Рабочее дерево git содержит незакоммиченные изменения — "
            "рекомендуется закоммитить или stash перед пакетом мутаций"
        )
    return None


def create_backup(project_root: Path, files: list[Path]) -> Backup:
    """Создать backup-сессию и скопировать перечисленные файлы.

    Копия сохраняет относительный путь внутри ``.edtb-backup/<timestamp>/``.
    Несуществующие файлы помечаются в реестре без копии (для удаления при
    откате созданных файлов).
    """
    ts = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    session_dir = project_root / BACKUP_DIR_NAME / ts
    session_dir.mkdir(parents=True, exist_ok=False)
    backup = Backup(root=project_root, session_dir=session_dir)
    for f in files:
        f = Path(f)
        try:
            rel = f.resolve().relative_to(project_root.resolve())
        except ValueError:
            rel = Path("_external") / f.name
        dest = session_dir / rel
        if f.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dest)
            backup.files[f] = dest
        else:
            backup.files[f] = dest  # копии нет — при restore файл удалится
    return backup


def unified_diff_preview(
    path: Path, new_content: str, encoding: str = "utf-8"
) -> str:
    """Построить unified-diff между текущим содержимым файла и новым.

    Для несуществующего файла — diff «создание файла».
    """
    if path.exists():
        old_lines = path.read_text(encoding=encoding).splitlines(keepends=True)
    else:
        old_lines = []
    new_lines = new_content.splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )
