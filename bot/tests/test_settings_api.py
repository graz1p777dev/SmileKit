from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from smilekit_bot.api import MAX_PROMPT_LENGTH, create_app, effective_settings
from smilekit_bot.config import Settings
from smilekit_bot.models import CrmAction, SupervisorResult
from smilekit_bot.prompts import MANAGER_PROMPT, SUPERVISOR_PROMPT
from smilekit_bot.service import SmilekitAssistant
from smilekit_bot.storage import BotStorage

TOKEN = "test-internal-token"


class FakeResponses:
    def create(self, **kwargs):
        return SimpleNamespace(output_text="Лабораторный черновик")

    def parse(self, **kwargs):
        review = SupervisorResult(status="approved", risk_level="low", crm_action=CrmAction())
        return SimpleNamespace(output_parsed=review)


class FakeClient:
    def __init__(self):
        self.responses = FakeResponses()


def make_settings(**overrides) -> Settings:
    values = {
        "telegram_bot_token": "tg-token",
        "openai_api_key": "oa-key",
        "bot_internal_token": TOKEN,
    }
    values.update(overrides)
    return Settings(**values)


def make_storage(tmp_path) -> BotStorage:
    return BotStorage(str(tmp_path / "bot.db"))


def make_assistant(tmp_path) -> SmilekitAssistant:
    knowledge = tmp_path / "knowledge_base.md"
    knowledge.write_text("[ЗАПОЛНИТЬ]", encoding="utf-8")
    return SmilekitAssistant(api_key="test", knowledge_path=knowledge, client=FakeClient())


class FakeBot:
    def __init__(self):
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))
        return SimpleNamespace(message_id=len(self.sent) * 1000)


@asynccontextmanager
async def running_client(tmp_path, bot=None, **setting_overrides):
    """Поднимает api.create_app на тестовом сервере в текущем event loop.

    Не используем aiohttp.pytest_plugin/aiohttp_client: он заводит свой
    event_loop, конфликтующий с pytest-asyncio (уже используется во всём
    остальном наборе тестов) и падает с "Timeout context manager should be
    used inside a task". TestServer/TestClient напрямую работают в текущем
    loop без плагина.
    """
    settings = make_settings(**setting_overrides)
    storage = make_storage(tmp_path)
    assistant = make_assistant(tmp_path)
    app = create_app(settings, storage, assistant, bot)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    try:
        yield client, storage, settings
    finally:
        await client.close()
        storage.close()


@pytest.mark.asyncio
async def test_health_does_not_require_auth(tmp_path):
    async with running_client(tmp_path) as (client, storage, settings):
        resp = await client.get("/internal/health")
        assert resp.status == 200


@pytest.mark.asyncio
async def test_settings_rejects_missing_token(tmp_path):
    async with running_client(tmp_path) as (client, storage, settings):
        resp = await client.get("/internal/settings")
        assert resp.status == 403


@pytest.mark.asyncio
async def test_settings_rejects_wrong_token(tmp_path):
    async with running_client(tmp_path) as (client, storage, settings):
        resp = await client.get("/internal/settings", headers={"x-internal-token": "wrong"})
        assert resp.status == 403


@pytest.mark.asyncio
async def test_settings_rejects_when_no_token_configured(tmp_path):
    # bot_internal_token пустой -> вход запрещён для всех, даже с пустым заголовком.
    async with running_client(tmp_path, bot_internal_token="") as (client, storage, settings):
        resp = await client.get("/internal/settings", headers={"x-internal-token": ""})
        assert resp.status == 403


@pytest.mark.asyncio
async def test_get_settings_returns_builtin_defaults(tmp_path):
    async with running_client(tmp_path) as (client, storage, settings):
        resp = await client.get("/internal/settings", headers={"x-internal-token": TOKEN})
        assert resp.status == 200
        data = await resp.json()
        assert data["managerPrompt"] == MANAGER_PROMPT
        assert data["managerPromptIsDefault"] is True
        assert data["supervisorPrompt"] == SUPERVISOR_PROMPT
        assert data["supervisorPromptIsDefault"] is True
        assert data["model"] == settings.openai_model
        assert data["models"] == [settings.openai_model]


