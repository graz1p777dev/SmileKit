"""HTTP-слой новой логики WhatsApp: CRM спрашивает, отправляет, подтверждает."""

import pytest

from smilekit_bot.wa import rules as R
from smilekit_bot.wa.brain import AskedQuestion, ImageCheck, Understanding, Written
from tests.test_settings_api import TOKEN, running_client

HEADERS = {"x-internal-token": TOKEN}
CHAT = "996500000000"


class FakeBrain:
    def __init__(self):
        self.next = Understanding()

    def understand(self, context):
        return self.next

    def write(self, role, context):
        return Written(messages=["ответ"], answered=[q["n"] for q in context["questions"]])

    def check_image(self, image):
        return image if isinstance(image, ImageCheck) else ImageCheck(kind="unreadable")


async def post(client, path, **body):
    response = await client.post(f"/internal/wa/{path}", json=body, headers=HEADERS)
    assert response.status == 200, await response.text()
    return await response.json()


def use_fake_brain(client) -> FakeBrain:
    service = client.app["wa"]
    service.brain = service.engine.brain = FakeBrain()
    return service.brain


@pytest.mark.asyncio
async def test_requires_internal_token(tmp_path):
    async with running_client(tmp_path, database_path=str(tmp_path / "bot.db")) as (client, _, _):
        response = await client.post("/internal/wa/handle", json={"chatId": CHAT, "messages": []})
        assert response.status == 403


@pytest.mark.asyncio
async def test_handle_send_confirm_cycle(tmp_path):
    async with running_client(tmp_path, database_path=str(tmp_path / "bot.db")) as (client, _, _):
        brain = use_fake_brain(client)
        brain.next = Understanding(intent="greeting")
        data = await post(client, "handle", chatId=CHAT, messages=[{"id": "m1", "text": "Здравствуйте"}])
        assert [out["body"] for out in data["outgoing"]] == [R.PRESENTATION, R.CITY_QUESTION]

        first, second = data["outgoing"]
        assert (await post(client, "can_send", chatId=CHAT, id=first["id"]))["ok"] is True
        await post(client, "delivered", chatId=CHAT, id=first["id"], ok=True)
        await post(client, "delivered", chatId=CHAT, id=second["id"], ok=True)

        state = await (await client.get("/internal/wa/state", params={"chatId": CHAT}, headers=HEADERS)).json()
        assert state["memory"]["presented"]["price"] is True
        # повтор того же вебхука ничего не отправляет
        assert (await post(client, "handle", chatId=CHAT, messages=[{"id": "m1", "text": "Здравствуйте"}]))["outgoing"] == []


@pytest.mark.asyncio
async def test_new_client_message_makes_pending_reply_stale(tmp_path):
    async with running_client(tmp_path, database_path=str(tmp_path / "bot.db")) as (client, _, _):
        brain = use_fake_brain(client)
        brain.next = Understanding(intent="greeting")
        first = (await post(client, "handle", chatId=CHAT, messages=[{"id": "m1", "text": "Привет"}]))["outgoing"][0]
        brain.next = Understanding(intent="question", questions=[AskedQuestion(text="цена?", topic="other")])
        await post(client, "handle", chatId=CHAT, messages=[{"id": "m2", "text": "а цена?"}])
        assert (await post(client, "can_send", chatId=CHAT, id=first["id"]))["ok"] is False


@pytest.mark.asyncio
async def test_reset_forgets_chat(tmp_path):
    async with running_client(tmp_path, database_path=str(tmp_path / "bot.db")) as (client, _, _):
        brain = use_fake_brain(client)
        brain.next = Understanding(intent="greeting")
        outs = (await post(client, "handle", chatId=CHAT, messages=[{"id": "m1", "text": "Привет"}]))["outgoing"]
        await post(client, "delivered", chatId=CHAT, id=outs[0]["id"], ok=True)
        await post(client, "control", chatId=CHAT, action="reset")
        again = (await post(client, "handle", chatId=CHAT, messages=[{"id": "m1", "text": "Привет"}]))["outgoing"]
        assert again[0]["body"] == R.PRESENTATION


