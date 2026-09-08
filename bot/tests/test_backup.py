import gzip
import sqlite3
import sys
import types
from datetime import datetime, timedelta, timezone

import pytest

from smilekit_bot import backup, control_center
from smilekit_bot.config import Settings
from smilekit_bot.storage import BotStorage


NOW = datetime(2026, 9, 8, 12, 30, tzinfo=timezone.utc)


def make_settings(tmp_path, **overrides) -> Settings:
    values = {
        "telegram_bot_token": "tg-token",
        "openai_api_key": "oa-key",
        "database_path": str(tmp_path / "data" / "smilekit.db"),
        "backup_s3_endpoint": "",
        "backup_s3_bucket": "",
        "backup_s3_access_key_id": "",
        "backup_s3_secret_access_key": "",
    }
    values.update(overrides)
    return Settings(**values)


def make_db(path) -> None:
    """Живая база через BotStorage (WAL) с парой записей."""
    storage = BotStorage(str(path))
    storage.set_bot_setting("manager_prompt", "проверочный промпт")
    storage.add_exchange(1, 10, "привет", "здравствуйте", "text")
    storage.close()


def read_setting(path) -> str | None:
    connection = sqlite3.connect(path)
    try:
        row = connection.execute("SELECT value FROM bot_settings WHERE key = 'manager_prompt'").fetchone()
        return row[0] if row else None
    finally:
        connection.close()


class FakeS3Client:
    def __init__(self, objects=None):
        self.uploads: list[tuple[str, str, str]] = []
        self.deleted: list[str] = []
        self.objects = objects or []

    def upload_file(self, filename, bucket, key):
        self.uploads.append((filename, bucket, key))

    def list_objects_v2(self, **kwargs):
        assert kwargs["Prefix"] == backup.S3_KEY_PREFIX
        return {"Contents": list(self.objects), "IsTruncated": False}

    def delete_object(self, Bucket, Key):
        self.deleted.append(Key)


def install_fake_boto3(monkeypatch, client=None):
    """Подменяет boto3 модулем-заглушкой: client=None означает «boto3.client
    вызывать нельзя» (проверка no-op без BACKUP_S3_*)."""
    module = types.ModuleType("boto3")
    module.calls = []

    def make_client(service, **kwargs):
        module.calls.append((service, kwargs))
        if client is None:
            raise AssertionError("boto3.client must not be constructed without BACKUP_S3_*")
        return client

    module.client = make_client
    monkeypatch.setitem(sys.modules, "boto3", module)
    return module


@pytest.fixture(autouse=True)
def _reset_control_center():
    control_center.configure("", "")
    yield
    control_center.configure("", "")


# ─── create_backup / restore_backup ────────────────────────────────────────


def test_create_backup_produces_valid_gz_that_restores(tmp_path):
    db_path = tmp_path / "data" / "smilekit.db"
    make_db(db_path)

    target = backup.create_backup(db_path, tmp_path / "out", now=NOW)

    assert target == tmp_path / "out" / "2026-09-08-1230.db.gz"
    assert target.exists()
    assert not list((tmp_path / "out").glob("*.part"))
    # Живая база не тронута.
    assert read_setting(db_path) == "проверочный промпт"

    raw = tmp_path / "unpacked.db"
    with gzip.open(target, "rb") as packed:
        raw.write_bytes(packed.read())
    assert backup._integrity_check(raw) == "ok"
    assert read_setting(raw) == "проверочный промпт"

    restored_path = tmp_path / "restore" / "smilekit.db"
    assert backup.restore_backup(target, restored_path) == restored_path
    assert backup._integrity_check(restored_path) == "ok"
    assert read_setting(restored_path) == "проверочный промпт"


def test_restore_replaces_existing_db_keeps_bak_and_drops_wal_sidecars(tmp_path):
    db_path = tmp_path / "data" / "smilekit.db"
    make_db(db_path)
    target = backup.create_backup(db_path, tmp_path / "out", now=NOW)

    # База «ушла вперёд» и в WAL-режиме имеет сайдкары.
    storage = BotStorage(str(db_path))
    storage.set_bot_setting("manager_prompt", "новее копии")
    (tmp_path / "data" / "smilekit.db-wal").touch()
    (tmp_path / "data" / "smilekit.db-shm").touch()
    storage.close()

    backup.restore_backup(target, db_path)

    assert read_setting(db_path) == "проверочный промпт"
    assert read_setting(tmp_path / "data" / "smilekit.db.bak") == "новее копии"
    assert not (tmp_path / "data" / "smilekit.db-wal").exists()
    assert not (tmp_path / "data" / "smilekit.db-shm").exists()


def test_create_backup_fails_loudly_on_integrity_error(tmp_path, monkeypatch):
    db_path = tmp_path / "data" / "smilekit.db"
    make_db(db_path)
    monkeypatch.setattr(backup, "_integrity_check", lambda path: "*** in database main ***")

    with pytest.raises(RuntimeError, match="integrity_check"):
        backup.create_backup(db_path, tmp_path / "out", now=NOW)
    assert not list((tmp_path / "out").iterdir())


def test_restore_rejects_corrupt_archive_and_keeps_current_db(tmp_path):
    db_path = tmp_path / "data" / "smilekit.db"
    make_db(db_path)
    bad = tmp_path / "bad.db.gz"
    with gzip.open(bad, "wb") as packed:
        packed.write(b"not a sqlite file at all")

    with pytest.raises((RuntimeError, sqlite3.DatabaseError)):
        backup.restore_backup(bad, db_path)
    assert read_setting(db_path) == "проверочный промпт"
    assert not (tmp_path / "data" / "smilekit.db.bak").exists()