@pytest.mark.asyncio
async def test_put_then_get_roundtrip_persists_override(tmp_path):
    async with running_client(tmp_path) as (client, storage, settings):
        put_resp = await client.put(
            "/internal/settings",
            headers={"x-internal-token": TOKEN},
            json={"managerPrompt": "Кастомный промпт менеджера"},
        )
        assert put_resp.status == 200
        put_data = await put_resp.json()
        assert put_data["managerPrompt"] == "Кастомный промпт менеджера"
        assert put_data["managerPromptIsDefault"] is False
        # supervisorPrompt не передавался -> остаётся дефолтом.
        assert put_data["supervisorPrompt"] == SUPERVISOR_PROMPT
        assert put_data["supervisorPromptIsDefault"] is True

        get_resp = await client.get("/internal/settings", headers={"x-internal-token": TOKEN})
        get_data = await get_resp.json()
        assert get_data["managerPrompt"] == "Кастомный промпт менеджера"
        assert get_data["managerPromptIsDefault"] is False

        # Прямая проверка storage: override реально лежит в SQLite, а не только в памяти ответа.
        assert storage.get_bot_setting("manager_prompt") == "Кастомный промпт менеджера"


@pytest.mark.asyncio
async def test_override_survives_reopening_storage(tmp_path):
    async with running_client(tmp_path) as (client, storage, settings):
        await client.put(
            "/internal/settings",
            headers={"x-internal-token": TOKEN},
            json={"supervisorPrompt": "Кастомный промпт супервизора"},
        )

    reopened = make_storage(tmp_path)
    assert reopened.get_bot_setting("supervisor_prompt") == "Кастомный промпт супервизора"
    reopened.close()


@pytest.mark.asyncio
async def test_put_truncates_overlong_prompt(tmp_path):
    async with running_client(tmp_path) as (client, storage, settings):
        long_prompt = "А" * (MAX_PROMPT_LENGTH + 500)
        resp = await client.put(
            "/internal/settings",
            headers={"x-internal-token": TOKEN},
            json={"managerPrompt": long_prompt},
        )
        data = await resp.json()
        assert len(data["managerPrompt"]) == MAX_PROMPT_LENGTH
        assert "managerPrompt" in data["truncated"]


def test_effective_settings_truncates_legacy_overlong_overrides(tmp_path):
    storage = make_storage(tmp_path)
    storage.set_bot_setting("manager_prompt", "А" * (MAX_PROMPT_LENGTH + 500))
    storage.set_bot_setting("supervisor_prompt", "Б" * (MAX_PROMPT_LENGTH + 500))

    data = effective_settings(storage, make_settings())

    assert len(data["managerPrompt"]) == MAX_PROMPT_LENGTH
    assert len(data["supervisorPrompt"]) == MAX_PROMPT_LENGTH
    storage.close()


@pytest.mark.asyncio
async def test_put_empty_prompt_falls_back_to_default(tmp_path):
    async with running_client(tmp_path) as (client, storage, settings):
        await client.put(
            "/internal/settings",
            headers={"x-internal-token": TOKEN},
            json={"managerPrompt": "Временный override"},
        )
        resp = await client.put(
            "/internal/settings",
            headers={"x-internal-token": TOKEN},
            json={"managerPrompt": "   "},
        )
        data = await resp.json()
        assert data["managerPrompt"] == MANAGER_PROMPT
        assert data["managerPromptIsDefault"] is True


@pytest.mark.asyncio
async def test_put_rejects_unknown_model(tmp_path):
    async with running_client(tmp_path) as (client, storage, settings):
        resp = await client.put(
            "/internal/settings",
            headers={"x-internal-token": TOKEN},
            json={"model": "not-a-real-model"},
        )
        assert resp.status == 400


@pytest.mark.asyncio
async def test_lab_requires_auth(tmp_path):
    async with running_client(tmp_path) as (client, storage, settings):
        resp = await client.post("/internal/lab", json={"message": "Привет"})
        assert resp.status == 403


