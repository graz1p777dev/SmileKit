"""Транскрибация голосовых: порядок провайдеров и формат файла для OpenAI.

Живые баги, которые закрывают эти тесты:
1. Приоритет был жёстко у Gemini — при заданном ключе OpenAI не пробовался
   вовсе, а Gemini упирался в 429, и клиент оставался без ответа.
2. Голосовые WhatsApp приходят как .oga, OpenAI отвечал
   «Unsupported file format oga» — хотя это тот же Ogg, что и .ogg.
"""
from types import SimpleNamespace

import pytest

from smilekit_bot.service import SmilekitAssistant


def _assistant(tmp_path, *, transcriptions, gemini_api_key=None) -> SmilekitAssistant:
    knowledge = tmp_path / "knowledge_base.md"
    knowledge.write_text("[ЗАПОЛНИТЬ]", encoding="utf-8")
    client = SimpleNamespace(audio=SimpleNamespace(transcriptions=transcriptions))
    return SmilekitAssistant(
        api_key="test", knowledge_path=knowledge, client=client, gemini_api_key=gemini_api_key
    )


class _RecordingTranscriptions:
    def __init__(self, text="привет"):
        self.names: list[str] = []
        self._text = text

    def create(self, model, file):
        self.names.append(file.name)
        return SimpleNamespace(text=self._text, usage=None)


@pytest.mark.asyncio
async def test_oga_is_sent_to_openai_as_ogg(tmp_path):
    calls = _RecordingTranscriptions()
    assistant = _assistant(tmp_path, transcriptions=calls)

    text = await assistant.transcribe(b"OggS...", "voice.oga", "whisper-1")

    assert text == "привет"
    assert calls.names == ["voice.ogg"]


@pytest.mark.asyncio
async def test_other_extensions_are_left_alone(tmp_path):
    calls = _RecordingTranscriptions()
    assistant = _assistant(tmp_path, transcriptions=calls)

    await assistant.transcribe(b"ID3...", "note.mp3", "whisper-1")

    assert calls.names == ["note.mp3"]


@pytest.mark.asyncio
async def test_openai_is_tried_before_gemini(tmp_path, monkeypatch):
    calls = _RecordingTranscriptions(text="из openai")
    assistant = _assistant(tmp_path, transcriptions=calls, gemini_api_key="gemini-key")

    async def must_not_be_called(*args, **kwargs):
        raise AssertionError("Gemini не должен вызываться, пока OpenAI отвечает")

    monkeypatch.setattr(assistant, "_transcribe_gemini", must_not_be_called)

    assert await assistant.transcribe(b"OggS...", "voice.oga", "whisper-1") == "из openai"


@pytest.mark.asyncio
async def test_gemini_is_fallback_when_openai_fails(tmp_path, monkeypatch):
    class _Broken:
        def create(self, model, file):
            raise RuntimeError("OpenAI недоступен")

    assistant = _assistant(tmp_path, transcriptions=_Broken(), gemini_api_key="gemini-key")

    async def gemini(audio, filename):
        return "из gemini"

    monkeypatch.setattr(assistant, "_transcribe_gemini", gemini)

    assert await assistant.transcribe(b"OggS...", "voice.oga", "whisper-1") == "из gemini"


@pytest.mark.asyncio
async def test_both_failing_reports_both_reasons(tmp_path, monkeypatch):
    class _Broken:
        def create(self, model, file):
            raise RuntimeError("openai down")

    assistant = _assistant(tmp_path, transcriptions=_Broken(), gemini_api_key="gemini-key")

    async def gemini(audio, filename):
        raise RuntimeError("gemini 429")

    monkeypatch.setattr(assistant, "_transcribe_gemini", gemini)

    with pytest.raises(RuntimeError) as error:
        await assistant.transcribe(b"OggS...", "voice.oga", "whisper-1")
    assert "openai down" in str(error.value)
    assert "gemini 429" in str(error.value)
