from types import SimpleNamespace

import pytest
from aiogram.enums import ChatType

from smilekit_bot.__main__ import GroupSilenceMiddleware


def message(chat_type: ChatType, text: str | None = None):
    return SimpleNamespace(chat=SimpleNamespace(type=chat_type), text=text)


@pytest.mark.asyncio
async def test_group_messages_are_ignored():
    called = False

    async def handler(event, data):
        nonlocal called
        called = True

    result = await GroupSilenceMiddleware()(handler, message(ChatType.SUPERGROUP, "Привет"), {})
    assert result is None
    assert not called


@pytest.mark.asyncio
async def test_delivery_group_command_is_allowed():
    async def handler(event, data):
        return "handled"

    result = await GroupSilenceMiddleware()(
        handler, message(ChatType.GROUP, "/delivery_group@Smilekitkgbot"), {}
    )
    assert result == "handled"


@pytest.mark.asyncio
async def test_private_messages_are_unchanged():
    async def handler(event, data):
        return "handled"

    result = await GroupSilenceMiddleware()(handler, message(ChatType.PRIVATE), {})
    assert result == "handled"
