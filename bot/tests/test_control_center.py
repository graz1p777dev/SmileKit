import json

import pytest

from smilekit_bot import control_center


@pytest.fixture(autouse=True)
def _reset_control_center():
    """Модуль хранит состояние на уровне модуля (singleton) — сбрасываем
    его до и после каждого теста, чтобы тесты не зависели от порядка."""
    control_center.configure("", "")
    yield
    control_center.configure("", "")


# ─── Выключенный коннектор — no-op ─────────────────────────────────────────


def test_disabled_by_default():
    assert control_center.enabled() is False


@pytest.mark.asyncio
async def test_disabled_connector_never_calls_http(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("HTTP must not be called when connector is disabled")

    monkeypatch.setattr("smilekit_bot.control_center.urllib.request.urlopen", boom)

    await control_center.send_heartbeat(status="ok")
    await control_center.record_usage(provider="openai", model="gpt-5", input_tokens=1, output_tokens=1)
    await control_center.record_event(type="deal", title="x")
    await control_center.flush()

    assert control_center._usage_batch == []
    assert control_center._event_batch == []


@pytest.mark.asyncio
async def test_disabled_with_only_url_or_only_token_is_still_disabled(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("HTTP must not be called when connector is disabled")

    monkeypatch.setattr("smilekit_bot.control_center.urllib.request.urlopen", boom)

    control_center.configure("https://cc.example", "")
    assert control_center.enabled() is False
    control_center.configure("", "secret")
    assert control_center.enabled() is False


# ─── Heartbeat ──────────────────────────────────────────────────────────────


class _FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return b"{}"


@pytest.mark.asyncio
async def test_send_heartbeat_posts_expected_payload(monkeypatch):
    control_center.configure("https://cc.example/", "secret")
    seen = {}

    def fake_urlopen(request, timeout=None):
        seen["url"] = request.full_url
        seen["auth"] = request.get_header("Authorization")
        seen["body"] = json.loads(request.data)
        return _FakeResponse()

    monkeypatch.setattr("smilekit_bot.control_center.urllib.request.urlopen", fake_urlopen)

    await control_center.send_heartbeat(
        status="ok",
        environment="production",
        git_sha="abc123",
        deploy_source="github",
        uptime_seconds=42.0,
        security={"envPresent": ["OPENAI_API_KEY", "DATABASE_URL"]},
    )

    assert seen["url"] == f"https://cc.example{control_center.HEARTBEAT_PATH}"
    assert seen["auth"] == "Bearer secret"
    assert seen["body"] == {
        "status": "ok",
        "gitSha": "abc123",
        "environment": "production",
        "deploySource": "github",
        "uptimeSeconds": 42.0,
        "security": {"envPresent": ["OPENAI_API_KEY", "DATABASE_URL"]},
    }


@pytest.mark.asyncio
async def test_heartbeat_never_raises_on_network_failure(monkeypatch, caplog):
    control_center.configure("https://cc.example", "secret")

    def boom(request, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr("smilekit_bot.control_center.urllib.request.urlopen", boom)

    await control_center.send_heartbeat(status="down")
    assert "Control Center heartbeat failed" in caplog.text


# ─── Usage/events batching ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_record_usage_batches_in_memory_without_http(monkeypatch):
    control_center.configure("https://cc.example", "secret")

    def boom(*args, **kwargs):
        raise AssertionError("record_usage must not call HTTP directly")

    monkeypatch.setattr("smilekit_bot.control_center.urllib.request.urlopen", boom)

    await control_center.record_usage(
        provider="deepseek", model="deepseek-v4-flash", input_tokens=100, output_tokens=50, cost_usd=0.01,
    )
    assert control_center._usage_batch == [
        {
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "inputTokens": 100,
            "outputTokens": 50,
            "costUsd": 0.01,
        }
    ]


def test_record_usage_sync_is_thread_safe_entry_point(monkeypatch):
    control_center.configure("https://cc.example", "secret")
    control_center.record_usage_sync(provider="openai", model="gpt-5", input_tokens=10, output_tokens=5)
    assert control_center._usage_batch == [
        {"provider": "openai", "model": "gpt-5", "inputTokens": 10, "outputTokens": 5}
    ]


@pytest.mark.asyncio
async def test_record_event_batches_in_memory():
    control_center.configure("https://cc.example", "secret")
    await control_center.record_event(type="deal", level="info", title="Deal created in CRM", payload={"externalKey": "telegram:1"})
    assert control_center._event_batch == [
        {"type": "deal", "level": "info", "title": "Deal created in CRM", "payload": {"externalKey": "telegram:1"}}
    ]


def test_batch_is_capped_at_500_entries():
    control_center.configure("https://cc.example", "secret")
    for _ in range(510):
        control_center.record_usage_sync(provider="openai", model="gpt-5", input_tokens=1, output_tokens=1)
    assert len(control_center._usage_batch) == 500


@pytest.mark.asyncio
async def test_flush_sends_batches_as_events_array_and_clears_them(monkeypatch):
    control_center.configure("https://cc.example/", "secret")
    await control_center.record_usage(provider="openai", model="gpt-5", input_tokens=1, output_tokens=2)
    await control_center.record_event(type="order", title="Order delivered to CRM")

    calls = []

    def fake_urlopen(request, timeout=None):
        calls.append((request.full_url, json.loads(request.data)))
        return _FakeResponse()

    monkeypatch.setattr("smilekit_bot.control_center.urllib.request.urlopen", fake_urlopen)

    await control_center.flush()

    urls = {url for url, _ in calls}
    assert urls == {
        f"https://cc.example{control_center.USAGE_PATH}",
        f"https://cc.example{control_center.EVENTS_PATH}",
    }
    for _, body in calls:
        assert list(body.keys()) == ["events"]
        assert len(body["events"]) == 1
    assert control_center._usage_batch == []
    assert control_center._event_batch == []


@pytest.mark.asyncio
async def test_flush_with_empty_batches_makes_no_http_call(monkeypatch):
    control_center.configure("https://cc.example", "secret")

    def boom(*args, **kwargs):
        raise AssertionError("flush with nothing to send must not call HTTP")

    monkeypatch.setattr("smilekit_bot.control_center.urllib.request.urlopen", boom)
    await control_center.flush()


@pytest.mark.asyncio
async def test_flush_failure_drops_batch_without_raising(monkeypatch, caplog):
    control_center.configure("https://cc.example", "secret")
    await control_center.record_event(type="error", title="x")

    def boom(request, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr("smilekit_bot.control_center.urllib.request.urlopen", boom)

    await control_center.flush()
    assert "Control Center flush" in caplog.text
    assert control_center._event_batch == []
