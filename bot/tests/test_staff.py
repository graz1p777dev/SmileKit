"""Смены сотрудников: /staffs, вход по паролю, меню и кнопки, HTTP-клиент CRM,
эндпоинт /internal/staff/notify.

Хендлеры гоняем через настоящий Dispatcher (dp.feed_update) с подменённой
сессией Telegram — так проверяются и фильтры, и порядок регистрации: пароль
не должен дойти до общего F.text, который в боевом боте ведёт в ИИ."""

import io
import json
import urllib.error
from datetime import datetime

import pytest
from aiogram import Bot, Dispatcher, F
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import (
    AnswerCallbackQuery,
    DeleteMessage,
    EditMessageText,
    GetMe,
    SendMessage,
)
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from smilekit_bot.crm import (
    STAFF_ACTION_PATH,
    STAFF_DECIDE_PATH,
    STAFF_LOGIN_PATH,
    STAFF_STATE_PATH,
    CrmClient,
)
from smilekit_bot.staff import (
    CRM_UNAVAILABLE_TEXT,
    PASSWORD_PROMPT_TEXT,
    register_staff_handlers,
    render_menu,
)
from smilekit_bot.storage import BotStorage

from test_settings_api import TOKEN, running_client

CHAT_ID = 555


class FakeSession(BaseSession):
    """Сессия Telegram без сети: запоминает вызовы и отдаёт правдоподобный ответ."""

    def __init__(self):
        super().__init__()
        self.calls = []

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, GetMe):
            return User(id=42, is_bot=True, first_name="Smilekit", username="smilekit_bot")
        if isinstance(method, (SendMessage, EditMessageText)):
            return Message(
                message_id=900 + len(self.calls),
                date=datetime.now(),
                chat=Chat(id=CHAT_ID, type="private"),
                text=method.text,
            )
        return True

    async def close(self):
        pass

    async def stream_content(self, *args, **kwargs):  # pragma: no cover
        yield b""

    def sent(self, method_type=SendMessage):
        return [call for call in self.calls if isinstance(call, method_type)]


class FakeCrm:
    """Подмена CrmClient.staff_call: ответы по пути, запись запросов."""

    def __init__(self, responses: dict):
        self.responses = responses
        self.calls: list[tuple[str, dict]] = []

    async def staff_call(self, path, payload):
        self.calls.append((path, payload))
        response = self.responses.get(path)
        if isinstance(response, list):
            return response.pop(0)
        return response


LOGGED_OUT = (200, {"ok": True, "staff": None, "myShift": None, "onShift": [],
                    "myPendingRequest": None, "awaitingMyDecision": []})
LOGGED_IN = (200, {
    "ok": True,
    "staff": {"id": "s1", "fullName": "Айгуль Нурланова"},
    "myShift": None,
    "onShift": [{"staffId": "s2", "fullName": "Бакыт Асанов", "mode": "solo",
                 "startedAt": "2026-09-17T03:15:00.000Z"}],
    "myPendingRequest": None,
    "awaitingMyDecision": [],
})


@pytest.fixture
def env(tmp_path):
    storage = BotStorage(str(tmp_path / "bot.db"))
    session = FakeSession()
    bot = Bot(token="42:TEST", session=session)
    sales_texts: list[str] = []

    def build(responses: dict):
        crm = FakeCrm(responses)
        dp = Dispatcher()
        register_staff_handlers(dp, crm, storage)

        # Заменитель продающего сценария из __main__.py: регистрируется позже,
        # как и там, и ловит любой текст.
        @dp.message(F.text)
        async def sales(message: Message) -> None:
            sales_texts.append(message.text)

        return dp, crm

    yield build, bot, session, storage, sales_texts
    storage.close()


_update_id = 0


def text_update(text: str, chat_type: str = "private") -> Update:
    global _update_id
    _update_id += 1
    return Update(
        update_id=_update_id,
        message=Message(
            message_id=_update_id,
            date=datetime.now(),
            chat=Chat(id=CHAT_ID if chat_type == "private" else -100, type=chat_type),
            from_user=User(id=CHAT_ID, is_bot=False, first_name="Айгуль"),
            text=text,
        ),
    )


