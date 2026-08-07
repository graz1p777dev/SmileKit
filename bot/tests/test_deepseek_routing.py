import json
from types import SimpleNamespace

from smilekit_bot.models import SupervisorResult
from smilekit_bot.service import SmilekitAssistant, _parse_json_loose


class FakeDeepSeekCompletions:
    def __init__(self, content):
        self.content = content
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))]
        )


class FakeDeepSeekClient:
    def __init__(self, content):
        self.completions = FakeDeepSeekCompletions(content)
        self.chat = SimpleNamespace(completions=self.completions)


class FakeOpenAIResponses:
    def __init__(self):
        self.called = False
        self.parsed_result = SupervisorResult(status="approved", risk_level="low")

    def create(self, **kwargs):
        self.called = True
        return SimpleNamespace(output_text="OpenAI-фолбэк")

    def parse(self, **kwargs):
        self.called = True
        return SimpleNamespace(output_parsed=self.parsed_result)


class FakeOpenAIClient:
    def __init__(self):
        self.responses = FakeOpenAIResponses()


def make_assistant(tmp_path, deepseek_content):
    knowledge = tmp_path / "knowledge_base.md"
    knowledge.write_text("[ЗАПОЛНИТЬ]", encoding="utf-8")
    return SmilekitAssistant(
        api_key="test",
        knowledge_path=knowledge,
        client=FakeOpenAIClient(),
        deepseek_client=FakeDeepSeekClient(deepseek_content),
    )


def test_draft_uses_deepseek_for_text_only(tmp_path):
    assistant = make_assistant(tmp_path, "Готовый черновик от DeepSeek")
    text = assistant._draft("Привет", [], "", None, "MANAGER", None)
    assert text == "Готовый черновик от DeepSeek"
    assert assistant.client.responses.called is False


def test_draft_falls_back_to_openai_when_image_present(tmp_path):
    assistant = make_assistant(tmp_path, "не должно использоваться")
    text = assistant._draft(
        "Смотри фото", [], "", "data:image/jpeg;base64,xxx", "MANAGER", None
    )
    assert text == "OpenAI-фолбэк"
    assert assistant.client.responses.called is True


def test_review_parses_deepseek_json_into_supervisor_result(tmp_path):
    payload = json.dumps(
        {
            "status": "approved",
            "risk_level": "low",
            "issues": [],
            "corrected_response": "",
            "handoff_reason": "",
            "crm_action": {"stage": "Диалог", "tags": [], "note": ""},
            "customer_memory": "",
            "order_summary": "",
            "order": {},
        }
    )
    assistant = make_assistant(tmp_path, payload)
    result = assistant._review("Привет", [], "черновик", "", None, "SUPERVISOR", None)
    assert isinstance(result, SupervisorResult)
    assert result.status == "approved"
    assert result.crm_action.stage == "Диалог"


def test_review_falls_back_to_openai_when_image_present(tmp_path):
    assistant = make_assistant(tmp_path, "не должно использоваться")
    result = assistant._review(
        "Привет", [], "черновик", "", "data:image/jpeg;base64,xxx", "SUPERVISOR", None
    )
    assert result is assistant.client.responses.parsed_result
    assert assistant.client.responses.called is True


def test_route_returns_template_id_from_deepseek_json(tmp_path):
    assistant = make_assistant(tmp_path, '{"template_id": "effect_duration"}')
    result = assistant._route("На сколько хватает эффекта?", [], None)
    assert result == "effect_duration"


def test_route_returns_none_when_deepseek_declines(tmp_path):
    assistant = make_assistant(tmp_path, '{"template_id": null}')
    result = assistant._route("Расскажите подробнее о применении", [], None)
    assert result is None


def test_parse_json_loose_strips_markdown_fence():
    text = '```json\n{"a": 1}\n```'
    assert _parse_json_loose(text) == {"a": 1}


def test_parse_json_loose_extracts_object_from_surrounding_text():
    text = 'Вот результат: {"a": 1} спасибо'
    assert _parse_json_loose(text) == {"a": 1}
