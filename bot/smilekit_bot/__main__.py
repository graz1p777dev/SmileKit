import asyncio
import base64
import logging
import random
import time
from collections import defaultdict
from io import BytesIO

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatAction
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from aiohttp import web

from .api import create_app, effective_settings
from .config import Settings
from .crm import (
    REQUEST_TIMEOUT,
    CrmClient,
    build_deal_payload,
    build_order_payload,
    stage_action_for_inbound,
)
from . import templates
from .media import result_photo_group, should_send_results_after_city, wants_result_photos
from .outgoing import split_outgoing_messages
from .service import SmilekitAssistant
from .storage import BotStorage


PHOTO_PROMPT = (
    "Клиент прислал фото. Ответь на его подпись и опиши только действительно "
    "видимые детали, относящиеся к улыбке или применению продукта. Не ставь "
    "диагноз и не делай выводов о здоровье зубов по изображению."
)
RATE_LIMIT_TEXT = (
    "Сообщений пришло слишком много за короткое время. "
    "Подождите, пожалуйста, минуту и отправьте вопрос ещё раз."
)
# template_id из assistant.route() -> готовый текст.
ROUTED_TEMPLATES = {
    "effect_duration": templates.EFFECT_DURATION,
    "safety_composition": templates.SAFETY_COMPOSITION,
}
# Клиент часто пишет мыслями через несколько сообщений подряд (видно на
# живых чатах). Без этого окна каждое такое сообщение уходило в отдельный
# независимый вызов AI, и клиенту прилетало по несколько ответов подряд.
DEBOUNCE_SECONDS = 3.0
# Клиент замолчал после ответа бота — не бросаем диалог: раз бот уже что-то
# показал (эффект, цену), полчаса тишины — разумный порог "перестал отвечать".
IDLE_NUDGE_DELAY_SECONDS = 30 * 60
# Ежедневное напоминание про скидку — время дня якорится на первый контакт
# (написал днём — рассылка днём). Останавливается, как только клиент
# оформил заказ; ограничено, чтобы не превратиться в бесконечный спам.
DAILY_NUDGE_INTERVAL_SECONDS = 24 * 60 * 60
DAILY_NUDGE_MAX_SENDS = 7
DAILY_NUDGE_CHECK_INTERVAL_SECONDS = 15 * 60