def callback_update(data: str) -> Update:
    global _update_id
    _update_id += 1
    return Update(
        update_id=_update_id,
        callback_query=CallbackQuery(
            id=str(_update_id),
            from_user=User(id=CHAT_ID, is_bot=False, first_name="Айгуль"),
            chat_instance="ci",
            data=data,
            message=Message(
                message_id=77,
                date=datetime.now(),
                chat=Chat(id=CHAT_ID, type="private"),
                text="меню",
            ),
        ),
    )


# ─── Вход по паролю ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_login_success_asks_password_then_shows_ok_button(env):
    build, bot, session, storage, sales_texts = env
    dp, crm = build({
        STAFF_STATE_PATH: LOGGED_OUT,
        STAFF_LOGIN_PATH: (200, {"ok": True, "staff": {"id": "s1", "fullName": "Айгуль Нурланова"}}),
    })

    await dp.feed_update(bot, text_update("/staffs"))
    assert session.sent()[-1].text == PASSWORD_PROMPT_TEXT

    await dp.feed_update(bot, text_update("sekret123"))
    assert crm.calls[-1] == (STAFF_LOGIN_PATH, {"telegramId": CHAT_ID, "password": "sekret123"})
    reply = session.sent()[-1]
    assert reply.text == "Вход: Айгуль Нурланова"
    assert reply.reply_markup.inline_keyboard[0][0].text == "ОК"
    # Сообщение с паролем удаляется, а в продающий сценарий пароль не попадает.
    assert session.sent(DeleteMessage)
    assert sales_texts == []
    # Ожидание снято: следующий текст — снова обычный диалог.
    await dp.feed_update(bot, text_update("Сколько стоит?"))
    assert sales_texts == ["Сколько стоит?"]


@pytest.mark.asyncio
async def test_login_wrong_password_shows_attempts_and_keeps_waiting(env):
    build, bot, session, storage, sales_texts = env
    dp, crm = build({
        STAFF_STATE_PATH: LOGGED_OUT,
        STAFF_LOGIN_PATH: (401, {"ok": False, "error": "Неверный пароль", "attemptsLeft": 3}),
    })
    await dp.feed_update(bot, text_update("/staffs"))
    await dp.feed_update(bot, text_update("wrong"))
    assert session.sent()[-1].text.startswith("Неверный пароль, осталось попыток: 3")

    await dp.feed_update(bot, text_update("wrong again"))
    assert [path for path, _ in crm.calls].count(STAFF_LOGIN_PATH) == 2
    assert sales_texts == []


@pytest.mark.asyncio
async def test_login_blocked_shows_crm_error_and_stops_waiting(env):
    build, bot, session, storage, sales_texts = env
    blocked = "Слишком много попыток. Попробуйте через 5 мин."
    dp, crm = build({
        STAFF_STATE_PATH: LOGGED_OUT,
        STAFF_LOGIN_PATH: (429, {"ok": False, "error": blocked, "retryAfterSeconds": 300}),
    })
    await dp.feed_update(bot, text_update("/staffs"))
    await dp.feed_update(bot, text_update("guess"))
    assert session.sent()[-1].text == blocked
    assert sales_texts == []

    await dp.feed_update(bot, text_update("Привет"))
    assert sales_texts == ["Привет"]


@pytest.mark.asyncio
async def test_password_wait_cancelled_by_repeat_staffs_and_cancel(env):
    build, bot, session, storage, sales_texts = env
    dp, crm = build({STAFF_STATE_PATH: LOGGED_OUT})

    await dp.feed_update(bot, text_update("/staffs"))
    await dp.feed_update(bot, text_update("/staffs"))
    assert session.sent()[-1].text == "Вход отменён."
    await dp.feed_update(bot, text_update("обычный текст"))
    assert sales_texts == ["обычный текст"]

    await dp.feed_update(bot, text_update("/staffs"))
    await dp.feed_update(bot, text_update("/cancel"))
    assert session.sent()[-1].text == "Вход отменён."
    assert STAFF_LOGIN_PATH not in [path for path, _ in crm.calls]


@pytest.mark.asyncio
async def test_password_wait_expires(env):
    build, bot, session, storage, sales_texts = env
    dp, crm = build({STAFF_STATE_PATH: LOGGED_OUT})
    storage.start_staff_password_wait(CHAT_ID, expires_at=1.0)  # давно в прошлом

    await dp.feed_update(bot, text_update("Здравствуйте"))
    assert sales_texts == ["Здравствуйте"]
    assert not storage.is_waiting_staff_password(CHAT_ID, 0.0)


