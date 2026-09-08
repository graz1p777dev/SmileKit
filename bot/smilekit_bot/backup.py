"""Резервные копии SQLite (settings.database_path на Railway-volume).

Снимок — через sqlite3.Connection.backup() (онлайн-безопасно, WAL не мешает),
проверка PRAGMA integrity_check, gzip в <dirname(db)>/backups/<YYYY-MM-DD-HHmm>.db.gz
(метка времени — UTC).
Если заданы BACKUP_S3_* — тот же файл уходит в S3-совместимое хранилище
(Cloudflare R2 / Railway bucket) с ключом smilekit-bot/<имя файла>; без них
загрузка — no-op (локальная копия всё равно остаётся). Копии старше
RETENTION_DAYS удаляются и локально, и в бакете.

Расписание — фоновая задача run_periodic_backups в __main__.py (раз в 6 часов,
первая через 5 минут после старта). Вручную:
  python -m smilekit_bot.backup                     # снять копию
  python -m smilekit_bot.backup restore <file.db.gz> # восстановить (бот остановить!)

boto3 импортируется лениво внутри загрузки: без него бот стартует и локальные
копии снимаются как обычно.
"""

import argparse
import asyncio
import gzip
import logging
import os
import shutil
import sqlite3
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import control_center
from .config import Settings


LOGGER = logging.getLogger(__name__)

TIMESTAMP_FORMAT = "%Y-%m-%d-%H%M"
BACKUP_SUFFIX = ".db.gz"
LOCAL_BACKUP_DIRNAME = "backups"
S3_KEY_PREFIX = "smilekit-bot/"
RETENTION_DAYS = 30
BACKUP_INTERVAL_SECONDS = 6 * 60 * 60
FIRST_BACKUP_DELAY_SECONDS = 5 * 60

_upload_disabled_logged = False


@dataclass
class BackupResult:
    path: Path
    size_bytes: int
    s3_key: str | None = None
    pruned_local: list[Path] = field(default_factory=list)
    pruned_remote: list[str] = field(default_factory=list)


