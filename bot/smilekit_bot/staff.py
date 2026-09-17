"""Смены сотрудников в Telegram: команда /staffs, вход по паролю, меню смены.

Вся логика смен (кто на смене, запросы, подтверждения, блок после 5 неверных
паролей) живёт в CRM — бот только показывает состояние и передаёт нажатия в
/api/internal/staff/* (договор: STAFF_SPEC.md). Своего состояния у бота одно:
«этот чат сейчас вводит пароль» (storage.staff_password_waits), чтобы пароль
не ушёл в продающий сценарий — ИИ, дожим и CRM-сделку клиента.

Хендлеры регистрируются в Dispatcher раньше всех клиентских (см. __main__.py):
aiogram перебирает хендлеры по порядку регистрации, и общий F.text иначе
забрал бы пароль первым.
"""

import logging
import time
from datetime import datetime, timedelta, timezone

from aiogram import Dispatcher, F
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from .crm import (
    STAFF_ACTION_PATH,
    STAFF_DECIDE_PATH,
    STAFF_LOGIN_PATH,
    STAFF_STATE_PATH,
    CrmClient,
)
from .storage import BotStorage


LOGGER = logging.getLogger(__name__)

PASSWORD_WAIT_SECONDS = 5 * 60
BISHKEK_TZ = timezone(timedelta(hours=6))
CALLBACK_PREFIX = "staff:"
ACTIONS = {"take", "join", "close", "cancel_request", "logout"}
KIND_LABELS = {"take": "«Моя смена»", "join": "«Работа вместе»"}

CRM_UNAVAILABLE_TEXT = "CRM не отвечает, попробуйте позже"
PASSWORD_PROMPT_TEXT = "Введите ваш пароль сотрудника"
LOGIN_CANCELLED_TEXT = "Вход отменён."
NOT_LOGGED_IN_TEXT = "Вы не вошли. Отправьте /staffs, чтобы войти."


def format_time(value: object) -> str:
    """ISO-время из CRM → «17.09 09:15» по Бишкеку. Непонятное значение
    показываем как есть, а не роняем меню."""
    if not isinstance(value, str) or not value:
        return "—"
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return value
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(BISHKEK_TZ).strftime("%d.%m %H:%M")


def render_menu(state: dict) -> tuple[str, InlineKeyboardMarkup]:
    """Текст и кнопки меню по ответу /api/internal/staff/state (staff не null)."""
    staff = state.get("staff") or {}
    lines = [f"👤 {staff.get('fullName') or '—'}", ""]

    on_shift = state.get("onShift") or []
    if not on_shift:
        lines.append("Сейчас на смене: никого")
    elif len(on_shift) == 1:
        person = on_shift[0]
        lines.append(
            f"Сейчас на смене: {person.get('fullName')} с {format_time(person.get('startedAt'))}"
        )
    else:
        lines.append("Сейчас на смене вместе:")
        lines.extend(
            f"• {person.get('fullName')} — с {format_time(person.get('startedAt'))}"
            for person in on_shift
        )

    my_shift = state.get("myShift")
    if my_shift:
        together = " (вместе)" if my_shift.get("mode") == "joint" else ""
        lines.append(f"Моя смена: идёт с {format_time(my_shift.get('startedAt'))}{together}")
    else:
        lines.append("Моя смена: нет")

    my_request = state.get("myPendingRequest")
    if my_request:
        kind = KIND_LABELS.get(my_request.get("kind"), "")
        lines.append(
            f"Мой запрос {kind} ждёт подтверждения (с {format_time(my_request.get('createdAt'))})"
        )

    rows = [
        [
            InlineKeyboardButton(text="🟢 Моя смена", callback_data="staff:take"),
            InlineKeyboardButton(text="🤝 Работа вместе", callback_data="staff:join"),
        ],
        [InlineKeyboardButton(text="🔴 Закрыть смену", callback_data="staff:close")],
    ]

    awaiting = state.get("awaitingMyDecision") or []
    if awaiting:
        lines.extend(["", "Ждут моего решения:"])
    for request in awaiting:
        name = request.get("requesterName") or "—"
        kind = KIND_LABELS.get(request.get("kind"), "")
        lines.append(f"• {name} просит {kind} ({format_time(request.get('createdAt'))})")
        # Несколько запросов сразу — без имени на кнопке не понять, чей какой.
        suffix = f": {name}" if len(awaiting) > 1 else ""
        request_id = request.get("requestId")
        rows.append([
            InlineKeyboardButton(text=f"✅ Подтвердить{suffix}", callback_data=f"staff:approve:{request_id}"),
            InlineKeyboardButton(text=f"❌ Отклонить{suffix}", callback_data=f"staff:reject:{request_id}"),
        ])

    if my_request:
        rows.append([InlineKeyboardButton(text="Отменить запрос", callback_data="staff:cancel_request")])
    rows.append([
        InlineKeyboardButton(text="🔄 Обновить", callback_data="staff:refresh"),
        InlineKeyboardButton(text="🚪 Выйти", callback_data="staff:logout"),
    ])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def _reply_text(response: tuple[int, dict] | None) -> str:
    """message при успехе, error при отказе — оба текста пишет CRM."""
    if response is None:
        return CRM_UNAVAILABLE_TEXT
    _status, body = response
    return str(body.get("message") or body.get("error") or "Не получилось, попробуйте ещё раз")


async def _menu_view(crm: CrmClient, telegram_id: int) -> tuple[str, InlineKeyboardMarkup | None]:
    """Текст и кнопки для текущего состояния сотрудника."""
    response = await crm.staff_call(STAFF_STATE_PATH, {"telegramId": telegram_id})
    if response is None:
        return CRM_UNAVAILABLE_TEXT, None
    status, body = response
    if status != 200 or not body.get("ok"):
        return _reply_text(response), None
    if not body.get("staff"):
        return NOT_LOGGED_IN_TEXT, None
    return render_menu(body)


