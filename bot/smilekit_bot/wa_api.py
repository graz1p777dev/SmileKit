"""HTTP-слой новой логики WhatsApp (пакет smilekit_bot/wa) для CRM.

CRM остаётся транспортом: принимает вебхук Green API, спрашивает здесь, что
ответить, сама отправляет и подтверждает факт отправки. Память диалога
лежит в отдельном файле рядом с основной базой бота и её не трогает.

Карточки заказа и передачи менеджеру возвращаются в CRM: она создаёт заказ,
шлёт их в рабочие группы и сообщает сюда, если доставить не удалось
(/internal/wa/card_failed) — тогда карточка повторяется по таймеру.
"""

import asyncio
import base64
import binascii
import logging
import threading
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from aiohttp import web

from .config import Settings
from .service import RECEIPT_CHECK_TIMEOUT_SECONDS, SmilekitAssistant
from .wa import rules as R
from .wa.brain import ImageCheck, OpenAIBrain
from .wa.engine import Engine, Incoming, Outgoing
from .wa.memory import Store

LOGGER = logging.getLogger(__name__)
MAX_IMAGE_BYTES = 20 * 1024 * 1024
# Вызывается раз в минуту — значит, не больше трёх напоминаний в минуту на весь магазин.
MAX_REMINDERS_PER_TICK = 3


class _Brain(OpenAIBrain):
    """Проверка чека сделана заранее в обработчике — здесь только отдаём результат."""

    def check_image(self, image: object) -> ImageCheck:
        return image if isinstance(image, ImageCheck) else ImageCheck(kind="unreadable")


class _Cards:
    def __init__(self) -> None:
        self.items: list[dict] = []

    def send_card(self, card: dict) -> bool:
        self.items.append(card)
        return True


class WaService:
    def __init__(self, settings: Settings, assistant: SmilekitAssistant) -> None:
        path = str(Path(settings.database_path).with_name("wa_engine.db"))
        self.store = Store(path)
        self.cards = _Cards()
        self.brain = _Brain(
            settings.openai_api_key.get_secret_value(), settings.openai_model,
            deepseek_api_key=settings.deepseek_api_key.get_secret_value(),
            deepseek_model=settings.deepseek_model, deepseek_base_url=settings.deepseek_base_url,
        )
        self.engine = Engine(self.store, self.brain, self.cards)
        self.assistant = assistant
        # Движок синхронный и ходит в модель — работает в отдельном потоке,
        # чтобы не останавливать Telegram-бота. Замок — один вызов за раз, и
        # ВСЯ работа с памятью (sqlite) идёт только под ним: чтение из цикла
        # событий одновременно с рабочим потоком ломало транзакции (инцидент 13.10).
        self.lock = threading.Lock()

    def _locked(self, fn, *args):
        with self.lock:
            self.cards.items = []
            result = fn(*args)
            return result, list(self.cards.items)

    async def run(self, fn, *args):
        """Возвращает (результат, карточки, появившиеся за этот вызов)."""
        return await asyncio.to_thread(self._locked, fn, *args)

    def _export(self, outs: list[Outgoing]) -> list[dict]:
        for out in outs:
            self.store.put_pending(out.id, out.chat_id, asdict(out))
        return [{"id": out.id, "chatId": out.chat_id, "kind": out.kind, "body": out.body, "after": out.after} for out in outs]

    def _pending(self, out_id: str, remove: bool) -> Outgoing | None:
        data = self.store.take_pending(out_id, remove)
        if data is None:
            return None
        data["effects"] = [tuple(effect) for effect in data["effects"]]
        return Outgoing(**data)

    # Ниже — цельные операции; каждая выполняется под замком через run().

    def handle_sync(self, chat_id: str, incoming: list[Incoming], has_history: bool, order_placed: bool) -> list[dict]:
        now = time.time()
        self.engine.seed(chat_id, has_history, order_placed, now)
        # Раз CRM позвала движок — бот в этом чате разрешён: снимаем отметку
        # «ведёт менеджер», если она осталась с прошлого раза.
        if self.store.load(chat_id).control.mode == "manager":
            self.engine.release_to_bot(chat_id, now)
        return self._export(self.engine.handle(chat_id, incoming, now))

    def can_send_sync(self, out_id: str) -> bool:
        out = self._pending(out_id, remove=False)
        return bool(out is not None and self.engine.can_send(out, time.time()))

    def delivered_sync(self, out_id: str, ok: bool, uncertain: bool) -> bool:
        out = self._pending(out_id, remove=True)
        if out is not None:
            self.engine.delivered(out, ok, time.time(), uncertain=uncertain)
        return out is not None

    def tick_chat_sync(self, chat_id: str) -> list[dict]:
        return self._export(self.engine.tick(chat_id, time.time()))

    def state_sync(self, chat_id: str) -> dict:
        memory = self.store.load(chat_id)
        journal = [
            {"time": R.bishkek_iso(ts), "agent": agent, "event": event, "detail": detail}
            for ts, agent, event, detail in self.store.journal(chat_id, 200)
        ]
        return {"memory": memory.model_dump(exclude={"seen_ids"}), "journal": journal}

    async def check_receipt(self, encoded: str, mime: str) -> ImageCheck:
        """Чек проверяет тот же vision-помощник, что и у рабочего бота. Любой
        сбой — «не удалось проверить», а не «это не чек»."""
        today = datetime.now(R.BISHKEK).strftime("%Y-%m-%d")
        kind = "pdf" if "pdf" in mime else "image"
        url = f"data:{mime};base64,{encoded}"
        try:
            if self.assistant.gemini_api_key:
                result = await asyncio.wait_for(
                    self.assistant._verify_receipt_gemini(url, today, kind), timeout=RECEIPT_CHECK_TIMEOUT_SECONDS
                )
            else:
                result = await asyncio.wait_for(
                    asyncio.to_thread(self.assistant._verify_receipt, url, today, kind),
                    timeout=RECEIPT_CHECK_TIMEOUT_SECONDS,
                )
        except Exception:
            LOGGER.exception("WA engine: receipt check failed")
            return ImageCheck(kind="unreadable")
        if not result.get("is_real_receipt_screenshot"):
            return ImageCheck(kind="other")
        return ImageCheck(
            kind="receipt", amount=result.get("detected_amount_kgs"),
            recipient_ok=bool(result.get("recipient_ok")), date_ok=bool(result.get("date_ok")),
        )