def _timestamp(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime(TIMESTAMP_FORMAT)


def _integrity_check(db_path: Path) -> str:
    connection = sqlite3.connect(db_path)
    try:
        row = connection.execute("PRAGMA integrity_check").fetchone()
        return str(row[0]) if row else "no result"
    finally:
        connection.close()


def create_backup(db_path: str | Path, dest_dir: str | Path, *, now: datetime | None = None) -> Path:
    """Снимок db_path -> <dest_dir>/<YYYY-MM-DD-HHmm>.db.gz.

    Онлайн-копия через Connection.backup() во временный файл, затем
    PRAGMA integrity_check (не «ok» — исключение, файл не пишется), затем gzip.
    """
    db_path = Path(db_path)
    dest_dir = Path(dest_dir)
    if not db_path.exists():
        raise FileNotFoundError(f"database not found: {db_path}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / f"{_timestamp(now)}{BACKUP_SUFFIX}"
    with tempfile.TemporaryDirectory(prefix="smilekit-backup-") as tmp:
        snapshot = Path(tmp) / "snapshot.db"
        source = sqlite3.connect(db_path)
        try:
            destination = sqlite3.connect(snapshot)
            try:
                source.backup(destination)
            finally:
                destination.close()
        finally:
            source.close()
        result = _integrity_check(snapshot)
        if result != "ok":
            raise RuntimeError(f"backup snapshot failed integrity_check: {result}")
        partial = target.with_name(target.name + ".part")
        with open(snapshot, "rb") as raw, gzip.open(partial, "wb") as packed:
            shutil.copyfileobj(raw, packed)
        os.replace(partial, target)
    LOGGER.info("SQLite backup written: %s (%d bytes)", target, target.stat().st_size)
    return target


def s3_configured(settings: Settings) -> bool:
    return bool(
        settings.backup_s3_endpoint
        and settings.backup_s3_bucket
        and settings.backup_s3_access_key_id
        and settings.backup_s3_secret_access_key.get_secret_value()
    )


def _s3_client(settings: Settings):
    import boto3  # лениво: без boto3 бот стартует, локальные копии работают

    return boto3.client(
        "s3",
        endpoint_url=settings.backup_s3_endpoint,
        aws_access_key_id=settings.backup_s3_access_key_id,
        aws_secret_access_key=settings.backup_s3_secret_access_key.get_secret_value(),
        region_name=settings.backup_s3_region,
    )


def upload_backup(path: Path, settings: Settings) -> str | None:
    """Кладёт файл в бакет под ключом smilekit-bot/<имя>. Без BACKUP_S3_* —
    no-op (пишет в лог один раз за процесс), возвращает None."""
    global _upload_disabled_logged
    if not s3_configured(settings):
        if not _upload_disabled_logged:
            LOGGER.info("Backup upload disabled: BACKUP_S3_* not set, keeping local copies only")
            _upload_disabled_logged = True
        return None
    key = f"{S3_KEY_PREFIX}{path.name}"
    _s3_client(settings).upload_file(str(path), settings.backup_s3_bucket, key)
    LOGGER.info("SQLite backup uploaded: s3://%s/%s", settings.backup_s3_bucket, key)
    return key


def _parse_backup_timestamp(name: str) -> datetime | None:
    if not name.endswith(BACKUP_SUFFIX):
        return None
    try:
        return datetime.strptime(name[: -len(BACKUP_SUFFIX)], TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def prune_local(dest_dir: str | Path, *, now: datetime | None = None, days: int = RETENTION_DAYS) -> list[Path]:
    """Удаляет локальные копии старше days (по метке времени в имени файла)."""
    dest_dir = Path(dest_dir)
    if not dest_dir.is_dir():
        return []
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=days)
    removed = []
    for path in sorted(dest_dir.iterdir()):
        stamp = _parse_backup_timestamp(path.name)
        if stamp is not None and stamp < cutoff:
            path.unlink()
            removed.append(path)
    if removed:
        LOGGER.info("Pruned %d local backups older than %d days", len(removed), days)
    return removed


def prune_remote(settings: Settings, *, now: datetime | None = None, days: int = RETENTION_DAYS) -> list[str]:
    """Удаляет объекты smilekit-bot/* старше days по LastModified. Без
    BACKUP_S3_* — no-op."""
    if not s3_configured(settings):
        return []
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=days)
    client = _s3_client(settings)
    removed = []
    token = None
    while True:
        kwargs = {"Bucket": settings.backup_s3_bucket, "Prefix": S3_KEY_PREFIX}
        if token:
            kwargs["ContinuationToken"] = token
        page = client.list_objects_v2(**kwargs)
        for item in page.get("Contents", []) or []:
            modified = item["LastModified"]
            if modified.tzinfo is None:
                modified = modified.replace(tzinfo=timezone.utc)
            if modified < cutoff:
                client.delete_object(Bucket=settings.backup_s3_bucket, Key=item["Key"])
                removed.append(item["Key"])
        if not page.get("IsTruncated"):
            break
        token = page.get("NextContinuationToken")
    if removed:
        LOGGER.info("Pruned %d remote backups older than %d days", len(removed), days)
    return removed


def local_backup_dir(db_path: str | Path) -> Path:
    return Path(db_path).parent / LOCAL_BACKUP_DIRNAME


def run_backup(settings: Settings, *, now: datetime | None = None) -> BackupResult:
    """Полный цикл: локальная копия -> загрузка в бакет (если настроен) ->
    ретеншн локально и в бакете. Синхронный, вызывается через to_thread."""
    dest_dir = local_backup_dir(settings.database_path)
    path = create_backup(settings.database_path, dest_dir, now=now)
    result = BackupResult(path=path, size_bytes=path.stat().st_size)
    result.s3_key = upload_backup(path, settings)
    result.pruned_local = prune_local(dest_dir, now=now)
    result.pruned_remote = prune_remote(settings, now=now)
    return result


def restore_backup(gz_path: str | Path, db_path: str | Path) -> Path:
    """Распаковывает копию, проверяет integrity_check и атомарно подменяет
    db_path (прежний файл остаётся рядом как <db>.bak). Бот в этот момент
    должен быть остановлен: сайдкары -wal/-shm прежней базы вычищаются,
    иначе SQLite применил бы старый WAL к восстановленному файлу."""
    gz_path = Path(gz_path)
    db_path = Path(db_path)
    if not gz_path.exists():
        raise FileNotFoundError(f"backup not found: {gz_path}")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # Временный файл в той же папке, что и база — os.replace атомарен только
    # внутри одной файловой системы.
    with tempfile.TemporaryDirectory(prefix="smilekit-restore-", dir=db_path.parent) as tmp:
        restored = Path(tmp) / "restored.db"
        with gzip.open(gz_path, "rb") as packed, open(restored, "wb") as raw:
            shutil.copyfileobj(packed, raw)
        result = _integrity_check(restored)
        if result != "ok":
            raise RuntimeError(f"backup {gz_path} failed integrity_check: {result}")
        backup_of_previous = db_path.with_name(db_path.name + ".bak")
        if db_path.exists():
            # Сначала докатываем WAL прежней базы в её основной файл, чтобы
            # .bak был полным, потом убираем сайдкары.
            previous = sqlite3.connect(db_path)
            try:
                previous.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                previous.close()
            os.replace(db_path, backup_of_previous)
        for sidecar in (db_path.with_name(db_path.name + "-wal"), db_path.with_name(db_path.name + "-shm")):
            if sidecar.exists():
                sidecar.unlink()
        os.replace(restored, db_path)
    LOGGER.info("SQLite database restored from %s into %s", gz_path, db_path)
    return db_path


async def backup_and_report(settings: Settings) -> BackupResult | None:
    """Снимает копию (в отдельном треде) и шлёт событие «backup» в Control
    Center — только размеры/пути/время, без PII. Никогда не бросает наружу."""
    started = time.time()
    try:
        result = await asyncio.to_thread(run_backup, settings)
    except Exception as exc:
        LOGGER.exception("SQLite backup failed")
        await control_center.record_event(
            type="backup",
            level="error",
            title="SQLite backup failed",
            payload={
                "dbPath": str(settings.database_path),
                "error": f"{type(exc).__name__}: {exc}"[:300],
                "durationSeconds": round(time.time() - started, 3),
            },
        )
        return None
    await control_center.record_event(
        type="backup",
        level="info",
        title="SQLite backup created",
        payload={
            "file": str(result.path),
            "sizeBytes": result.size_bytes,
            "s3Key": result.s3_key,
            "prunedLocal": len(result.pruned_local),
            "prunedRemote": len(result.pruned_remote),
            "durationSeconds": round(time.time() - started, 3),
        },
    )
    return result


async def _cli_backup(settings: Settings) -> int:
    result = await backup_and_report(settings)
    # Процесс CLI сразу завершается — фонового флаша нет, отправляем сами.
    await control_center.flush()
    if result is None:
        return 1
    print(result.path)
    if result.s3_key:
        print(f"uploaded: s3://{settings.backup_s3_bucket}/{result.s3_key}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m smilekit_bot.backup", description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("backup", help="снять копию (по умолчанию)")
    restore = subparsers.add_parser("restore", help="восстановить базу из <file.db.gz>")
    restore.add_argument("gz_path")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings()
    if args.command == "restore":
        restore_backup(args.gz_path, settings.database_path)
        print(settings.database_path)
        return 0
    control_center.configure(settings.control_center_url, settings.control_center_token.get_secret_value())
    return asyncio.run(_cli_backup(settings))


if __name__ == "__main__":
    raise SystemExit(main())
