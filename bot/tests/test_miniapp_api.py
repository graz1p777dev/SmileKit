"""HTTP-слой Mini App: авторизация, отметки, фото, награда, ИИ-чат."""

import json
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from smilekit_bot.api import create_app
from smilekit_bot.config import Settings
from smilekit_bot.miniapp import INIT_DATA_HEADER
from smilekit_bot.miniapp_store import PROCEDURE_DAYS, REWARD_PROCEDURES_REQUIRED
from smilekit_bot.service import SmilekitAssistant
from smilekit_bot.storage import BotStorage

from test_miniapp import BOT_TOKEN, init_data


class FakeResponses:
    def create(self, **kwargs):
        return SimpleNamespace(output_text="Кофе лучше отложить на два часа 💜")


class FakeClient:
    def __init__(self):
        self.responses = FakeResponses()


class FakeBot:
    def __init__(self):
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))
        return SimpleNamespace(message_id=len(self.sent) * 1000)


@asynccontextmanager
async def running_client(tmp_path, bot=None):
    knowledge = tmp_path / "knowledge_base.md"
    knowledge.write_text("[ЗАПОЛНИТЬ]", encoding="utf-8")
    settings = Settings(
        telegram_bot_token=BOT_TOKEN,
        openai_api_key="oa-key",
        database_path=str(tmp_path / "bot.db"),
    )
    storage = BotStorage(settings.database_path)
    assistant = SmilekitAssistant(api_key="test", knowledge_path=knowledge, client=FakeClient())
    app = create_app(settings, storage, assistant, bot)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        yield client, app
    finally:
        await client.close()
        app["app_store"].close()
        storage.close()


def auth_headers(user_id: int = 42) -> dict:
    return {INIT_DATA_HEADER: init_data(user_id)}


async def onboard(client, user_id: int = 42):
    return await client.post(
        "/app/api/onboarding",
        headers=auth_headers(user_id),
        json={"goal": "max", "drink": "coffee", "sensitivity": "no"},
    )


@pytest.mark.asyncio
async def test_state_requires_valid_init_data(tmp_path):
    async with running_client(tmp_path) as (client, _):
        assert (await client.get("/app/api/state")).status == 401
        assert (await client.get("/app/api/state", headers={INIT_DATA_HEADER: "hash=nope"})).status == 401
        assert (await client.get("/app/api/state", headers=auth_headers())).status == 200


@pytest.mark.asyncio
async def test_index_is_served_without_auth(tmp_path):
    """Саму страницу отдаём всем: initData проверяется на /app/api/*, а
    выдавать 401 на HTML значило бы показывать клиенту пустой экран."""
    async with running_client(tmp_path) as (client, _):
        resp = await client.get("/app")
        assert resp.status == 200
        assert "Моя программа SmileKit" in await resp.text()


@pytest.mark.asyncio
async def test_new_client_is_not_onboarded(tmp_path):
    async with running_client(tmp_path) as (client, _):
        state = await (await client.get("/app/api/state", headers=auth_headers())).json()
        assert state["user"]["onboarded"] is False
        assert state["course"]["day"] == 0
        assert state["user"]["first_name"] == "Алина"


@pytest.mark.asyncio
async def test_onboarding_starts_the_course(tmp_path):
    async with running_client(tmp_path) as (client, _):
        state = await (await onboard(client)).json()
        assert state["user"]["onboarded"] is True
        assert state["course"]["day"] == 1
        assert state["today"]["is_procedure_day"] is True
        assert [task["id"] for task in state["today"]["tasks"]][0] == "procedure"


@pytest.mark.asyncio
async def test_onboarding_rejects_unknown_values(tmp_path):
    async with running_client(tmp_path) as (client, _):
        resp = await client.post(
            "/app/api/onboarding",
            headers=auth_headers(),
            json={"goal": "hack", "drink": "coffee", "sensitivity": "no"},
        )
        assert resp.status == 400


@pytest.mark.asyncio
async def test_checkin_marks_the_day_and_can_be_undone(tmp_path):
    async with running_client(tmp_path) as (client, _):
        await onboard(client)
        state = await (await client.post(
            "/app/api/checkin", headers=auth_headers(), json={"day": 1, "tasks": ["procedure"]}
        )).json()
        assert state["today"]["done"] is True
        assert state["stats"]["procedures_done"] == 1

        state = await (await client.post(
            "/app/api/checkin", headers=auth_headers(), json={"day": 1, "undo": True}
        )).json()
        assert state["today"]["done"] is False