async def _show(callback: CallbackQuery, text: str, keyboard: InlineKeyboardMarkup | None) -> None:
    """Перерисовывает сообщение с кнопкой. Если его изменить нельзя (старое,
    недоступное) — присылает меню новым сообщением."""
    message = callback.message
    if isinstance(message, Message):
        try:
            await message.edit_text(text, reply_markup=keyboard)
            return
        except TelegramBadRequest as error:
            if "message is not modified" in str(error):
                return
            LOGGER.warning("Staff menu edit failed, sending new message: %s", error)
    await callback.bot.send_message(callback.from_user.id, text, reply_markup=keyboard)


def register_staff_handlers(dp: Dispatcher, crm: CrmClient, storage: BotStorage) -> None:
    private = F.chat.type == ChatType.PRIVATE

    def waiting_password(message: Message) -> bool:
        return storage.is_waiting_staff_password(message.chat.id, time.time())

    @dp.message(Command("staffs"), private)
    async def staffs(message: Message) -> None:
        chat_id = message.chat.id
        if storage.is_waiting_staff_password(chat_id, time.time()):
            storage.clear_staff_password_wait(chat_id)
            await message.answer(LOGIN_CANCELLED_TEXT)
            return
        telegram_id = message.from_user.id if message.from_user else chat_id
        response = await crm.staff_call(STAFF_STATE_PATH, {"telegramId": telegram_id})
        if response is None:
            await message.answer(CRM_UNAVAILABLE_TEXT)
            return
        status, body = response
        if status != 200 or not body.get("ok"):
            await message.answer(_reply_text(response))
            return
        if not body.get("staff"):
            storage.start_staff_password_wait(chat_id, time.time() + PASSWORD_WAIT_SECONDS)
            await message.answer(PASSWORD_PROMPT_TEXT)
            return
        text, keyboard = render_menu(body)
        await message.answer(text, reply_markup=keyboard)

    @dp.message(Command("cancel"), private, waiting_password)
    async def cancel_login(message: Message) -> None:
        storage.clear_staff_password_wait(message.chat.id)
        await message.answer(LOGIN_CANCELLED_TEXT)

    # Прочие команды (/start, /reset…) пропускаем дальше: пароль командой не бывает.
    @dp.message(F.text, ~F.text.startswith("/"), private, waiting_password)
    async def staff_password(message: Message) -> None:
        chat_id = message.chat.id
        telegram_id = message.from_user.id if message.from_user else chat_id
        password = message.text or ""
        # Пароль не должен оставаться в переписке: телефон могут взять в руки
        # другие. Удалить не вышло (нет прав, сообщение старое) — не страшно.
        try:
            await message.delete()
        except Exception:
            LOGGER.info("Could not delete staff password message chat=%s", chat_id)

        response = await crm.staff_call(
            STAFF_LOGIN_PATH, {"telegramId": telegram_id, "password": password}
        )
        if response is None:
            storage.clear_staff_password_wait(chat_id)
            await message.answer(CRM_UNAVAILABLE_TEXT)
            return
        status, body = response
        if status == 200 and body.get("ok"):
            storage.clear_staff_password_wait(chat_id)
            full_name = (body.get("staff") or {}).get("fullName") or "—"
            await message.answer(
                f"Вход: {full_name}",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="ОК", callback_data="staff:refresh")
                ]]),
            )
            return
        if status == 401:
            # Ждём следующую попытку заново: сотрудник просто вводит пароль ещё
            # раз. Лимит попыток держит CRM (после 5 ошибок ответит 429).
            storage.start_staff_password_wait(chat_id, time.time() + PASSWORD_WAIT_SECONDS)
            attempts = body.get("attemptsLeft")
            await message.answer(
                f"Неверный пароль, осталось попыток: {attempts if attempts is not None else '—'}\n"
                "Введите пароль ещё раз или /cancel"
            )
            return
        storage.clear_staff_password_wait(chat_id)
        await message.answer(_reply_text(response))

    @dp.callback_query(F.data.startswith(CALLBACK_PREFIX))
    async def staff_callback(callback: CallbackQuery) -> None:
        # Отвечаем сразу: у Telegram на ответ ~15 с, а до CRM два запроса по 10 с.
        try:
            await callback.answer()
        except Exception:
            LOGGER.info("Staff callback answer failed")
        telegram_id = callback.from_user.id
        parts = (callback.data or "").split(":", 2)
        command = parts[1] if len(parts) > 1 else ""

        notice = None
        if command in {"approve", "reject"} and len(parts) == 3 and parts[2]:
            response = await crm.staff_call(
                STAFF_DECIDE_PATH,
                {"telegramId": telegram_id, "requestId": parts[2], "decision": command},
            )
            notice = _reply_text(response)
        elif command in ACTIONS:
            response = await crm.staff_call(
                STAFF_ACTION_PATH, {"telegramId": telegram_id, "action": command}
            )
            notice = _reply_text(response)
        elif command != "refresh":
            return

        if notice == CRM_UNAVAILABLE_TEXT:
            await _show(callback, notice, None)
            return
        text, keyboard = await _menu_view(crm, telegram_id)
        if notice and text == NOT_LOGGED_IN_TEXT:
            # После «Выйти» меню уже нет — достаточно ответа CRM и подсказки.
            text = "Чтобы войти снова, отправьте /staffs."
        if notice:
            text = f"{notice}\n\n{text}"
        await _show(callback, text, keyboard)