@pytest.mark.asyncio
async def test_password_wait_survives_storage_reopen(tmp_path):
    path = str(tmp_path / "bot.db")
    first = BotStorage(path)
    first.start_staff_password_wait(CHAT_ID, expires_at=2000.0)
    first.close()
    reopened = BotStorage(path)
    assert reopened.is_waiting_staff_password(CHAT_ID, 1000.0)
    reopened.close()


@pytest.mark.asyncio
async def test_staffs_is_ignored_in_groups(env):
    build, bot, session, storage, sales_texts = env
    dp, crm = build({STAFF_STATE_PATH: LOGGED_OUT})
    await dp.feed_update(bot, text_update("/staffs", chat_type="supergroup"))
    assert crm.calls == []
    assert session.sent() == []


@pytest.mark.asyncio
async def test_staffs_when_crm_is_down(env):
    build, bot, session, storage, sales_texts = env
    dp, crm = build({STAFF_STATE_PATH: None})
    await dp.feed_update(bot, text_update("/staffs"))
    assert session.sent()[-1].text == CRM_UNAVAILABLE_TEXT
    assert not storage.is_waiting_staff_password(CHAT_ID, 0.0)


@pytest.mark.asyncio
async def test_staffs_for_logged_in_staff_shows_menu(env):
    build, bot, session, storage, sales_texts = env
    dp, crm = build({STAFF_STATE_PATH: LOGGED_IN})
    await dp.feed_update(bot, text_update("/staffs"))
    reply = session.sent()[-1]
    assert "Бакыт Асанов с 17.09 09:15" in reply.text  # 03:15 UTC → 09:15 Бишкек
    labels = [button.text for row in reply.reply_markup.inline_keyboard for button in row]
    assert labels == ["🟢 Моя смена", "🤝 Работа вместе", "🔴 Закрыть смену", "🔄 Обновить", "🚪 Выйти"]


# ─── Меню ───────────────────────────────────────────────────────────────────


def test_menu_shows_joint_shift_decisions_and_my_request():
    state = dict(LOGGED_IN[1])
    state.update({
        "onShift": [
            {"staffId": "s2", "fullName": "Бакыт Асанов", "mode": "solo", "startedAt": "2026-09-17T03:15:00Z"},
            {"staffId": "s1", "fullName": "Айгуль Нурланова", "mode": "joint", "startedAt": "2026-09-17T04:00:00Z"},
        ],
        "myShift": {"mode": "joint", "startedAt": "2026-09-17T04:00:00Z"},
        "myPendingRequest": {"id": "r0", "kind": "take", "createdAt": "2026-09-17T05:00:00Z"},
        "awaitingMyDecision": [
            {"requestId": "r1", "kind": "join", "requesterName": "Чолпон", "createdAt": "2026-09-17T05:05:00Z"},
        ],
    })
    text, keyboard = render_menu(state)
    assert "Сейчас на смене вместе:" in text
    assert "Моя смена: идёт с 17.09 10:00 (вместе)" in text
    assert "Мой запрос «Моя смена» ждёт подтверждения" in text
    assert "Чолпон просит «Работа вместе»" in text
    data = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert "staff:approve:r1" in data and "staff:reject:r1" in data
    assert "staff:cancel_request" in data


# ─── Кнопки ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_approve_callback_decides_and_redraws_menu(env):
    build, bot, session, storage, sales_texts = env
    dp, crm = build({
        STAFF_DECIDE_PATH: (200, {"ok": True, "message": "Запрос подтверждён"}),
        STAFF_STATE_PATH: LOGGED_IN,
    })
    await dp.feed_update(bot, callback_update("staff:approve:req-1"))

    assert crm.calls[0] == (
        STAFF_DECIDE_PATH, {"telegramId": CHAT_ID, "requestId": "req-1", "decision": "approve"}
    )
    assert session.sent(AnswerCallbackQuery)
    edited = session.sent(EditMessageText)[-1]
    assert edited.text.startswith("Запрос подтверждён\n\n👤 Айгуль Нурланова")