@pytest.mark.asyncio
async def test_seeded_chat_gets_no_presentation_and_manager_mode_is_released(tmp_path):
    async with running_client(tmp_path, database_path=str(tmp_path / "bot.db")) as (client, _, _):
        brain = use_fake_brain(client)
        brain.next = Understanding(intent="wants_human")
        first = await post(client, "handle", chatId=CHAT, seed={"hasHistory": True}, messages=[{"id": "m1", "text": "позовите человека"}])
        assert first["outgoing"] == [] and first["cards"][0]["type"] == "handoff"
        await post(client, "card_failed", chatId=CHAT, type="handoff")
        again = await post(client, "tick")
        assert [card["type"] for card in again["cards"]] == ["handoff"]
        # CRM снова позвала движок — значит, чат возвращён боту
        brain.next = Understanding(intent="greeting")
        outs = (await post(client, "handle", chatId=CHAT, messages=[{"id": "m2", "text": "Здравствуйте"}]))["outgoing"]
        assert len(outs) == 1 and outs[0]["body"] != R.PRESENTATION


@pytest.mark.asyncio
async def test_tick_sends_at_most_three_reminders_and_backs_off_after_failure(tmp_path):
    async with running_client(tmp_path, database_path=str(tmp_path / "bot.db")) as (client, _, _):
        brain = use_fake_brain(client)
        brain.next = Understanding(intent="greeting")
        service = client.app["wa"]
        start = service_now = __import__("time").time()
        chats = [f"99650000{n:04d}" for n in range(8)]
        for chat in chats:
            outs = (await post(client, "handle", chatId=chat, messages=[{"id": "m1", "text": "Здравствуйте"}]))["outgoing"]
            for out in outs:
                await post(client, "delivered", chatId=chat, id=out["id"], ok=True)
        # все восемь клиентов молчат дольше получаса, сейчас день по Бишкеку
        for chat in chats:
            m = service.store.load(chat)
            m.wait.last_bot_sent_at = m.wait.last_client_at = start - 3600
            service.store.save(m)
        if R.is_quiet_hours(start):
            pytest.skip("ночь по Бишкеку — напоминания не уходят")
        first = (await post(client, "tick"))["outgoing"]
        assert len(first) == 3
        # сбой отправки: эти три чата не повторяются в следующую минуту
        for out in first:
            await post(client, "delivered", chatId=out["chatId"], id=out["id"], ok=False)
        second = (await post(client, "tick"))["outgoing"]
        assert len(second) == 3 and not {o["chatId"] for o in first} & {o["chatId"] for o in second}
        # ошибка сервиса «возможно доставлено» — считается отправленным, повтора нет
        for out in second:
            await post(client, "delivered", chatId=out["chatId"], id=out["id"], ok=True, uncertain=True)
        third = (await post(client, "tick"))["outgoing"]
        assert len(third) == 2


@pytest.mark.asyncio
async def test_parallel_requests_do_not_break_memory(tmp_path):
    """Инцидент 13.10: чтение памяти из цикла событий одновременно с рабочим
    потоком давало sqlite «cannot commit». Все операции идут по одной."""
    import asyncio

    async with running_client(tmp_path, database_path=str(tmp_path / "bot.db")) as (client, _, _):
        brain = use_fake_brain(client)
        brain.next = Understanding(intent="greeting")

        async def one(n: int) -> int:
            chat = f"99655500{n:04d}"
            data = await post(client, "handle", chatId=chat, messages=[{"id": "m1", "text": "Здравствуйте"}])
            statuses = []
            for out in data["outgoing"]:
                statuses.append((await client.post("/internal/wa/can_send", json={"chatId": chat, "id": out["id"]}, headers=HEADERS)).status)
                statuses.append((await client.post("/internal/wa/delivered", json={"chatId": chat, "id": out["id"], "ok": True}, headers=HEADERS)).status)
            statuses.append((await client.get("/internal/wa/state", params={"chatId": chat}, headers=HEADERS)).status)
            statuses.append((await client.post("/internal/wa/tick", json={}, headers=HEADERS)).status)
            return max(statuses)

        assert await asyncio.gather(*(one(n) for n in range(25))) == [200] * 25
        state = await (await client.get("/internal/wa/state", params={"chatId": "996555000007"}, headers=HEADERS)).json()
        assert state["memory"]["presented"]["price"] is True
