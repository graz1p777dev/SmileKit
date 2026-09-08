"""Живой баг: aiohttp.ClientResponseError печатает URL запроса вместе с
?key=<GEMINI_API_KEY>, и через logging.exception ключ уходил в Railway-логи.
_gemini_generate обязан отдавать наружу ошибку без URL и без исходного
исключения в цепочке (from None)."""
from types import SimpleNamespace

import aiohttp
import pytest

from smilekit_bot import service
from smilekit_bot.service import SmilekitAssistant

SECRET = "SECRET-GEMINI-KEY-123"


class _FakeResponse:
    def __init__(self, status: int, reason: str):
        self.status = status
        self.reason = reason
        self.request_info = SimpleNamespace(
            real_url=f"https://generativelanguage.googleapis.com/x?key={SECRET}",
            url=f"https://generativelanguage.googleapis.com/x?key={SECRET}",
            method="POST",
            headers={},
        )
        self.history = ()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def raise_for_status(self):
        raise aiohttp.ClientResponseError(
            self.request_info, self.history, status=self.status, message=self.reason
        )

    async def json(self):
        return {}


class _FakeSession:
    def __init__(self, status: int, reason: str):
        self._status = status
        self._reason = reason

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def post(self, url, **kwargs):
        # Реальный URL действительно содержит ключ в params — именно поэтому
        # ошибка не должна его пересказывать.
        assert kwargs["params"]["key"] == SECRET
        return _FakeResponse(self._status, self._reason)


def _assistant(tmp_path) -> SmilekitAssistant:
    knowledge = tmp_path / "knowledge_base.md"
    knowledge.write_text("[ЗАПОЛНИТЬ]", encoding="utf-8")
    return SmilekitAssistant(
        api_key="test", knowledge_path=knowledge, client=SimpleNamespace(), gemini_api_key=SECRET
    )


def _assert_no_key(error: BaseException) -> None:
    """Проверяем ровно то, что попадёт в лог: traceback печатает __cause__, а
    __context__ — только если он не подавлен (raise ... from None ставит
    __suppress_context__=True, сам объект контекста Python всё равно хранит)."""
    seen = []
    cursor: BaseException | None = error
    while cursor is not None:
        seen.append(str(cursor))
        if cursor.__cause__ is not None:
            cursor = cursor.__cause__
        elif not cursor.__suppress_context__:
            cursor = cursor.__context__
        else:
            cursor = None
    joined = "\n".join(seen)
    assert SECRET not in joined
    assert "key=" not in joined


@pytest.mark.asyncio
async def test_429_after_retries_does_not_leak_api_key(tmp_path, monkeypatch):
    monkeypatch.setattr(service.aiohttp, "ClientSession", lambda: _FakeSession(429, "Too Many Requests"))

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(service.asyncio, "sleep", no_sleep)
    with pytest.raises(RuntimeError) as info:
        await _assistant(tmp_path)._gemini_generate([{"text": "hi"}])
    assert "429" in str(info.value)
    _assert_no_key(info.value)


@pytest.mark.asyncio
async def test_non_retryable_http_error_is_sanitized(tmp_path, monkeypatch):
    monkeypatch.setattr(service.aiohttp, "ClientSession", lambda: _FakeSession(400, "Bad Request"))
    with pytest.raises(RuntimeError) as info:
        await _assistant(tmp_path)._gemini_generate([{"text": "hi"}])
    assert "400" in str(info.value)
    assert info.value.__cause__ is None  # raise ... from None
    _assert_no_key(info.value)