@pytest.mark.asyncio
async def test_lab_returns_reply_and_never_touches_persistent_tables(tmp_path):
    async with running_client(tmp_path) as (client, storage, settings):

        def counts():
            return {
                table: storage._db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("messages", "customer_memory", "orders", "sent_events")
            }

        before = counts()
        resp = await client.post(
            "/internal/lab",
            headers={"x-internal-token": TOKEN},
            json={"message": "Как правильно применять полоски?", "history": []},
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["reply"] == "Лабораторный черновик"
        assert data["supervisor"]["status"] == "approved"
        after = counts()
        assert before == after


@pytest.mark.asyncio
async def test_lab_accepts_history(tmp_path):
    async with running_client(tmp_path) as (client, storage, settings):
        resp = await client.post(
            "/internal/lab",
            headers={"x-internal-token": TOKEN},
            json={
                "message": "А доставка?",
                "history": [
                    {"role": "user", "content": "Привет"},
                    {"role": "assistant", "content": "Здравствуйте"},
                ],
            },
        )
        assert resp.status == 200


@pytest.mark.asyncio
async def test_chat_status_defaults_to_not_paused(tmp_path):
    async with running_client(tmp_path) as (client, storage, settings):
        resp = await client.get("/internal/chats/42/status", headers={"x-internal-token": TOKEN})
        assert resp.status == 200
        assert (await resp.json()) == {"paused": False}


@pytest.mark.asyncio
async def test_chat_pause_and_resume(tmp_path):
    async with running_client(tmp_path) as (client, storage, settings):
        resp = await client.post("/internal/chats/42/pause", headers={"x-internal-token": TOKEN})
        assert resp.status == 200
        assert (await resp.json()) == {"paused": True}
        assert storage.is_paused(42)

        resp = await client.get("/internal/chats/42/status", headers={"x-internal-token": TOKEN})
        assert (await resp.json()) == {"paused": True}

        resp = await client.post("/internal/chats/42/resume", headers={"x-internal-token": TOKEN})
        assert resp.status == 200
        assert (await resp.json()) == {"paused": False}
        assert not storage.is_paused(42)


@pytest.mark.asyncio
async def test_chat_message_sends_and_pauses(tmp_path):
    bot = FakeBot()
    async with running_client(tmp_path, bot=bot) as (client, storage, settings):
        resp = await client.post(
            "/internal/chats/42/message",
            headers={"x-internal-token": TOKEN},
            json={"text": "Здравствуйте, это Айгерим, помогу с заказом"},
        )
        assert resp.status == 200
        assert (await resp.json()) == {"paused": True}
        assert bot.sent == [(42, "Здравствуйте, это Айгерим, помогу с заказом")]
        assert storage.is_paused(42)
        history = storage.history(42)
        assert history[-1].role == "assistant"
        assert history[-1].content == "Здравствуйте, это Айгерим, помогу с заказом"


@pytest.mark.asyncio
async def test_chat_message_without_bot_returns_503(tmp_path):
    async with running_client(tmp_path) as (client, storage, settings):
        resp = await client.post(
            "/internal/chats/42/message",
            headers={"x-internal-token": TOKEN},
            json={"text": "Привет"},
        )
        assert resp.status == 503


@pytest.mark.asyncio
async def test_chat_message_requires_text(tmp_path):
    bot = FakeBot()
    async with running_client(tmp_path, bot=bot) as (client, storage, settings):
        resp = await client.post(
            "/internal/chats/42/message",
            headers={"x-internal-token": TOKEN},
            json={},
        )
        assert resp.status == 400


@pytest.mark.asyncio
async def test_events_endpoint_returns_newest_first_and_filters_since_id(tmp_path):
    async with running_client(tmp_path) as (client, storage, settings):
        storage.log_event(1, "info", "reply", "reply.ai_generated", "Первое")
        storage.log_event(2, "warn", "order", "order.receipt_mismatch", "Второе")

        resp = await client.get("/internal/events", headers={"x-internal-token": TOKEN})
        assert resp.status == 200
        data = await resp.json()
        assert [e["event"] for e in data["events"]] == ["order.receipt_mismatch", "reply.ai_generated"]

        newest_id = data["events"][0]["id"]
        resp2 = await client.get(f"/internal/events?sinceId={newest_id}", headers={"x-internal-token": TOKEN})
        data2 = await resp2.json()
        assert data2["events"] == []


@pytest.mark.asyncio
async def test_events_endpoint_requires_auth(tmp_path):
    async with running_client(tmp_path) as (client, storage, settings):
        resp = await client.get("/internal/events")
        assert resp.status == 403
