import json

import pytest

from smilekit_bot.crm import (
    DEALS_PATH,
    ORDER_PATH,
    CrmClient,
    build_deal_payload,
    build_order_payload,
    order_fingerprint,
    stage_action_for_inbound,
)
from smilekit_bot.models import OrderDetails, SupervisorResult
from smilekit_bot.sales import MANAGER_CONTACT


def supervisor(order_summary="1 коробка, Бишкек", **order_fields) -> SupervisorResult:
    return SupervisorResult(
        status="approved",
        risk_level="low",
        order_summary=order_summary,
        order=OrderDetails(**order_fields),
    )


# ─── Сборка тела запроса ────────────────────────────────────────────────────


def test_no_payload_until_order_started():
    assert build_order_payload(1, supervisor(order_summary="")) is None
    assert build_order_payload(1, supervisor(order_summary="   ")) is None


def test_payload_carries_structured_fields():
    payload = build_order_payload(
        555,
        supervisor(
            quantity=2,
            total_amount=2980,
            currency="kgs",
            city="Бишкек",
            contact_phone="0700123456",
            contact_username="@client",
        ),
        customer_name="Айгуль",
        telegram_username="tg_client",
    )
    assert payload["telegramChatId"] == 555
    assert payload["source"] == "telegram_bot"
    assert payload["quantity"] == 2
    assert payload["totalAmount"] == 2980.0
    assert payload["currency"] == "KGS"
    assert payload["city"] == "Бишкек"
    assert payload["customerPhone"] == "0700123456"
    assert payload["customerUsername"] == "client"
    assert payload["customerName"] == "Айгуль"
    assert payload["externalKey"].startswith("telegram:555:order:")


def test_unknown_fields_stay_null():
    payload = build_order_payload(7, supervisor(contact_phone="0700123456"))
    assert payload["quantity"] is None
    assert payload["totalAmount"] is None
    assert payload["currency"] is None
    assert payload["city"] is None
    assert payload["summary"] == "1 коробка, Бишкек"


def test_invalid_values_are_dropped_not_guessed():
    payload = build_order_payload(
        7,
        supervisor(quantity=0, total_amount=-5, currency="EUR", city="   ", contact_phone="0700123456"),
    )
    assert payload["quantity"] is None
    assert payload["totalAmount"] is None
    assert payload["currency"] is None
    assert payload["city"] is None


def test_manager_phone_never_becomes_customer_phone():
    payload = build_order_payload(7, supervisor(contact_phone=f"+996 {MANAGER_CONTACT[1:]}"))
    assert payload is None


def test_no_payload_until_phone_is_collected():
    """order_summary иногда заполняется рано (клиент просто согласился купить,
    телефон ещё не назвал) — без реального телефона платёж запрашивать рано."""
    assert build_order_payload(7, supervisor()) is None


def test_telegram_username_is_the_fallback_contact():
    payload = build_order_payload(7, supervisor(contact_phone="0700123456"), telegram_username="fallback")
    assert payload["customerUsername"] == "fallback"


# ─── Идемпотентность ────────────────────────────────────────────────────────


def test_same_order_gives_same_key_even_if_summary_rephrased():
    fields = dict(quantity=1, total_amount=1490, currency="KGS", city="Бишкек", contact_phone="0700123456")
    first = build_order_payload(42, supervisor("1 коробка в Бишкек", **fields))
    second = build_order_payload(42, supervisor("Заказ: одна коробка, Бишкек", **fields))
    assert first["externalKey"] == second["externalKey"]


def test_changed_order_gives_new_key():
    one = order_fingerprint(OrderDetails(quantity=1, city="Бишкек"))
    two = order_fingerprint(OrderDetails(quantity=2, city="Бишкек"))
    assert one != two


def test_key_is_per_chat():
    order = OrderDetails(quantity=1, city="Бишкек", contact_phone="0700123456")
    first = build_order_payload(1, supervisor(quantity=1, city="Бишкек", contact_phone="0700123456"))
    second = build_order_payload(2, supervisor(quantity=1, city="Бишкек", contact_phone="0700123456"))
    assert order_fingerprint(order) in first["externalKey"]
    assert first["externalKey"] != second["externalKey"]


# ─── Отправка ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_client_is_disabled_without_settings():
    assert not CrmClient("", "").enabled
    assert not CrmClient("https://crm.example", "").enabled
    assert await CrmClient("", "").send_order({"externalKey": "x"}) is False


