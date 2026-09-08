import json
import urllib.error
from types import SimpleNamespace

import pytest

from smilekit_bot import control_center
from smilekit_bot.api import effective_settings
from smilekit_bot.config import Settings
from smilekit_bot.models import CrmAction, SupervisorResult
from smilekit_bot.prompts import MANAGER_PROMPT
from smilekit_bot.service import SmilekitAssistant
from smilekit_bot.storage import BotStorage


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


# ─── Prompt sync (Prompt Studio) ────────────────────────────────────────────


class _JsonResponse:
    def __init__(self, body: dict):
        self._body = json.dumps(body).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self._body


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://cc.example", code, "x", {}, None)


def _prompt_body(version_id: str, compiled: str) -> dict:
    return {
        "ok": True,
        "promptId": "p1",
        "version": 3,
        "versionId": version_id,
        "updatedAt": "2026-09-07T10:00:00Z",
        "compiled": compiled,
    }


@pytest.mark.asyncio
async def test_fetch_prompt_200_loads_remote_prompt(monkeypatch):
    control_center.configure("https://cc.example/", "secret")
    seen = {}

    def fake_urlopen(request, timeout=None):
        seen["url"] = request.full_url
        seen["method"] = request.get_method()
        seen["auth"] = request.get_header("Authorization")
        seen["etag"] = request.get_header("If-none-match")
        return _JsonResponse(_prompt_body("v1", "## Identity\nremote"))

    monkeypatch.setattr("smilekit_bot.control_center.urllib.request.urlopen", fake_urlopen)

    assert await control_center.fetch_prompt() == "v1"
    assert seen["url"] == f"https://cc.example{control_center.PROMPT_PATH}"
    assert seen["method"] == "GET"
    assert seen["auth"] == "Bearer secret"
    assert seen["etag"] is None  # первый запрос — версии ещё нет
    assert control_center.current_prompt_version_id() == "v1"
    assert control_center.current_prompt("local") == "## Identity\nremote"


@pytest.mark.asyncio
async def test_fetch_prompt_sends_etag_and_keeps_last_good_on_304(monkeypatch):
    control_center.configure("https://cc.example", "secret")
    monkeypatch.setattr(
        "smilekit_bot.control_center.urllib.request.urlopen",
        lambda request, timeout=None: _JsonResponse(_prompt_body("v1", "remote v1")),
    )
    await control_center.fetch_prompt()

    seen = {}

    def not_modified(request, timeout=None):
        seen["etag"] = request.get_header("If-none-match")
        raise _http_error(304)

    monkeypatch.setattr("smilekit_bot.control_center.urllib.request.urlopen", not_modified)

    assert await control_center.fetch_prompt() is None
    assert seen["etag"] == '"v1"'
    assert control_center.current_prompt_version_id() == "v1"
    assert control_center.current_prompt("local") == "remote v1"


@pytest.mark.asyncio
async def test_fetch_prompt_404_means_no_published_prompt_use_local(monkeypatch):
    control_center.configure("https://cc.example", "secret")

    def not_found(request, timeout=None):
        raise _http_error(404)

    monkeypatch.setattr("smilekit_bot.control_center.urllib.request.urlopen", not_found)

    assert await control_center.fetch_prompt() is None
    assert control_center.current_prompt_version_id() is None
    assert control_center.current_prompt("local") == "local"