def _auth(request: web.Request) -> web.Response | None:
    from .api import _require_auth

    return _require_auth(request)


async def _body(request: web.Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        raise web.HTTPBadRequest(text="invalid_json")
    if not isinstance(body, dict) or not isinstance(body.get("chatId", ""), str):
        raise web.HTTPBadRequest(text="invalid_body")
    return body


async def handle(request: web.Request) -> web.Response:
    if (denied := _auth(request)) is not None:
        return denied
    body = await _body(request)
    service: WaService = request.app["wa"]
    incoming = []
    for raw in body.get("messages", []):
        item = Incoming(
            id=str(raw["id"]), kind=raw.get("kind", "text"), text=str(raw.get("text") or ""),
            quoted=str(raw.get("quoted") or ""),
        )
        if item.kind == "image":
            encoded = raw.get("imageBase64") or ""
            try:
                size = len(base64.b64decode(encoded, validate=True)) if encoded else 0
            except (ValueError, binascii.Error):
                size = 0
            item.image = (
                await service.check_receipt(encoded, raw.get("mime") or "image/jpeg")
                if 0 < size <= MAX_IMAGE_BYTES else ImageCheck(kind="unreadable")
            )
        incoming.append(item)
    seed = body.get("seed") if isinstance(body.get("seed"), dict) else {}
    outgoing, cards = await service.run(
        service.handle_sync, body["chatId"], incoming, bool(seed.get("hasHistory")), bool(seed.get("orderPlaced"))
    )
    return web.json_response({"outgoing": outgoing, "cards": cards})


async def can_send(request: web.Request) -> web.Response:
    if (denied := _auth(request)) is not None:
        return denied
    body = await _body(request)
    service: WaService = request.app["wa"]
    ok, _ = await service.run(service.can_send_sync, str(body.get("id")))
    return web.json_response({"ok": ok})


async def delivered(request: web.Request) -> web.Response:
    if (denied := _auth(request)) is not None:
        return denied
    body = await _body(request)
    service: WaService = request.app["wa"]
    known, _ = await service.run(
        service.delivered_sync, str(body.get("id")), bool(body.get("ok")), bool(body.get("uncertain"))
    )
    return web.json_response({"ok": known})


async def tick(request: web.Request) -> web.Response:
    """Напоминания по всем чатам, но не больше MAX_REMINDERS_PER_TICK за вызов:
    очередь, накопившаяся за ночь, расходится постепенно, а не залпом."""
    if (denied := _auth(request)) is not None:
        return denied
    service: WaService = request.app["wa"]
    outgoing, cards = [], []
    chat_ids, _ = await service.run(service.store.chat_ids)
    for chat_id in chat_ids:
        outs, new_cards = await service.run(service.tick_chat_sync, chat_id)
        outgoing += outs
        cards += new_cards
        if len(outgoing) >= MAX_REMINDERS_PER_TICK:
            break
    return web.json_response({"outgoing": outgoing, "cards": cards})


async def card_failed(request: web.Request) -> web.Response:
    if (denied := _auth(request)) is not None:
        return denied
    body = await _body(request)
    service: WaService = request.app["wa"]
    await service.run(service.engine.card_failed, body["chatId"], str(body.get("type")), time.time())
    return web.json_response({"ok": True})


async def control(request: web.Request) -> web.Response:
    if (denied := _auth(request)) is not None:
        return denied
    body = await _body(request)
    service: WaService = request.app["wa"]
    action = body.get("action")
    if action == "manager":
        await service.run(service.engine.operator_took_over, body["chatId"], time.time())
    elif action == "bot":
        await service.run(service.engine.release_to_bot, body["chatId"], time.time())
    elif action == "reset":
        await service.run(service.store.forget, body["chatId"])
    elif action == "stop_all_reminders":
        count, _ = await service.run(service.engine.stop_all_reminders, time.time())
        return web.json_response({"ok": True, "stopped": count})
    else:
        return web.json_response({"error": "action must be manager, bot, reset or stop_all_reminders"}, status=400)
    return web.json_response({"ok": True})


async def state(request: web.Request) -> web.Response:
    """Диагностика: память чата и журнал решений. Клиенту не показывается."""
    if (denied := _auth(request)) is not None:
        return denied
    service: WaService = request.app["wa"]
    data, _ = await service.run(service.state_sync, request.query.get("chatId", ""))
    return web.json_response(data)


def setup_wa(app: web.Application, settings: Settings, assistant: SmilekitAssistant) -> None:
    try:
        app["wa"] = WaService(settings, assistant)
    except Exception:
        # Новая логика не должна мешать запуску основного бота.
        LOGGER.exception("WA engine is not available — /internal/wa/* routes are off")
        return
    app.router.add_post("/internal/wa/handle", handle)
    app.router.add_post("/internal/wa/can_send", can_send)
    app.router.add_post("/internal/wa/delivered", delivered)
    app.router.add_post("/internal/wa/tick", tick)
    app.router.add_post("/internal/wa/card_failed", card_failed)
    app.router.add_post("/internal/wa/control", control)
    app.router.add_get("/internal/wa/state", state)
