"""Отдельный тестовый Telegram-бот на детерминированных шаблонах — без ИИ,
без записи в CRM/Supabase, полностью изолирован от прод-бота smilekit_bot.

Запуск: python -m smilekit_bot_template_test (из корня SmileKit/bot,
нужен .env.template_test с TELEGRAM_BOT_TOKEN).
"""
import asyncio
import logging
import random
import time
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatAction
from aiogram.filters import Command, CommandStart
from aiogram.types import BotCommand, FSInputFile, Message

from .config import TemplateTestSettings
from .router import ChatState, advance_followup, route

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
EFFECT_PHOTO_PATH = ASSETS_DIR / "results" / "before_after.jpg"
VOICE_DIR = ASSETS_DIR / "voice"

# Защита от повторов: то же самое сообщение подряд в одном чате в течение
# этого окна — не обрабатываем второй раз (дубль от Telegram при обрыве
# связи, или клиент случайно отправил одно и то же дважды).
DUPLICATE_WINDOW_SECONDS = 30

# Пауза между несколькими сообщениями одного ответа — то же самое, что и в
# проде (asyncio.sleep(random.uniform(...)) перед отправкой в smilekit_bot/
# __main__.py), чтобы бот не вываливал весь ответ одним залпом.
MIN_DELAY_SECONDS = 0.8
MAX_DELAY_SECONDS = 1.4

# В памяти процесса — это одноразовый тестовый инструмент для одного
# владельца, не прод: не нужно тянуть sqlite/CRM/Supabase ради этого.
_chats: dict[int, ChatState] = {}
_last_message: dict[int, tuple[str, float]] = {}  # chat_id -> (text, timestamp)
# Живой баг: два сообщения от одного клиента подряд (до ответа на первое)
# обрабатывались как две параллельные asyncio-задачи и гонялись за одним и
# тем же ChatState — второе сообщение читало is_first_message ДО того, как
# первое успевало его же выставить, и получало не тот шаблон. Лок на чат
# сериализует обработку — как если бы клиент писал по одному сообщению.
_locks: dict[int, asyncio.Lock] = {}


def _state_for(chat_id: int) -> ChatState:
    if chat_id not in _chats:
        _chats[chat_id] = ChatState()
    return _chats[chat_id]


def _lock_for(chat_id: int) -> asyncio.Lock:
    if chat_id not in _locks:
        _locks[chat_id] = asyncio.Lock()
    return _locks[chat_id]


def _is_duplicate(chat_id: int, text: str) -> bool:
    previous = _last_message.get(chat_id)
    now = time.monotonic()
    _last_message[chat_id] = (text, now)
    if previous is None:
        return False
    previous_text, previous_time = previous
    return previous_text == text and (now - previous_time) < DUPLICATE_WINDOW_SECONDS


async def _send_texts(message: Message, texts: list[str]) -> None:
    for index, text in enumerate(texts):
        if index > 0:
            await asyncio.sleep(random.uniform(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS))
        await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
        await asyncio.sleep(random.uniform(0.6, 1.1))
        await message.answer(text)


async def main() -> None:
    settings = TemplateTestSettings()
    bot = Bot(token=settings.telegram_bot_token.get_secret_value())
    dp = Dispatcher()

    @dp.message(CommandStart())
    async def on_start(message: Message) -> None:
        _chats[message.chat.id] = ChatState()
        await message.answer(
            "Тестовый бот SmileKit (шаблоны, без ИИ) 😊\n\n"
            "Пишите как обычный клиент — отвечаю теми же шаблонами, что и WhatsApp-бот.\n\n"
            "Чтобы увидеть дожим: напишите цену и город (например «сколько стоит» → «Бишкек»), "
            "потом жмите /repeat — каждый раз шлёт следующий шаг дожима (CTA → «актуально?» → скидка 13% → "
            "скидка 20% → «осталось 2 упаковки»), не дожидаясь реальных минут ожидания.\n\n"
            "/reset — начать диалог заново."
        )

    @dp.message(Command("reset"))
    async def on_reset(message: Message) -> None:
        _chats[message.chat.id] = ChatState()
        await message.answer("— диалог сброшен —")

    @dp.message(Command("repeat"))
    async def on_repeat(message: Message) -> None:
        # Перемотка времени: сразу шлёт следующий шаг дожима (3/10/15/15/15
        # минут в реальном боте), не дожидаясь реального времени — тот же
        # смысл, что кнопка «Промотать» в WhatsApp Лаборатории.
        state = _state_for(message.chat.id)
        result = advance_followup(state)
        await _send_texts(message, result.texts)

    @dp.message(F.text)
    async def on_text(message: Message) -> None:
        text = message.text or ""
        if _is_duplicate(message.chat.id, text):
            logging.info("duplicate message ignored chat=%s", message.chat.id)
            return
        async with _lock_for(message.chat.id):
            state = _state_for(message.chat.id)
            is_first_message = len(state.history) == 0
            result = route(text, state)
        if is_first_message:
            # Живой баг, уже пойманный и починенный в WhatsApp-боте: самое
            # первое «Здравствуйте» уходило мгновенно — читалось как
            # автоответчик. 10 секунд — та же пауза, что делает менеджер,
            # прежде чем открыть чат и начать печатать.
            await asyncio.sleep(10)
        if result.send_text:
            await _send_texts(message, result.texts)
        if result.send_results_block:
            await asyncio.sleep(random.uniform(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS))
            photo_path = EFFECT_PHOTO_PATH
            if photo_path.exists():
                await message.answer_photo(FSInputFile(photo_path))
            else:
                logging.warning("results photo missing at %s", photo_path)
        if result.voice:
            await message.bot.send_chat_action(message.chat.id, ChatAction.RECORD_VOICE)
            await asyncio.sleep(random.uniform(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS))
            voice_path = VOICE_DIR / result.voice
            if voice_path.exists():
                await message.answer_voice(FSInputFile(voice_path))
            else:
                logging.warning("voice file missing at %s", voice_path)

    @dp.message()
    async def on_other(message: Message) -> None:
        # Фото/голос/файлы — этот тестовый бот работает только с текстом.
        await message.answer("Этот тестовый бот пока понимает только текст 😊")

    await bot.set_my_commands([
        BotCommand(command="start", description="Начать заново"),
        BotCommand(command="reset", description="Сбросить диалог"),
        BotCommand(command="repeat", description="Промотать время — следующий шаг дожима"),
    ])
    await bot.delete_webhook(drop_pending_updates=False)
    logging.info("smilekit_bot_template_test: long polling started")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