@pytest.mark.asyncio
async def test_reject_callback_shows_crm_error(env):
    build, bot, session, storage, sales_texts = env
    dp, crm = build({
        STAFF_DECIDE_PATH: (409, {"ok": False, "error": "Запрос уже истёк"}),
        STAFF_STATE_PATH: LOGGED_IN,
    })
    await dp.feed_update(bot, callback_update("staff:reject:req-2"))
    assert crm.calls[0][1]["decision"] == "reject"
    assert session.sent(EditMessageText)[-1].text.startswith("Запрос уже истёк")


@pytest.mark.asyncio
async def test_action_callback_calls_action(env):
    build, bot, session, storage, sales_texts = env
    dp, crm = build({
        STAFF_ACTION_PATH: (200, {"ok": True, "result": "requested", "message": "Запрос отправлен"}),
        STAFF_STATE_PATH: LOGGED_IN,
    })
    await dp.feed_update(bot, callback_update("staff:take"))
    assert crm.calls[0] == (STAFF_ACTION_PATH, {"telegramId": CHAT_ID, "action": "take"})
    assert session.sent(EditMessageText)[-1].text.startswith("Запрос отправлен")


@pytest.mark.asyncio
async def test_logout_callback_hides_menu(env):
    build, bot, session, storage, sales_texts = env
    dp, crm = build({
        STAFF_ACTION_PATH: (200, {"ok": True, "result": "logged_out", "message": "Вы вышли"}),
        STAFF_STATE_PATH: LOGGED_OUT,
    })
    await dp.feed_update(bot, callback_update("staff:logout"))
    edited = session.sent(EditMessageText)[-1]
    assert edited.text == "Вы вышли\n\nЧтобы войти снова, отправьте /staffs."
    assert edited.reply_markup is None


@pytest.mark.asyncio
async def test_callback_when_crm_is_down(env):
    build, bot, session, storage, sales_texts = env
    dp, crm = build({STAFF_ACTION_PATH: None})
    await dp.feed_update(bot, callback_update("staff:close"))
    assert session.sent(EditMessageText)[-1].text == CRM_UNAVAILABLE_TEXT


# ─── HTTP-клиент CRM ────────────────────────────────────────────────────────