@pytest.mark.asyncio
async def test_fetch_prompt_network_or_bad_response_keeps_last_good(monkeypatch, caplog):
    control_center.configure("https://cc.example", "secret")
    monkeypatch.setattr(
        "smilekit_bot.control_center.urllib.request.urlopen",
        lambda request, timeout=None: _JsonResponse(_prompt_body("v1", "remote v1")),
    )
    await control_center.fetch_prompt()

    def boom(request, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr("smilekit_bot.control_center.urllib.request.urlopen", boom)
    assert await control_center.fetch_prompt() is None
    assert "Control Center prompt fetch failed" in caplog.text

    # 200, но без compiled — тоже не ломаем текущую версию.
    monkeypatch.setattr(
        "smilekit_bot.control_center.urllib.request.urlopen",
        lambda request, timeout=None: _JsonResponse({"ok": True, "versionId": "v2", "compiled": ""}),
    )
    assert await control_center.fetch_prompt() is None
    assert control_center.current_prompt_version_id() == "v1"
    assert control_center.current_prompt("local") == "remote v1"


@pytest.mark.asyncio
async def test_fetch_prompt_new_version_replaces_old(monkeypatch):
    control_center.configure("https://cc.example", "secret")
    monkeypatch.setattr(
        "smilekit_bot.control_center.urllib.request.urlopen",
        lambda request, timeout=None: _JsonResponse(_prompt_body("v1", "remote v1")),
    )
    await control_center.fetch_prompt()
    monkeypatch.setattr(
        "smilekit_bot.control_center.urllib.request.urlopen",
        lambda request, timeout=None: _JsonResponse(_prompt_body("v2", "remote v2")),
    )
    assert await control_center.fetch_prompt() == "v2"
    assert control_center.current_prompt("local") == "remote v2"


@pytest.mark.asyncio
async def test_fetch_prompt_disabled_is_noop_and_current_prompt_is_local(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("HTTP must not be called when connector is disabled")

    monkeypatch.setattr("smilekit_bot.control_center.urllib.request.urlopen", boom)

    assert await control_center.fetch_prompt() is None
    assert control_center.current_prompt_version_id() is None
    assert control_center.current_prompt("local") == "local"


def test_configure_resets_loaded_prompt():
    control_center.configure("https://cc.example", "secret")
    control_center._prompt_compiled = "remote"
    control_center._prompt_version_id = "v1"
    control_center.configure("", "")
    assert control_center.current_prompt("local") == "local"
    assert control_center.current_prompt_version_id() is None


@pytest.mark.asyncio
async def test_heartbeat_acks_prompt_version_id(monkeypatch):
    control_center.configure("https://cc.example", "secret")
    seen = {}

    def fake_urlopen(request, timeout=None):
        seen["body"] = json.loads(request.data)
        return _FakeResponse()

    monkeypatch.setattr("smilekit_bot.control_center.urllib.request.urlopen", fake_urlopen)

    await control_center.send_heartbeat(status="ok", prompt_version_id="v1")
    assert seen["body"] == {"status": "ok", "promptVersionId": "v1"}

    await control_center.send_heartbeat(status="ok", prompt_version_id=None)
    assert "promptVersionId" not in seen["body"]


# ─── Потребители: service.reply и api.effective_settings ────────────────────


class _CapturingResponses:
    def __init__(self):
        self.create_kwargs = None

    def create(self, **kwargs):
        self.create_kwargs = kwargs
        return SimpleNamespace(output_text="Черновик")

    def parse(self, **kwargs):
        review = SupervisorResult(status="approved", risk_level="low", crm_action=CrmAction())
        return SimpleNamespace(output_parsed=review)


def _assistant(tmp_path):
    knowledge = tmp_path / "knowledge_base.md"
    knowledge.write_text("[ЗАПОЛНИТЬ]", encoding="utf-8")
    responses = _CapturingResponses()
    client = SimpleNamespace(responses=responses)
    return SmilekitAssistant(api_key="test", knowledge_path=knowledge, client=client), responses


@pytest.mark.asyncio
async def test_service_reply_uses_remote_prompt_when_loaded(tmp_path):
    control_center.configure("https://cc.example", "secret")
    control_center._prompt_compiled = "REMOTE MANAGER PROMPT"
    control_center._prompt_version_id = "v1"
    assistant, responses = _assistant(tmp_path)

    await assistant.reply("Расскажите о товаре", [])

    instructions = responses.create_kwargs["instructions"]
    assert instructions.startswith("REMOTE MANAGER PROMPT")
    assert MANAGER_PROMPT not in instructions


@pytest.mark.asyncio
async def test_service_reply_uses_local_prompt_when_disabled(tmp_path):
    assistant, responses = _assistant(tmp_path)

    await assistant.reply("Расскажите о товаре", [])

    assert responses.create_kwargs["instructions"].startswith(MANAGER_PROMPT)


@pytest.mark.asyncio
async def test_service_reply_explicit_override_beats_remote_prompt(tmp_path):
    control_center.configure("https://cc.example", "secret")
    control_center._prompt_compiled = "REMOTE MANAGER PROMPT"
    control_center._prompt_version_id = "v1"
    assistant, responses = _assistant(tmp_path)

    await assistant.reply("Расскажите о товаре", [], manager_prompt="CRM OVERRIDE")

    assert responses.create_kwargs["instructions"].startswith("CRM OVERRIDE")


def test_effective_settings_default_manager_prompt_is_remote_when_loaded(tmp_path):
    settings = Settings(telegram_bot_token="tg", openai_api_key="oa")
    storage = BotStorage(str(tmp_path / "bot.db"))
    try:
        assert effective_settings(storage, settings)["managerPrompt"] == MANAGER_PROMPT.strip()

        control_center.configure("https://cc.example", "secret")
        control_center._prompt_compiled = "REMOTE MANAGER PROMPT"
        control_center._prompt_version_id = "v1"
        current = effective_settings(storage, settings)
        assert current["managerPrompt"] == "REMOTE MANAGER PROMPT"
        assert current["managerPromptIsDefault"] is True

        storage.set_bot_setting("manager_prompt", "CRM OVERRIDE")
        assert effective_settings(storage, settings)["managerPrompt"] == "CRM OVERRIDE"
    finally:
        storage.close()