async def main() -> None:
    settings = Settings()
    bot = Bot(token=settings.telegram_bot_token.get_secret_value())
    dp = Dispatcher()
    assistant = SmilekitAssistant(
        api_key=settings.openai_api_key.get_secret_value(),
        model=settings.openai_model,
    )
    storage = BotStorage(settings.database_path)
    crm = CrmClient(
        settings.crm_base_url,
        settings.crm_internal_token.get_secret_value(),
    )
    if not crm.enabled:
        logging.info("CRM order push disabled: CRM_BASE_URL/CRM_INTERNAL_TOKEN not set")
    locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
    follow_up_tasks: set[asyncio.Task] = set()
    crm_tasks: set[asyncio.Task] = set()
    pending_texts: dict[int, list[str]] = defaultdict(list)
    debounce_tasks: dict[int, asyncio.Task] = {}

    async def follow_up_after_results(chat_id: int, message_id: int) -> None:
        await asyncio.sleep(3 * 60)
        if storage.has_message_after(chat_id, message_id):
            return
        if not storage.claim_event(chat_id, "results_follow_up"):
            return
        await bot.send_chat_action(chat_id, ChatAction.TYPING)
        await asyncio.sleep(random.uniform(0.8, 1.3))
        await bot.send_message(
            chat_id,
            "Если хотите, могу отправить ещё отзывы наших клиентов 😊",
        )

    async def idle_nudge(chat_id: int, message_id: int) -> None:
        """Клиент замолчал после ответа бота — напоминаем об эффекте один раз
        на этот простой. claim_event по message_id, а не по chat_id: после
        следующего сообщения клиента новый простой сможет напомнить снова."""
        await asyncio.sleep(IDLE_NUDGE_DELAY_SECONDS)
        if storage.has_message_after(chat_id, message_id):
            return
        if not storage.claim_event(chat_id, f"idle_nudge:{message_id}"):
            return
        await bot.send_chat_action(chat_id, ChatAction.TYPING)
        await asyncio.sleep(random.uniform(0.8, 1.3))
        await bot.send_message(chat_id, templates.FIRST_USE_EFFECT)

    async def check_daily_nudges() -> None:
        while True:
            await asyncio.sleep(DAILY_NUDGE_CHECK_INTERVAL_SECONDS)
            now = time.time()
            for chat_id in storage.due_daily_nudges(now, DAILY_NUDGE_MAX_SENDS):
                try:
                    await bot.send_chat_action(chat_id, ChatAction.TYPING)
                    await asyncio.sleep(random.uniform(0.6, 1.0))
                    await bot.send_message(chat_id, templates.DAILY_DISCOUNT_OFFER)
                except Exception:
                    logging.exception("Daily nudge send failed chat=%s", chat_id)
                storage.advance_daily_nudge(chat_id, now + DAILY_NUDGE_INTERVAL_SECONDS)

    async def _submit_order_and_advance(chat_id: int, payload: dict) -> None:
        delivered = await crm.send_order(payload)
        if delivered:
            # Заказ оформлен — сделка передаётся менеджеру на закрытие.
            await crm.advance_stage(chat_id, "order")

    async def push_order(message: Message, supervisor) -> None:
        """Отправляет клиенту инструкцию+благодарность и заказ в CRM.

        Ответ клиенту уже отправлен и ждать сеть не должен, поэтому CRM-задача
        не ожидается здесь: результат и ошибки уходят в лог внутри send_order.
        Клиентские сообщения claim_event-дедуплицированы по составу заказа —
        повторное подтверждение того же состава (тот же externalKey) их не
        повторит, а изменившийся состав — новый заказ, новая благодарность.
        """
        author = message.from_user
        payload = build_order_payload(
            message.chat.id,
            supervisor,
            customer_name=author.full_name if author else None,
            telegram_username=author.username if author else None,
        )
        if payload is None:
            return
        chat_id = message.chat.id
        storage.cancel_daily_nudge(chat_id)
        if storage.claim_event(chat_id, f"order_thanks:{payload['externalKey']}"):
            await bot.send_chat_action(chat_id, ChatAction.TYPING)
            await asyncio.sleep(random.uniform(0.6, 1.0))
            await message.answer(templates.USAGE_INSTRUCTIONS)
            await bot.send_chat_action(chat_id, ChatAction.TYPING)
            await asyncio.sleep(random.uniform(0.6, 1.0))
            await message.answer(templates.THANK_YOU_ORDER)
        if not crm.enabled:
            return
        task = asyncio.create_task(_submit_order_and_advance(message.chat.id, payload))
        crm_tasks.add(task)
        task.add_done_callback(crm_tasks.discard)

    def push_deal(message: Message, *, is_new_conversation: bool) -> None:
        """Заводит сделку в CRM при первом сообщении клиента этому боту."""
        if not crm.enabled or not is_new_conversation:
            return
        author = message.from_user
        payload = build_deal_payload(
            message.chat.id,
            customer_name=author.full_name if author else None,
            telegram_username=author.username if author else None,
        )
        task = asyncio.create_task(crm.create_deal(payload))
        crm_tasks.add(task)
        task.add_done_callback(crm_tasks.discard)

    def push_stage(message: Message, text: str) -> None:
        """Продвигает сделку по воронке по распознанному намерению клиента."""
        if not crm.enabled:
            return
        action = stage_action_for_inbound(text)
        if not action:
            return
        task = asyncio.create_task(crm.advance_stage(message.chat.id, action))
        crm_tasks.add(task)
        task.add_done_callback(crm_tasks.discard)

    async def claim(message: Message, *, rate_limit: bool = True) -> bool:
        chat_id = message.chat.id
        if not storage.claim_message(chat_id, message.message_id):
            logging.info(
                "Ignoring duplicate Telegram message chat=%s message=%s",
                chat_id,
                message.message_id,
            )
            return False
        if rate_limit and not storage.allow_request(
            chat_id, settings.rate_limit_per_minute
        ):
            await message.answer(RATE_LIMIT_TEXT)
            return False
        return True

    async def download(file_id: str) -> bytes:
        telegram_file = await bot.get_file(file_id)
        if not telegram_file.file_path:
            raise RuntimeError("Telegram returned no file path")
        output = BytesIO()
        await bot.download_file(telegram_file.file_path, destination=output)
        return output.getvalue()

    async def process(
        message: Message,
        text: str,
        *,
        kind: str,
        image_data_url: str | None = None,
    ) -> None:
        chat_id = message.chat.id
        logging.info(
            "Processing Telegram message chat=%s message=%s kind=%s",
            chat_id,
            message.message_id,
            kind,
        )
        history = storage.history(chat_id)
        is_new_conversation = not history
        push_deal(message, is_new_conversation=is_new_conversation)
        if kind == "text":
            push_stage(message, text)

        # Первый контакт — готовый сценарий без обращения к ИИ вообще:
        # экономия полного цикла черновик+проверка на каждый новый диалог.
        if kind == "text" and is_new_conversation:
            await message.answer(templates.PRODUCT_INFO)
            await bot.send_chat_action(chat_id, ChatAction.TYPING)
            await asyncio.sleep(random.uniform(0.6, 1.0))
            await message.answer(templates.CITY_QUESTION)
            storage.add_exchange(
                chat_id, message.message_id, text,
                f"{templates.PRODUCT_INFO}\n\n{templates.CITY_QUESTION}", kind,
            )
            storage.schedule_daily_nudge(chat_id, time.time() + DAILY_NUDGE_INTERVAL_SECONDS)
            task = asyncio.create_task(idle_nudge(chat_id, message.message_id))
            follow_up_tasks.add(task)
            task.add_done_callback(follow_up_tasks.discard)
            return

        memory = storage.memory(chat_id)
        auto_result_photos = kind == "text" and should_send_results_after_city(
            text, [item.content for item in history]
        )
        await bot.send_chat_action(chat_id, ChatAction.TYPING)
        current_settings = effective_settings(storage, settings)

        # Роутер шаблонов: если вопрос дословно попадает под готовый ответ
        # (срок эффекта, состав) — шлём его напрямую, без полного цикла ИИ.
        if kind == "text":
            template_id = await assistant.route(text, history, model=current_settings["model"])
            template_text = ROUTED_TEMPLATES.get(template_id)
            if template_text:
                await message.answer(template_text)
                storage.add_exchange(chat_id, message.message_id, text, template_text, kind)
                logging.info(
                    "Template routed chat=%s message=%s template=%s",
                    chat_id, message.message_id, template_id,
                )
                task = asyncio.create_task(idle_nudge(chat_id, message.message_id))
                follow_up_tasks.add(task)
                task.add_done_callback(follow_up_tasks.discard)
                return

        reply = await assistant.reply(
            text,
            history,
            customer_memory=memory,
            image_data_url=image_data_url,
            manager_prompt=current_settings["managerPrompt"],
            supervisor_prompt=current_settings["supervisorPrompt"],
            model=current_settings["model"],
        )
        logging.info(
            "AI reply ready chat=%s message=%s",
            chat_id,
            message.message_id,
        )
        parts = split_outgoing_messages(reply.text)
        for index, part in enumerate(parts):
            if index:
                await bot.send_chat_action(chat_id, ChatAction.TYPING)
                typing_delay = min(2.0, max(0.7, len(part) / 55))
                await asyncio.sleep(typing_delay + random.uniform(0.0, 0.35))
            await message.answer(part)
        logging.info(
            "Telegram reply sent chat=%s message=%s parts=%s",
            chat_id,
            message.message_id,
            len(parts),
        )
        stored_reply = "\n\n".join(parts)
        storage.add_exchange(
            chat_id,
            message.message_id,
            f"[{kind}] {text}" if kind != "text" else text,
            stored_reply,
            kind,
        )
        storage.update_memory(
            chat_id,
            reply.supervisor.customer_memory,
            reply.supervisor.order_summary,
        )
        task = asyncio.create_task(idle_nudge(chat_id, message.message_id))
        follow_up_tasks.add(task)
        task.add_done_callback(follow_up_tasks.discard)
        await push_order(message, reply.supervisor)
        if kind == "text" and wants_result_photos(text):
            await message.answer("Вот результат нашего клиента до и после применения 👇")
            await bot.send_media_group(chat_id, media=result_photo_group())
            task = asyncio.create_task(
                follow_up_after_results(chat_id, message.message_id)
            )
            follow_up_tasks.add(task)
            task.add_done_callback(follow_up_tasks.discard)
        elif auto_result_photos and storage.claim_event(chat_id, "results_after_city"):
            await message.answer("Вот результат нашего клиента до и после применения 👇")
            await bot.send_media_group(chat_id, media=result_photo_group())
            await bot.send_chat_action(chat_id, ChatAction.TYPING)
            await asyncio.sleep(random.uniform(0.8, 1.3))
            await message.answer(templates.FIRST_USE_EFFECT)
            delivery_text = templates.delivery_template_for_city(text)
            if delivery_text:
                await bot.send_chat_action(chat_id, ChatAction.TYPING)
                await asyncio.sleep(random.uniform(0.7, 1.1))
                await message.answer(delivery_text)
            await bot.send_chat_action(chat_id, ChatAction.TYPING)
            await asyncio.sleep(random.uniform(0.7, 1.1))
            await message.answer("Вам оформить заказ?")
            task = asyncio.create_task(
                follow_up_after_results(chat_id, message.message_id)
            )
            follow_up_tasks.add(task)
            task.add_done_callback(follow_up_tasks.discard)

    @dp.message(CommandStart())
    async def start(message: Message) -> None:
        async with locks[message.chat.id]:
            if not await claim(message, rate_limit=False):
                return
            storage.clear_chat(message.chat.id)
            start_text = (
                "Здравствуйте! Я виртуальный помощник Smilekit. "
                "Могу проконсультировать по продукции и помочь с заказом. "
                "Что хотите уточнить?"
            )
            await message.answer(start_text)
            storage.add_exchange(
                message.chat.id, message.message_id, "/start", start_text, "command"
            )

    @dp.message(Command("reset"))
    async def reset(message: Message) -> None:
        async with locks[message.chat.id]:
            if not await claim(message, rate_limit=False):
                return
            storage.clear_chat(message.chat.id)
            await message.answer("Диалог и сохранённая память очищены.")

    @dp.message(F.photo)
    async def handle_photo(message: Message) -> None:
        async with locks[message.chat.id]:
            if not await claim(message):
                return
            try:
                photo = await download(message.photo[-1].file_id)
                encoded = base64.b64encode(photo).decode("ascii")
                text = message.caption.strip() if message.caption else PHOTO_PROMPT
                await process(
                    message,
                    text,
                    kind="photo",
                    image_data_url=f"data:image/jpeg;base64,{encoded}",
                )
            except Exception:
                logging.exception("Photo processing failed")
                await message.answer(
                    "Не получилось обработать фото. "
                    "Попробуйте отправить его ещё раз немного позже."
                )

    @dp.message(F.voice | F.audio)
    async def handle_audio(message: Message) -> None:
        async with locks[message.chat.id]:
            if not await claim(message):
                return
            media = message.voice or message.audio
            if not media:
                return
            if media.duration and media.duration > settings.max_voice_seconds:
                await message.answer(
                    f"Голосовое слишком длинное. Отправьте запись до "
                    f"{settings.max_voice_seconds // 60} минут."
                )
                return
            try:
                await bot.send_chat_action(message.chat.id, ChatAction.TYPING)
                audio = await download(media.file_id)
                filename = getattr(media, "file_name", None) or "voice.ogg"
                transcript = await assistant.transcribe(
                    audio,
                    filename,
                    settings.transcription_model,
                )
                await process(
                    message,
                    transcript,
                    kind="voice",
                )
            except Exception:
                logging.exception("Voice processing failed")
                await message.answer(
                    "Не получилось распознать голосовое. "
                    "Отправьте его ещё раз или напишите вопрос текстом."
                )

    async def debounced_text_process(message: Message) -> None:
        chat_id = message.chat.id
        try:
            await asyncio.sleep(DEBOUNCE_SECONDS)
        except asyncio.CancelledError:
            # Клиент прислал ещё одно сообщение до истечения окна ожидания —
            # его обработчик уже запланировал новый debounce-таск и допишет
            # текст в pending_texts, дальше ничего делать не нужно.
            return
        # Убираем себя из реестра ДО захвата lock: если во время генерации
        # ответа придёт новое сообщение, его обработчик не должен отменять
        # уже выполняющийся вызов AI — вместо этого он поставит в очередь
        # свой собственный debounce-таск, который дождётся освобождения lock.
        debounce_tasks.pop(chat_id, None)
        texts = pending_texts.pop(chat_id, None)
        if not texts:
            return
        combined_text = "\n".join(texts)
        async with locks[chat_id]:
            try:
                await process(message, combined_text, kind="text")
            except Exception:
                logging.exception(
                    "Text processing failed chat=%s message=%s",
                    chat_id,
                    message.message_id,
                )
                await message.answer(
                    "Не получилось обработать сообщение. "
                    "Отправьте его, пожалуйста, ещё раз."
                )

    @dp.message(F.text)
    async def handle_text(message: Message) -> None:
        if not message.text:
            return
        if not await claim(message):
            return
        chat_id = message.chat.id
        pending_texts[chat_id].append(message.text)
        previous_task = debounce_tasks.get(chat_id)
        if previous_task is not None:
            previous_task.cancel()
        debounce_tasks[chat_id] = asyncio.create_task(debounced_text_process(message))

    @dp.message()
    async def unsupported(message: Message) -> None:
        async with locks[message.chat.id]:
            if not await claim(message):
                return
            await message.answer(
                "Я могу обработать текст, фотографию или голосовое сообщение."
            )

    # /internal/* HTTP-слой для CRM (настройки бота + лаборатория) поднимается
    # рядом с long polling в этом же процессе, на отдельном порту — вебхуки не
    # трогаем, это чисто аддитивная ручка для внутреннего использования.
    api_app = create_app(settings, storage, assistant)
    runner = web.AppRunner(api_app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=settings.port)
    await site.start()
    logging.info("Internal HTTP API listening on port %s", settings.port)

    daily_nudge_task = asyncio.create_task(check_daily_nudges())

    try:
        await bot.delete_webhook(drop_pending_updates=False)
        await dp.start_polling(bot)
    finally:
        daily_nudge_task.cancel()
        for task in follow_up_tasks:
            task.cancel()
        for task in debounce_tasks.values():
            task.cancel()
        if crm_tasks:
            # Заказ терять нельзя: даём отправке доехать до выхода.
            await asyncio.wait(crm_tasks, timeout=REQUEST_TIMEOUT)
        await runner.cleanup()
        storage.close()
        await bot.session.close()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(main())
