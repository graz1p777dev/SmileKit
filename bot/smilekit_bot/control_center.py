"""Лёгкий клиент телеметрии Control Center («коннектор CRM»).

Контракт: /Users/alihantorebekov/Desktop/Projects/ADMINBOT/docs/CRM_CONNECTOR_CONTRACT.md
Три вызова:
  - send_heartbeat  — «я жив», раз в ~2 минуты (см. __main__.py).
  - record_usage    — расход LLM-провайдера, кладётся в батч и уходит раз в
                       минуту (флаш — фоновая задача, см. __main__.py).
  - record_event    — бизнес/ops-событие (сделка, заказ, ошибка), тот же батч
                       и тот же интервал.

Как и crm.py: «отправил и забыл» — сеть не должна ронять бота и не должна
задерживать ответ клиенту. Пустые CONTROL_CENTER_URL/CONTROL_CENTER_TOKEN —
коннектор полностью выключен (configure() выставляет enabled() в False),
никаких вызовов наружу. HTTP — тот же urllib.request в отдельном треде, что
и в crm.py (запрос блокирующий, событийный цикл не занимает).

Никогда не передаём содержимое сообщений, телефоны и имена — только цифры
и идентификаторы.
"""

import asyncio
import json
import logging
import threading
import urllib.error
import urllib.request


LOGGER = logging.getLogger(__name__)

HEARTBEAT_PATH = "/api/v1/connector/heartbeat"
USAGE_PATH = "/api/v1/connector/usage"
EVENTS_PATH = "/api/v1/connector/events"
REQUEST_TIMEOUT = 5.0
MAX_BATCH = 500
FLUSH_INTERVAL_SECONDS = 60

_base_url = ""
_token = ""
_enabled = False

_usage_batch: list[dict] = []
_event_batch: list[dict] = []
_lock = threading.Lock()


def configure(base_url: str, token: str) -> None:
    """Вызывается один раз при старте бота. Пустые значения — коннектор
    выключен, все остальные функции этого модуля становятся no-op."""
    global _base_url, _token, _enabled
    _base_url = (base_url or "").strip().rstrip("/")
    _token = (token or "").strip()
    _enabled = bool(_base_url and _token)
    with _lock:
        _usage_batch.clear()
        _event_batch.clear()


def enabled() -> bool:
    return _enabled


def _post(path: str, payload: dict) -> None:
    request = urllib.request.Request(
        f"{_base_url}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {_token}",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
        response.read()


async def send_heartbeat(
    *,
    status: str = "ok",
    version: str | None = None,
    git_sha: str | None = None,
    environment: str | None = None,
    deploy_source: str | None = None,
    uptime_seconds: float | None = None,
    details: dict | None = None,
    security: dict | None = None,
) -> None:
    """«Я жив». Никогда не бросает исключение наружу — недоступный Control
    Center не должен мешать боту работать."""
    if not _enabled:
        return
    payload: dict = {"status": status}
    if version is not None:
        payload["version"] = version
    if git_sha is not None:
        payload["gitSha"] = git_sha
    if environment is not None:
        payload["environment"] = environment
    if deploy_source is not None:
        payload["deploySource"] = deploy_source
    if uptime_seconds is not None:
        payload["uptimeSeconds"] = uptime_seconds
    if details is not None:
        payload["details"] = details
    if security is not None:
        payload["security"] = security
    try:
        await asyncio.to_thread(_post, HEARTBEAT_PATH, payload)
    except Exception:
        LOGGER.warning("Control Center heartbeat failed: %s", _base_url, exc_info=True)


def _append(batch: list[dict], entry: dict) -> None:
    with _lock:
        if len(batch) < MAX_BATCH:
            batch.append(entry)


def record_usage_sync(
    *,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float | None = None,
    metadata: dict | None = None,
) -> None:
    """Синхронная версия record_usage — для вызова из синхронных методов
    ассистента (service.py), которые выполняются в отдельном треде через
    asyncio.to_thread и не могут запланировать asyncio.create_task (там нет
    работающего event loop). Только потокобезопасное добавление в батч, без
    сети — сеть только в фоновой задаче раз в минуту (flush)."""
    if not _enabled:
        return
    entry: dict = {
        "provider": provider,
        "model": model,
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
    }
    if cost_usd is not None:
        entry["costUsd"] = cost_usd
    if metadata is not None:
        entry["metadata"] = metadata
    _append(_usage_batch, entry)


async def record_usage(
    *,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float | None = None,
    metadata: dict | None = None,
) -> None:
    """Асинхронная обёртка над record_usage_sync — для вызова из event loop
    (например crm.py/__main__.py). Не делает сетевых вызовов, поэтому просто
    await, без create_task: заблокировать событийный цикл нечем."""
    record_usage_sync(
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
        metadata=metadata,
    )


async def record_event(
    *,
    type: str,
    title: str,
    level: str | None = None,
    payload: dict | None = None,
) -> None:
    """Бизнес/ops-событие: type — "lead"|"deal"|"order"|"error". title и
    payload не должны содержать текст сообщений, телефоны или имена — только
    цифры и идентификаторы."""
    if not _enabled:
        return
    entry: dict = {"type": type, "title": title}
    if level is not None:
        entry["level"] = level
    if payload is not None:
        entry["payload"] = payload
    _append(_event_batch, entry)


async def _flush_one(path: str, batch: list[dict]) -> None:
    with _lock:
        if not batch:
            return
        items, batch[:] = list(batch), []
    try:
        await asyncio.to_thread(_post, path, {"events": items})
    except Exception:
        LOGGER.warning(
            "Control Center flush to %s failed, %d entries dropped", path, len(items)
        )


async def flush() -> None:
    """Отправляет оба батча (usage/events) и опустошает их. Вызывается
    фоновой задачей раз в минуту (см. __main__.py). Никогда не бросает
    исключение наружу."""
    if not _enabled:
        return
    await _flush_one(USAGE_PATH, _usage_batch)
    await _flush_one(EVENTS_PATH, _event_batch)