class _FakeHttpResponse(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


@pytest.mark.asyncio
async def test_staff_call_returns_body_on_success(monkeypatch):
    seen = {}

    def fake_urlopen(request, timeout=None):
        seen["url"] = request.full_url
        seen["token"] = request.get_header("X-internal-token")
        seen["body"] = json.loads(request.data)
        seen["timeout"] = timeout
        return _FakeHttpResponse(b'{"ok": true, "staff": {"id": "s1", "fullName": "A"}}')

    monkeypatch.setattr("smilekit_bot.crm.urllib.request.urlopen", fake_urlopen)
    client = CrmClient("https://crm.example/", "tok")
    status, body = await client.staff_call(STAFF_LOGIN_PATH, {"telegramId": 1, "password": "p"})
    assert status == 200 and body["staff"]["fullName"] == "A"
    assert seen["url"] == "https://crm.example/api/internal/staff/login"
    assert seen["token"] == "tok"
    assert seen["body"] == {"telegramId": 1, "password": "p"}
    assert seen["timeout"] == 10.0


@pytest.mark.parametrize("code, payload", [
    (401, {"ok": False, "error": "Неверный пароль", "attemptsLeft": 4}),
    (429, {"ok": False, "error": "Слишком много попыток. Попробуйте через 5 мин.", "retryAfterSeconds": 300}),
])
@pytest.mark.asyncio
async def test_staff_call_keeps_error_body(monkeypatch, code, payload):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(
            request.full_url, code, "err", {}, io.BytesIO(json.dumps(payload).encode("utf-8"))
        )

    monkeypatch.setattr("smilekit_bot.crm.urllib.request.urlopen", fake_urlopen)
    assert await CrmClient("https://crm.example", "tok").staff_call(STAFF_LOGIN_PATH, {}) == (code, payload)


@pytest.mark.asyncio
async def test_staff_call_is_none_when_crm_unreachable(monkeypatch):
    def timeout(request, timeout=None):
        raise TimeoutError("timed out")

    def server_error(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 502, "bad gateway", {}, io.BytesIO(b"<html>"))

    client = CrmClient("https://crm.example", "tok")
    monkeypatch.setattr("smilekit_bot.crm.urllib.request.urlopen", timeout)
    assert await client.staff_call(STAFF_STATE_PATH, {}) is None
    monkeypatch.setattr("smilekit_bot.crm.urllib.request.urlopen", server_error)
    assert await client.staff_call(STAFF_STATE_PATH, {}) is None
    assert await CrmClient("", "").staff_call(STAFF_STATE_PATH, {}) is None


# ─── /internal/staff/notify ─────────────────────────────────────────────────


class NotifyBot:
    def __init__(self, error: Exception | None = None):
        self.sent = []
        self.error = error

    async def send_message(self, chat_id, text, **kwargs):
        if self.error:
            raise self.error
        self.sent.append((chat_id, text, kwargs.get("reply_markup")))


@pytest.mark.asyncio
async def test_notify_sends_message_with_buttons_one_per_row(tmp_path):
    bot = NotifyBot()
    async with running_client(tmp_path, bot=bot) as (client, storage, settings):
        resp = await client.post(
            "/internal/staff/notify",
            headers={"x-internal-token": TOKEN},
            json={
                "telegramId": 555,
                "text": "Бакыт просит «Моя смена»",
                "buttons": [
                    {"text": "✅ Подтвердить", "callbackData": "staff:approve:r1"},
                    {"text": "❌ Отклонить", "callbackData": "staff:reject:r1"},
                ],
            },
        )
        assert resp.status == 200
        assert await resp.json() == {"ok": True}
    chat_id, text, markup = bot.sent[0]
    assert (chat_id, text) == (555, "Бакыт просит «Моя смена»")
    assert [[b.callback_data for b in row] for row in markup.inline_keyboard] == [
        ["staff:approve:r1"], ["staff:reject:r1"]
    ]


@pytest.mark.asyncio
async def test_notify_without_buttons(tmp_path):
    bot = NotifyBot()
    async with running_client(tmp_path, bot=bot) as (client, storage, settings):
        resp = await client.post(
            "/internal/staff/notify",
            headers={"x-internal-token": TOKEN},
            json={"telegramId": 555, "text": "Смена началась"},
        )
        assert resp.status == 200
    assert bot.sent == [(555, "Смена началась", None)]


@pytest.mark.parametrize("body", [
    {"text": "hi"},
    {"telegramId": "555", "text": "hi"},
    {"telegramId": True, "text": "hi"},
    {"telegramId": 555, "text": "   "},
    {"telegramId": 555, "text": "x" * 4001},
    {"telegramId": 555, "text": "hi", "buttons": [{"text": "b", "callbackData": "c"}] * 7},
    {"telegramId": 555, "text": "hi", "buttons": [{"text": "b", "callbackData": "я" * 33}]},
    {"telegramId": 555, "text": "hi", "buttons": [{"text": "", "callbackData": "c"}]},
    {"telegramId": 555, "text": "hi", "buttons": "nope"},
])
@pytest.mark.asyncio
async def test_notify_validation(tmp_path, body):
    bot = NotifyBot()
    async with running_client(tmp_path, bot=bot) as (client, storage, settings):
        resp = await client.post(
            "/internal/staff/notify", headers={"x-internal-token": TOKEN}, json=body
        )
        assert resp.status == 400
        assert "error" in await resp.json()
    assert bot.sent == []


@pytest.mark.asyncio
async def test_notify_requires_token(tmp_path):
    async with running_client(tmp_path, bot=NotifyBot()) as (client, storage, settings):
        resp = await client.post("/internal/staff/notify", json={"telegramId": 1, "text": "hi"})
        assert resp.status == 403


@pytest.mark.asyncio
async def test_notify_telegram_failure_is_502(tmp_path):
    error = TelegramForbiddenError(method=None, message="Forbidden: bot was blocked by the user")
    async with running_client(tmp_path, bot=NotifyBot(error)) as (client, storage, settings):
        resp = await client.post(
            "/internal/staff/notify",
            headers={"x-internal-token": TOKEN},
            json={"telegramId": 555, "text": "hi"},
        )
        assert resp.status == 502
        assert await resp.json() == {"error": "Forbidden: bot was blocked by the user"}
