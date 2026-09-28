"""Обновляет только исходники из отслеживаемой ветки Git перед запуском CRM."""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "app.db"
BACKUP_DIR = ROOT / "backups"


def git(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    return subprocess.run(
        ["git", "-C", str(ROOT), *args],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=45,
        env=env,
        check=False,
    )


def error_text(result: subprocess.CompletedProcess[str]) -> str:
    return (result.stderr or result.stdout or "неизвестная ошибка").strip()[:400]


def backup_database() -> Path | None:
    if not DB_PATH.is_file():
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    target = BACKUP_DIR / f"pre_update_{datetime.now():%Y%m%d_%H%M%S_%f}.db"
    try:
        with sqlite3.connect(str(DB_PATH)) as source:
            with sqlite3.connect(str(target)) as destination:
                source.backup(destination)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return target


def main() -> None:
    if not (ROOT / ".git").exists():
        print("Обновления GitHub: проект запущен из архива, Git пока не подключён.")
        return
    if shutil.which("git") is None:
        print("Обновления GitHub: Git не установлен; запускаю текущую версию.")
        return

    try:
        changes = git("status", "--porcelain", "--untracked-files=no")
        if changes.returncode != 0:
            print(f"Обновления GitHub: {error_text(changes)}; запускаю текущую версию.")
            return
        if changes.stdout.strip():
            print("Обновления GitHub: исходники изменены на этом компьютере; "
                  "автоматическое обновление пропущено.")
            return

        upstream = git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
        if upstream.returncode != 0:
            print("Обновления GitHub: для этой ветки не настроено отслеживание; "
                  "запускаю текущую версию.")
            return

        fetched = git("fetch", "--quiet")
        if fetched.returncode != 0:
            print(f"Обновления GitHub: {error_text(fetched)}; запускаю текущую версию.")
            return

        current = git("rev-parse", "HEAD")
        latest = git("rev-parse", "@{upstream}")
        if current.returncode != 0 or latest.returncode != 0:
            print("Обновления GitHub: не удалось проверить версии; запускаю текущую версию.")
            return
        if current.stdout.strip() == latest.stdout.strip():
            print("Обновления GitHub: установлена актуальная версия.")
            return

        ancestor = git("merge-base", "--is-ancestor", "HEAD", "@{upstream}")
        if ancestor.returncode != 0:
            print("Обновления GitHub: локальная ветка расходится с удалённой; "
                  "автоматическое обновление пропущено.")
            return

        try:
            backup = backup_database()
        except (OSError, sqlite3.Error) as exc:
            print(f"Обновления GitHub: не удалось сохранить копию базы: {exc}; "
                  "автоматическое обновление пропущено.")
            return
        if backup:
            print(f"Перед обновлением сохранена копия базы: {backup}")

        updated = git("merge", "--ff-only", "@{upstream}")
        if updated.returncode != 0:
            print(f"Обновления GitHub: {error_text(updated)}; запускаю текущую версию.")
            return
        print("Обновления GitHub: установлена новая версия кода.")
    except subprocess.TimeoutExpired:
        print("Обновления GitHub: истекло время ожидания; запускаю текущую версию.")
    except OSError as exc:
        print(f"Обновления GitHub: {exc}; запускаю текущую версию.")


if __name__ == "__main__":
    main()