@pytest.mark.asyncio
async def test_checkin_drops_task_ids_that_are_not_in_the_plan(tmp_path):
    """Клиент присылает id задач сам — принимаем только те, что реально
    входят в план дня, иначе в истории оседал бы произвольный текст."""
    async with running_client(tmp_path) as (client, _):
        await onboard(client)
        state = await (await client.post(
            "/app/api/checkin",
            headers=auth_headers(),
            json={"day": 1, "tasks": ["procedure", "<script>", "sensitivity"]},
        )).json()
        assert state["check_ins"]["1"]["tasks"] == ["procedure"]


@pytest.mark.asyncio
async def test_checkin_rejects_days_outside_the_course(tmp_path):
    async with running_client(tmp_path) as (client, _):
        await onboard(client)
        resp = await client.post("/app/api/checkin", headers=auth_headers(), json={"day": 99})
        assert resp.status == 400


@pytest.mark.asyncio
async def test_clients_do_not_see_each_others_progress(tmp_path):
    async with running_client(tmp_path) as (client, _):
        await onboard(client, user_id=42)
        await onboard(client, user_id=43)
        await client.post("/app/api/checkin", headers=auth_headers(42), json={"day": 1, "tasks": []})
        other = await (await client.get("/app/api/state", headers=auth_headers(43))).json()
        assert other["check_ins"] == {}


@pytest.mark.asyncio
async def test_reward_is_locked_until_the_course_is_done(tmp_path):
    async with running_client(tmp_path) as (client, _):
        await onboard(client)
        resp = await client.post("/app/api/reward", headers=auth_headers(), json={})
        assert resp.status == 403


@pytest.mark.asyncio
async def test_reward_is_issued_and_sent_to_the_chat(tmp_path):
    bot = FakeBot()
    async with running_client(tmp_path, bot=bot) as (client, _):
        await onboard(client)
        for day in PROCEDURE_DAYS[:REWARD_PROCEDURES_REQUIRED]:
            await client.post("/app/api/checkin", headers=auth_headers(), json={"day": day, "tasks": []})
        state = await (await client.post("/app/api/reward", headers=auth_headers(), json={})).json()
        assert state["reward"]["unlocked"] is True
        assert state["reward"]["code"].startswith("SMILE")
        assert state["reward"]["code"] in bot.sent[0][1]


@pytest.mark.asyncio
async def test_photo_upload_download_and_delete(tmp_path):
    async with running_client(tmp_path) as (client, _):
        await onboard(client)
        form = {"day": "1", "photo": b"\xff\xd8fake-jpeg"}
        state = await (await client.post("/app/api/photo", headers=auth_headers(), data=form)).json()
        assert len(state["photos"]) == 1

        url = state["photos"][0]["url"]
        assert (await client.get(url)).status == 200
        assert (await client.get(url.replace(".jpg", "x.jpg"))).status == 404

        state = await (await client.delete("/app/api/photo/1", headers=auth_headers())).json()
        assert state["photos"] == []


@pytest.mark.asyncio
async def test_reading_articles_raises_the_score(tmp_path):
    async with running_client(tmp_path) as (client, _):
        await onboard(client)
        before = await (await client.get("/app/api/state", headers=auth_headers())).json()
        for index in range(5):
            state = await (await client.post(
                "/app/api/read", headers=auth_headers(), json={"article_id": f"article-{index}"}
            )).json()
        assert len(state["reads"]) == 5
        assert state["score"] > before["score"]


@pytest.mark.asyncio
async def test_support_ticket_reaches_the_chat_with_context(tmp_path):
    bot = FakeBot()
    async with running_client(tmp_path, bot=bot) as (client, _):
        await onboard(client)
        resp = await client.post(
            "/app/api/support",
            headers=auth_headers(),
            json={"topic": "sensitivity", "text": "Немного ноет после второй полоски"},
        )
        assert resp.status == 200
        assert "Появилась чувствительность" in bot.sent[0][1]
        assert "День курса: 1" in bot.sent[0][1]
        assert "Немного ноет" in bot.sent[0][1]


@pytest.mark.asyncio
async def test_support_rejects_unknown_topics(tmp_path):
    async with running_client(tmp_path, bot=FakeBot()) as (client, _):
        resp = await client.post("/app/api/support", headers=auth_headers(), json={"topic": "spam"})
        assert resp.status == 400


