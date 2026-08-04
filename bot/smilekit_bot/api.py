"""Внутренний HTTP-слой для CRM: настройки бота + лаборатория.

Работает в том же asyncio-процессе, что и long polling (см. __main__.py),
на отдельном порту (PORT, Railway его сам прокидывает). Ничего не меняет в
поведении polling-бота — только читает/пишет bot_settings в storage.py и
дёргает тот же SmilekitAssistant, что и реальный бот.

Аутентификация: заголовок x-internal-token сверяется с settings.bot_internal_token
(отдельный секрет от crm_internal_token — тот идёт в обратную сторону, бот -> CRM).
"""

import logging

from aiohttp import web

from .config import Settings
from .models import ChatMessage
from .prompts import MANAGER_PROMPT, SUPERVISOR_PROMPT
from .service import SmilekitAssistant
from .storage import BotStorage

LOGGER = logging.getLogger(__name__)

MANAGER_PROMPT_KEY = "manager_prompt"
SUPERVISOR_PROMPT_KEY = "supervisor_prompt"
MODEL_KEY = "model"

# Встроенный MANAGER_PROMPT сам по себе ~8550 символов — лимит должен быть
# заведомо больше дефолта, иначе «сохранить без правок» тихо обрежет промпт
# и живой бот получит урезанную инструкцию.
MAX_PROMPT_LENGTH = 12000


def allowed_models(settings: Settings) -> list[str]:
    """Небольшой фиксированный список моделей, доступных в настройках бота.

    Ничего не выдумываем: единственная модель, для которой заведомо известно,
    что она рабочая в этом окружении — та, что уже настроена в OPENAI_MODEL.
    Список можно расширить через BOT_ALLOWED_MODELS (через запятую), без
    редактирования кода.
    """
    extra = [item.strip() for item in settings.bot_allowed_models.split(",") if item.strip()]
    models = [settings.openai_model, *extra]
    seen: list[str] = []
    for model in models:
        if model not in seen:
            seen.append(model)
    return seen


def _truncate(value: str, limit: int = MAX_PROMPT_LENGTH) -> tuple[str, bool]:
    stripped = value.strip()
    if len(stripped) <= limit:
        return stripped, False
    return stripped[:limit], True


def effective_settings(storage: BotStorage, settings: Settings) -> dict:
    """Текущие действующие настройки: override из storage либо дефолт из prompts.py."""
    manager_override = storage.get_bot_setting(MANAGER_PROMPT_KEY)
    supervisor_override = storage.get_bot_setting(SUPERVISOR_PROMPT_KEY)
    model_override = storage.get_bot_setting(MODEL_KEY)
    models = allowed_models(settings)
    model = model_override if model_override in models else settings.openai_model
    manager_prompt, manager_was_truncated = _truncate(manager_override or MANAGER_PROMPT)
    supervisor_prompt, supervisor_was_truncated = _truncate(
        supervisor_override or SUPERVISOR_PROMPT
    )
    if manager_was_truncated or supervisor_was_truncated:
        LOGGER.warning(
            "Legacy oversized bot prompts were limited: manager=%s supervisor=%s",
            manager_was_truncated,
            supervisor_was_truncated,
        )
    return {
        "managerPrompt": manager_prompt,
        "managerPromptIsDefault": not bool(manager_override),
        "supervisorPrompt": supervisor_prompt,
        "supervisorPromptIsDefault": not bool(supervisor_override),
        "model": model,
        "models": models,
    }


def _require_auth(request: web.Request) -> web.Response | None:
    settings: Settings = request.app["settings"]
    expected = settings.bot_internal_token.get_secret_value()
    provided = request.headers.get("x-internal-token", "")
    if not expected or provided != expected:
        return web.json_response({"error": "forbidden"}, status=403)
    return None


async def health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def get_settings_handler(request: web.Request) -> web.Response:
    denied = _require_auth(request)
    if denied is not None:
        return denied
    storage: BotStorage = request.app["storage"]
    settings: Settings = request.app["settings"]
    return web.json_response(effective_settings(storage, settings))


async def put_settings_handler(request: web.Request) -> web.Response:
    denied = _require_auth(request)
    if denied is not None:
        return denied
    storage: BotStorage = request.app["storage"]
    settings: Settings = request.app["settings"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "invalid_body"}, status=400)

    truncated: list[str] = []

    if "managerPrompt" in body:
        value = body["managerPrompt"]
        if not isinstance(value, str):
            return web.json_response({"error": "managerPrompt must be a string"}, status=400)
        text, was_truncated = _truncate(value)
        if was_truncated:
            truncated.append("managerPrompt")
        storage.set_bot_setting(MANAGER_PROMPT_KEY, text)

    if "supervisorPrompt" in body:
        value = body["supervisorPrompt"]
        if not isinstance(value, str):
            return web.json_response({"error": "supervisorPrompt must be a string"}, status=400)
        text, was_truncated = _truncate(value)
        if was_truncated:
            truncated.append("supervisorPrompt")
        storage.set_bot_setting(SUPERVISOR_PROMPT_KEY, text)

    if "model" in body:
        value = body["model"]
        if not isinstance(value, str) or value not in allowed_models(settings):
            return web.json_response({"error": "unknown model"}, status=400)
        storage.set_bot_setting(MODEL_KEY, value)

    # Перечитываем из storage, а не эхо запроса — так CRM видит, что реально
    # записалось (тот же приём, что и в Мостовой saveSettings -> getSettings()).
    result = effective_settings(storage, settings)
    result["truncated"] = truncated
    return web.json_response(result)


async def post_lab_handler(request: web.Request) -> web.Response:
    denied = _require_auth(request)
    if denied is not None:
        return denied
    storage: BotStorage = request.app["storage"]
    settings: Settings = request.app["settings"]
    assistant: SmilekitAssistant = request.app["assistant"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid_json"}, status=400)
    if not isinstance(body, dict) or not isinstance(body.get("message"), str):
        return web.json_response({"error": "message is required"}, status=400)

    raw_history = body.get("history") or []
    if not isinstance(raw_history, list):
        return web.json_response({"error": "history must be a list"}, status=400)
    try:
        history = [ChatMessage(role=item["role"], content=item["content"]) for item in raw_history]
    except Exception:
        return web.json_response({"error": "invalid history item"}, status=400)

    current = effective_settings(storage, settings)
    # Изолировано: своя in-memory история из запроса, без storage/crm/telegram —
    # ни одна из persistent-таблиц бота (messages/customer_memory/orders/
    # sent_events) здесь не трогается.
    reply = await assistant.reply(
        body["message"],
        history,
        manager_prompt=current["managerPrompt"],
        supervisor_prompt=current["supervisorPrompt"],
        model=current["model"],
    )
    return web.json_response(
        {
            "reply": reply.text,
            "supervisor": reply.supervisor.model_dump(),
        }
    )


@web.middleware
async def error_middleware(request: web.Request, handler):
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except Exception:
        LOGGER.exception("Unhandled error in %s %s", request.method, request.path)
        return web.json_response({"error": "internal_error"}, status=500)


def create_app(settings: Settings, storage: BotStorage, assistant: SmilekitAssistant) -> web.Application:
    app = web.Application(middlewares=[error_middleware])
    app["settings"] = settings
    app["storage"] = storage
    app["assistant"] = assistant
    app.router.add_get("/internal/health", health)
    app.router.add_get("/internal/settings", get_settings_handler)
    app.router.add_put("/internal/settings", put_settings_handler)
    app.router.add_post("/internal/lab", post_lab_handler)
    return app