@pytest.mark.asyncio
async def test_send_order_posts_json_with_token(monkeypatch):
    client = CrmClient("https://crm.example/", "secret")
    captured = {}

    def fake_request(method, path, payload):
        captured["payload"] = payload

    monkeypatch.setattr(client, "_request", fake_request)
    assert await client.send_order({"externalKey": "telegram:1:order:abc"}) is True
    assert captured["payload"]["externalKey"] == "telegram:1:order:abc"


@pytest.mark.asyncio
async def test_send_order_builds_correct_request(monkeypatch):
    client = CrmClient("https://crm.example/", "secret")
    seen = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b"{}"

    def fake_urlopen(request, timeout=None):
        seen["url"] = request.full_url
        seen["token"] = request.get_header("X-internal-token")
        seen["body"] = json.loads(request.data)
        return FakeResponse()

    monkeypatch.setattr("smilekit_bot.crm.urllib.request.urlopen", fake_urlopen)
    await client.send_order({"externalKey": "k"})

    assert seen["url"] == f"https://crm.example{ORDER_PATH}"
    assert seen["token"] == "secret"
    assert seen["body"] == {"externalKey": "k"}


@pytest.mark.asyncio
async def test_dead_crm_never_raises(monkeypatch, caplog):
    client = CrmClient("https://crm.example", "secret")

    def boom(method, path, payload):
        raise OSError("connection refused")

    monkeypatch.setattr(client, "_request", boom)
    assert await client.send_order({"externalKey": "k"}) is False
    assert "CRM order delivery failed" in caplog.text


# ─── Продвижение по воронке ─────────────────────────────────────────────────


def test_stage_action_ready_to_buy_wins_over_other_signals():
    # "хочу" совпал бы с interest_confirmed, но явное решение купить сильнее.
    assert stage_action_for_inbound("Хочу оформить заказ") == "ready_to_buy"
    assert stage_action_for_inbound("беру 2 коробки") == "ready_to_buy"


def test_stage_action_interest_confirmed():
    assert stage_action_for_inbound("Хочу попробовать") == "interest_confirmed"
    assert stage_action_for_inbound("давайте, меня устраивает") == "interest_confirmed"


def test_stage_action_need_identified():
    assert stage_action_for_inbound("Сколько стоит коробка полосок?") == "need_identified"
    assert stage_action_for_inbound("Какой состав у продукта?") == "need_identified"


def test_stage_action_none_for_unrelated_text():
    assert stage_action_for_inbound("Привет") is None
    assert stage_action_for_inbound("") is None
    assert stage_action_for_inbound(None) is None


def test_build_deal_payload_strips_at_from_username():
    payload = build_deal_payload(42, customer_name="Тест Тестов", telegram_username="@testov")
    assert payload == {
        "externalKey": "telegram:42",
        "source": "telegram",
        "customerName": "Тест Тестов",
        "customerUsername": "testov",
    }


@pytest.mark.asyncio
async def test_create_deal_posts_to_deals_path(monkeypatch):
    client = CrmClient("https://crm.example/", "secret")
    seen = {}

    def fake_request(method, path, payload):
        seen["method"], seen["path"], seen["payload"] = method, path, payload

    monkeypatch.setattr(client, "_request", fake_request)
    assert await client.create_deal(build_deal_payload(1)) is True
    assert seen["method"] == "POST"
    assert seen["path"] == DEALS_PATH
    assert seen["payload"]["externalKey"] == "telegram:1"


@pytest.mark.asyncio
async def test_advance_stage_patches_deals_path(monkeypatch):
    client = CrmClient("https://crm.example/", "secret")
    seen = {}

    def fake_request(method, path, payload):
        seen["method"], seen["path"], seen["payload"] = method, path, payload

    monkeypatch.setattr(client, "_request", fake_request)
    assert await client.advance_stage(7, "ready_to_buy") is True
    assert seen["method"] == "PATCH"
    assert seen["path"] == DEALS_PATH
    assert seen["payload"] == {"externalKey": "telegram:7", "action": "ready_to_buy"}


@pytest.mark.asyncio
async def test_advance_stage_ignores_404_without_warning(monkeypatch, caplog):
    import urllib.error

    client = CrmClient("https://crm.example", "secret")

    def not_found(method, path, payload):
        raise urllib.error.HTTPError("url", 404, "Not Found", {}, None)

    monkeypatch.setattr(client, "_request", not_found)
    assert await client.advance_stage(1, "ready_to_buy") is False
    assert "CRM rejected stage advance" not in caplog.text


@pytest.mark.asyncio
async def test_advance_stage_disabled_without_settings():
    assert await CrmClient("", "").advance_stage(1, "ready_to_buy") is False
