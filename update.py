"""Обновление CRM перед запуском: Git-клон или публичный архив GitHub."""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "app.db"
BACKUP_DIR = ROOT / "backups"
REVISION_PATH = ROOT / ".crm_revision"
API_BASE = "https://api.github.com/repos/rshamrin/CRM-TechPres"
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024


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


def git_error(result: subprocess.CompletedProcess[str]) -> str:
    return (result.stderr or result.stdout or "неизвестная ошибка").strip()[:400]


def update_git_clone() -> None:
    if shutil.which("git") is None:
        print("Обновления: это Git-клон, но Git недоступен. Запускаю текущую версию.")
        return
    changes = git("status", "--porcelain", "--untracked-files=no")
    if changes.returncode != 0:
        print(f"Обновления: {git_error(changes)}; запускаю текущую версию.")
        return
    if changes.stdout.strip():
        print("Обновления: исходники изменены локально; обновление пропущено.")
        return
    upstream = git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    if upstream.returncode != 0:
        print("Обновления: ветка Git не отслеживает удалённую; запускаю текущую версию.")
        return
    fetched = git("fetch", "--quiet")
    if fetched.returncode != 0:
        print(f"Обновления: {git_error(fetched)}; запускаю текущую версию.")
        return
    current = git("rev-parse", "HEAD")
    latest = git("rev-parse", "@{upstream}")
    if current.returncode != 0 or latest.returncode != 0:
        print("Обновления: не удалось сравнить версии Git; запускаю текущую версию.")
        return
    if current.stdout.strip() == latest.stdout.strip():
        print("Обновления: установлена актуальная версия.")
        return
    if git("merge-base", "--is-ancestor", "HEAD", "@{upstream}").returncode != 0:
        print("Обновления: ветки Git разошлись; автоматическое обновление пропущено.")
        return
    try:
        backup = backup_database()
    except (OSError, sqlite3.Error) as exc:
        print(f"Обновления: копия базы не создана: {exc}; обновление пропущено.")
        return
    if backup:
        print(f"Копия базы перед обновлением: {backup}")
    merged = git("merge", "--ff-only", "@{upstream}")
    if merged.returncode != 0:
        print(f"Обновления: {git_error(merged)}; запускаю текущую версию.")
        return
    print("Обновления: новая версия кода установлена через Git.")


def get_bytes(url: str, limit: int) -> bytes:
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "CRM-TechPres-updater",
        },
    )
    with urlopen(request, timeout=20) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("загружаемый файл слишком большой")
    return data


def latest_commit() -> str:
    payload = json.loads(get_bytes(f"{API_BASE}/commits/main", 1024 * 1024))
    sha = payload.get("sha") if isinstance(payload, dict) else None
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("GitHub вернул некорректную версию")
    return sha


def allowed_path(path: PurePosixPath) -> bool:
    if path.as_posix() in {"README.md", "requirements.txt", "update.py"}:
        return True
    return (
        len(path.parts) >= 2
        and path.parts[0] == "app"
        and path.suffix in {".py", ".html", ".js", ".css"}
    )


def source_files(archive_bytes: bytes) -> dict[Path, bytes]:
    selected: dict[Path, bytes] = {}
    seen: set[str] = set()
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        entries = [info for info in archive.infolist() if not info.is_dir()]
        roots = {PurePosixPath(info.filename).parts[0] for info in entries}
        if len(roots) != 1:
            raise ValueError("неожиданная структура архива")
        root_name = next(iter(roots))
        for info in entries:
            path = PurePosixPath(info.filename)
            if path.is_absolute() or ".." in path.parts or path.parts[0] != root_name:
                raise ValueError("некорректный путь в архиве")
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ValueError("символическая ссылка в архиве")
            relative = PurePosixPath(*path.parts[1:])
            if not allowed_path(relative):
                continue
            name = relative.as_posix().casefold()
            if name in seen or info.file_size > MAX_DOWNLOAD_BYTES:
                raise ValueError("дублирующийся или слишком большой файл в архиве")
            seen.add(name)
            selected[Path(*relative.parts)] = archive.read(info)
    if Path("app/main.py") not in selected or Path("update.py") not in selected:
        raise ValueError("в архиве отсутствуют основные файлы CRM")
    return selected


def replace_files(files: dict[Path, bytes]) -> None:
    previous: dict[Path, bytes | None] = {}
    changed: list[Path] = []
    with tempfile.TemporaryDirectory(prefix=".crm_update_", dir=ROOT) as temp_name:
        temp = Path(temp_name)
        try:
            for index, (relative, content) in enumerate(sorted(files.items())):
                destination = ROOT / relative
                if destination.is_symlink() or any(
                    parent.is_symlink() for parent in destination.parents if parent != ROOT and ROOT in parent.parents
                ):
                    raise ValueError("символическая ссылка в каталоге приложения")
                previous[destination] = destination.read_bytes() if destination.exists() else None
                destination.parent.mkdir(parents=True, exist_ok=True)
                staged = temp / f"{index}.new"
                staged.write_bytes(content)
                os.replace(staged, destination)
                changed.append(destination)
        except Exception:
            for index, destination in enumerate(reversed(changed)):
                original = previous[destination]
                if original is None:
                    destination.unlink(missing_ok=True)
                else:
                    restore = temp / f"{index}.restore"
                    restore.write_bytes(original)
                    os.replace(restore, destination)
            raise


def update_public_archive() -> None:
    sha = latest_commit()
    installed = REVISION_PATH.read_text(encoding="ascii").strip() if REVISION_PATH.exists() else ""
    if installed == sha:
        print("Обновления: установлена актуальная версия.")
        return
    archive = get_bytes(f"{API_BASE}/zipball/{sha}", MAX_DOWNLOAD_BYTES)
    files = source_files(archive)
    backup = backup_database()
    if backup:
        print(f"Копия базы перед обновлением: {backup}")
    replace_files(files)
    temporary = REVISION_PATH.with_name(".crm_revision.new")
    temporary.write_text(sha + "\n", encoding="ascii")
    os.replace(temporary, REVISION_PATH)
    print("Обновления: новая версия кода установлена без Git.")


def main() -> None:
    try:
        if (ROOT / ".git").exists():
            update_git_clone()
        else:
            update_public_archive()
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError,
            sqlite3.Error, zipfile.BadZipFile, subprocess.TimeoutExpired) as exc:
        print(f"Обновления недоступны ({exc}); запускаю текущую версию.")


if __name__ == "__main__":
    main()