@pytest.mark.asyncio
async def test_ask_answers_and_keeps_history(tmp_path):
    async with running_client(tmp_path) as (client, _):
        await onboard(client)
        answer = await (await client.post(
            "/app/api/ask", headers=auth_headers(), json={"text": "Можно ли пить кофе?"}
        )).json()
        assert answer["answer"] == "Кофе лучше отложить на два часа 💜"

        history = await (await client.get("/app/api/ask", headers=auth_headers())).json()
        assert [item["role"] for item in history["messages"]] == ["user", "assistant"]

        await client.delete("/app/api/ask", headers=auth_headers())
        history = await (await client.get("/app/api/ask", headers=auth_headers())).json()
        assert history["messages"] == []


@pytest.mark.asyncio
async def test_ask_rejects_an_empty_question(tmp_path):
    async with running_client(tmp_path) as (client, _):
        await onboard(client)
        resp = await client.post("/app/api/ask", headers=auth_headers(), json={"text": "   "})
        assert resp.status == 400


@pytest.mark.asyncio
async def test_settings_toggle_reminders(tmp_path):
    async with running_client(tmp_path) as (client, app):
        await onboard(client)
        state = await (await client.post(
            "/app/api/settings", headers=auth_headers(), json={"reminders_on": False}
        )).json()
        assert state["user"]["reminders_on"] is False
        assert app["app_store"].due_reminders(time.time() + 86400) == []

        state = await (await client.post(
            "/app/api/settings", headers=auth_headers(), json={"reminders_on": True, "reminder_hour": 9}
        )).json()
        assert state["user"]["reminder_hour"] == 9


@pytest.mark.asyncio
async def test_restart_resets_progress(tmp_path):
    async with running_client(tmp_path) as (client, _):
        await onboard(client)
        await client.post("/app/api/checkin", headers=auth_headers(), json={"day": 1, "tasks": []})
        state = await (await client.post("/app/api/restart", headers=auth_headers(), json={})).json()
        assert state["check_ins"] == {}
        assert state["course"]["day"] == 1
        assert state["user"]["goal"] == "max"


@pytest.mark.asyncio
async def test_quiz_score_is_capped_by_total(tmp_path):
    async with running_client(tmp_path) as (client, _):
        await onboard(client)
        state = await (await client.post(
            "/app/api/quiz", headers=auth_headers(), json={"score": 99, "total": 5}
        )).json()
        assert state["quiz"]["best"] == 5


@pytest.mark.asyncio
async def test_every_app_endpoint_rejects_missing_init_data(tmp_path):
    async with running_client(tmp_path, bot=FakeBot()) as (client, _):
        calls = [
            ("get", "/app/api/state", None),
            ("post", "/app/api/onboarding", {}),
            ("post", "/app/api/checkin", {}),
            ("post", "/app/api/read", {}),
            ("post", "/app/api/quiz", {}),
            ("post", "/app/api/settings", {}),
            ("post", "/app/api/restart", {}),
            ("post", "/app/api/reward", {}),
            ("post", "/app/api/support", {}),
            ("post", "/app/api/ask", {}),
            ("get", "/app/api/ask", None),
            ("delete", "/app/api/ask", None),
            ("delete", "/app/api/photo/1", None),
        ]
        for method, path, body in calls:
            resp = await getattr(client, method)(path, **({"json": body} if body is not None else {}))
            assert resp.status == 401, f"{method.upper()} {path} отдал {resp.status}"


@pytest.mark.asyncio
async def test_onboarding_reply_route_is_registered(tmp_path):
    """Маршрут /onboarding/reply один раз уже был написан, но не зарегистрирован:
    статика перехватывала путь и отдавала 405, а онбординг молча падал на
    кнопочный запасной вариант."""
    async with running_client(tmp_path) as (client, _):
        resp = await client.post(
            "/app/api/onboarding/reply",
            headers=auth_headers(),
            json={"step": "drink", "text": "кофе литрами"},
        )
        assert resp.status == 200
        body = await resp.json()
        assert set(body) == {"value", "ack"}


@pytest.mark.asyncio
async def test_onboarding_reply_rejects_unknown_step(tmp_path):
    async with running_client(tmp_path) as (client, _):
        resp = await client.post(
            "/app/api/onboarding/reply", headers=auth_headers(), json={"step": "hack", "text": "x"}
        )
        assert resp.status == 400