def test_create_backup_requires_existing_db(tmp_path):
    with pytest.raises(FileNotFoundError):
        backup.create_backup(tmp_path / "missing.db", tmp_path / "out")


# ─── Ретеншн ────────────────────────────────────────────────────────────────


def test_prune_local_deletes_only_backups_older_than_30_days(tmp_path):
    out = tmp_path / "backups"
    out.mkdir()
    old = out / (NOW - timedelta(days=31)).strftime("%Y-%m-%d-%H%M.db.gz")
    fresh = out / (NOW - timedelta(days=29)).strftime("%Y-%m-%d-%H%M.db.gz")
    unrelated = out / "notes.txt"
    for path in (old, fresh, unrelated):
        path.write_bytes(b"x")

    removed = backup.prune_local(out, now=NOW)

    assert removed == [old]
    assert not old.exists()
    assert fresh.exists()
    assert unrelated.exists()


def test_prune_remote_deletes_by_last_modified(tmp_path, monkeypatch):
    client = FakeS3Client(objects=[
        {"Key": "smilekit-bot/old.db.gz", "LastModified": NOW - timedelta(days=40)},
        {"Key": "smilekit-bot/fresh.db.gz", "LastModified": NOW - timedelta(days=2)},
    ])
    install_fake_boto3(monkeypatch, client)
    settings = make_settings(
        tmp_path,
        backup_s3_endpoint="https://r2.example",
        backup_s3_bucket="bucket",
        backup_s3_access_key_id="id",
        backup_s3_secret_access_key="secret",
    )

    assert backup.prune_remote(settings, now=NOW) == ["smilekit-bot/old.db.gz"]
    assert client.deleted == ["smilekit-bot/old.db.gz"]


# ─── Загрузка в S3 ──────────────────────────────────────────────────────────


def test_run_backup_without_s3_env_never_constructs_boto3_client(tmp_path, monkeypatch):
    boto3_stub = install_fake_boto3(monkeypatch, client=None)
    settings = make_settings(tmp_path)
    make_db(settings.database_path)

    result = backup.run_backup(settings, now=NOW)

    assert result.path == tmp_path / "data" / "backups" / "2026-09-08-1230.db.gz"
    assert result.path.exists()
    assert result.size_bytes == result.path.stat().st_size
    assert result.s3_key is None
    assert result.pruned_remote == []
    assert boto3_stub.calls == []


def test_run_backup_without_boto3_installed_still_keeps_local_copy(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "boto3", None)  # import boto3 -> ImportError
    settings = make_settings(tmp_path)
    make_db(settings.database_path)

    result = backup.run_backup(settings, now=NOW)
    assert result.path.exists()
    assert result.s3_key is None


def test_run_backup_with_s3_env_uploads_under_prefix(tmp_path, monkeypatch):
    client = FakeS3Client()
    boto3_stub = install_fake_boto3(monkeypatch, client)
    settings = make_settings(
        tmp_path,
        backup_s3_endpoint="https://r2.example",
        backup_s3_bucket="bucket",
        backup_s3_access_key_id="id",
        backup_s3_secret_access_key="secret",
        backup_s3_region="auto",
    )
    make_db(settings.database_path)

    result = backup.run_backup(settings, now=NOW)

    assert result.s3_key == "smilekit-bot/2026-09-08-1230.db.gz"
    assert client.uploads == [(str(result.path), "bucket", "smilekit-bot/2026-09-08-1230.db.gz")]
    service, kwargs = boto3_stub.calls[0]
    assert service == "s3"
    assert kwargs == {
        "endpoint_url": "https://r2.example",
        "aws_access_key_id": "id",
        "aws_secret_access_key": "secret",
        "region_name": "auto",
    }


# ─── Событие в Control Center ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_backup_and_report_records_backup_event(tmp_path, monkeypatch):
    install_fake_boto3(monkeypatch, client=None)
    control_center.configure("https://cc.example", "secret")
    settings = make_settings(tmp_path)
    make_db(settings.database_path)

    result = await backup.backup_and_report(settings)

    assert result is not None
    assert len(control_center._event_batch) == 1
    event = control_center._event_batch[0]
    assert event["type"] == "backup"
    assert event["level"] == "info"
    assert event["payload"]["file"] == str(result.path)
    assert event["payload"]["sizeBytes"] == result.size_bytes
    assert event["payload"]["s3Key"] is None


@pytest.mark.asyncio
async def test_backup_and_report_failure_records_error_event_and_does_not_raise(tmp_path):
    control_center.configure("https://cc.example", "secret")
    settings = make_settings(tmp_path)  # базы нет -> FileNotFoundError внутри

    assert await backup.backup_and_report(settings) is None
    event = control_center._event_batch[0]
    assert event["type"] == "backup"
    assert event["level"] == "error"
    assert "FileNotFoundError" in event["payload"]["error"]


@pytest.mark.asyncio
async def test_backup_and_report_disabled_connector_records_nothing(tmp_path, monkeypatch):
    install_fake_boto3(monkeypatch, client=None)
    settings = make_settings(tmp_path)
    make_db(settings.database_path)

    assert await backup.backup_and_report(settings) is not None
    assert control_center._event_batch == []
