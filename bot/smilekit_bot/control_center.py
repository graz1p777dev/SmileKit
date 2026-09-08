"""Лёгкий клиент телеметрии Control Center («коннектор CRM»).

Контракт: /Users/alihantorebekov/Desktop/Projects/ADMINBOT/docs/CRM_CONNECTOR_CONTRACT.md
Четыре вызова:
  - send_heartbeat  — «я жив», раз в ~2 минуты (см. __main__.py).
  - record_usage    — расход LLM-провайдера, кладётся в батч и уходит раз в
                       минуту (флаш — фоновая задача, см. __main__.py).
  - record_event    — бизнес/ops-событие (сделка, заказ, ошибка), тот же батч
                       и тот же интервал.
  - fetch_prompt    — системный промпт менеджера из Prompt Studio (GET с
                       If-None-Match по versionId, тот же цикл, что heartbeat).
                       Последняя удачно загруженная версия живёт в памяти
                       модуля; current_prompt() отдаёт её вместо локального
                       MANAGER_PROMPT, а heartbeat подтверждает versionId (ACK).

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
PROMPT_PATH = "/api/v1/connector/prompt"
REQUEST_TIMEOUT = 5.0
MAX_BATCH = 500
FLUSH_INTERVAL_SECONDS = 60

_base_url = ""
_token = ""
_enabled = False

_usage_batch: list[dict] = []
_event_batch: list[dict] = []
_lock = threading.Lock()

# Последний удачно загруженный промпт из Prompt Studio. None — ещё не
# загружали / промпт не опубликован (404) — работаем на локальном.
_prompt_compiled: str | None = None
_prompt_version_id: str | None = None


def configure(base_url: str, token: str) -> None:
    """Вызывается один раз при старте бота. Пустые значения — коннектор
    выключен, все остальные функции этого модуля становятся no-op."""
    global _base_url, _token, _enabled, _prompt_compiled, _prompt_version_id
    _base_url = (base_url or "").strip().rstrip("/")
    _token = (token or "").strip()
    _enabled = bool(_base_url and _token)
    _prompt_compiled = None
    _prompt_version_id = None
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


def _get_prompt(known_version_id: str | None) -> tuple[str, str] | None:
    """GET /connector/prompt. Возвращает (compiled, versionId) на 200,
    None на 304 (не изменился) и 404 (промпт не опубликован); остальные
    HTTP-ошибки и сетевые сбои — исключение, разбирает fetch_prompt."""
    headers = {"Authorization": f"Bearer {_token}"}
    if known_version_id:
        headers["If-None-Match"] = f'"{known_version_id}"'
    request = urllib.request.Request(f"{_base_url}{PROMPT_PATH}", headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        if error.code in (304, 404):
            return None
        raise
    compiled = body.get("compiled")
    version_id = body.get("versionId")
    if not isinstance(compiled, str) or not compiled.strip() or not version_id:
        raise ValueError("Control Center prompt response lacks compiled/versionId")
    return compiled, str(version_id)


async def fetch_prompt() -> str | None:
    """Опрос Prompt Studio (раз в ~2 минуты, см. __main__.py). Возвращает
    versionId только что загруженной новой версии, иначе None: 304/404,
    сеть, невалидный ответ — остаёмся на последней удачной версии (или на
    локальном промпте). Никогда не бросает наружу."""
    global _prompt_compiled, _prompt_version_id
    if not _enabled:
        return None
    try:
        result = await asyncio.to_thread(_get_prompt, _prompt_version_id)
    except Exception:
        LOGGER.warning("Control Center prompt fetch failed: %s", _base_url, exc_info=True)
        return None
    if result is None:
        return None
    compiled, version_id = result
    if version_id == _prompt_version_id:
        return None
    _prompt_compiled, _prompt_version_id = compiled, version_id
    LOGGER.info("Control Center prompt loaded: versionId=%s chars=%d", version_id, len(compiled))
    return version_id


def current_prompt_version_id() -> str | None:
    """versionId промпта, на котором реально работает бот (для ACK в
    heartbeat); None — работаем на локальном MANAGER_PROMPT."""
    return _prompt_version_id


def current_prompt(local_fallback: str) -> str:
    """Системный промпт менеджера: загруженный из Prompt Studio, иначе
    локальный. При выключенном коннекторе всегда локальный."""
    if _enabled and _prompt_compiled is not None:
        return _prompt_compiled
    return local_fallback


async def send_heartbeat(
    *,
    status: str = "ok",
    version: str | None = None,
    git_sha: str | None = None,
    environment: str | None = None,
    deploy_source: str | None = None,
    prompt_version_id: str | None = None,
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
    if prompt_version_id is not None:
        payload["promptVersionId"] = prompt_version_id
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
