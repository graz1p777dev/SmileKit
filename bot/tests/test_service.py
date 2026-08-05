import json
from types import SimpleNamespace

import pytest

from smilekit_bot.models import ChatMessage, CrmAction, SupervisorResult
from smilekit_bot.media import should_send_results_after_city, wants_result_photos
from smilekit_bot.prompts import MANAGER_PROMPT, SUPERVISOR_PROMPT
from smilekit_bot.service import FALLBACK_TEXT, SmilekitAssistant
from smilekit_bot.sales import ensure_manager_contact, wants_to_order


class FakeResponses:
    def __init__(
        self,
        review: SupervisorResult | None = None,
        fail=False,
        fail_review=False,
        draft="Готовый черновик",
    ):
        self.review = review
        self.fail = fail
        self.fail_review = fail_review
        self.draft = draft
        self.create_kwargs = None
        self.parse_kwargs = None

    def create(self, **kwargs):
        self.create_kwargs = kwargs
        if self.fail:
            raise RuntimeError("network")
        return SimpleNamespace(output_text=self.draft)

    def parse(self, **kwargs):
        self.parse_kwargs = kwargs
        if self.fail or self.fail_review:
            raise RuntimeError("network")
        return SimpleNamespace(output_parsed=self.review)


class FakeClient:
    def __init__(self, responses, transcription_text="распознанный текст"):
        self.responses = responses
        self.audio = SimpleNamespace(
            transcriptions=SimpleNamespace(
                create=lambda **kwargs: SimpleNamespace(text=transcription_text)
            )
        )


def assistant(tmp_path, responses):
    knowledge = tmp_path / "knowledge_base.md"
    knowledge.write_text("[ЗАПОЛНИТЬ]", encoding="utf-8")
    return SmilekitAssistant(
        api_key="test",
        knowledge_path=knowledge,
        client=FakeClient(responses),
    )


@pytest.mark.asyncio
async def test_approved_uses_draft(tmp_path):
    review = SupervisorResult(
        status="approved",
        risk_level="low",
        crm_action=CrmAction(),
    )
    result = await assistant(tmp_path, FakeResponses(review)).reply("Расскажите о товаре", [])
    assert result.text == "Готовый черновик"


@pytest.mark.asyncio
async def test_price_question_is_written_by_ai(tmp_path):
    review = SupervisorResult(status="approved", risk_level="low")
    responses = FakeResponses(review, draft="1490 сом. В коробке 14 полосок.")
    result = await assistant(tmp_path, responses).reply("Сколько стоит?", [])
    assert result.text == "1490 сом. В коробке 14 полосок."
    assert responses.create_kwargs is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    ["Сколько стоию", "сколко стоит", "скок стоит"],
)
async def test_price_question_with_typo_is_understood(tmp_path, message):
    review = SupervisorResult(status="approved", risk_level="low")
    responses = FakeResponses(review, draft="Понял: цена 1490 сом.")
    result = await assistant(tmp_path, responses).reply(message, [])
    assert "1490 сом" in result.text
    assert "Не совсем понял" not in result.text
    assert responses.create_kwargs["input"][-1]["content"] == message


@pytest.mark.asyncio
async def test_plain_greeting_is_written_by_ai(tmp_path):
    review = SupervisorResult(status="approved", risk_level="low")
    responses = FakeResponses(review, draft="Здравствуйте! Что подсказать?")
    result = await assistant(tmp_path, responses).reply("Здравствуйте", [])
    assert result.text == "Здравствуйте! Что подсказать?"
    assert responses.create_kwargs is not None


@pytest.mark.asyncio
async def test_instruction_question_is_written_by_ai(tmp_path):
    review = SupervisorResult(status="approved", risk_level="low")
    responses = FakeResponses(review, draft="Высушите зубы и оставьте полоски на 30 минут.")
    result = await assistant(tmp_path, responses).reply(
        "А есть инструкция по применению?", []
    )
    assert "Высушите зубы" in result.text
    assert "30 минут" in result.text
    assert responses.create_kwargs is not None


@pytest.mark.asyncio
async def test_explicit_discount_request_is_written_by_ai(tmp_path):
    review = SupervisorResult(status="approved", risk_level="low")
    responses = FakeResponses(
        review,
        draft="Для вас сделаю скидку 20% — получится 1190 сом.",
    )
    result = await assistant(tmp_path, responses).reply(
        "У меня только 1300 сом, скидку сделаете?", []
    )
    assert "скидку 20%" in result.text
    assert "1190 сом" in result.text
    assert responses.create_kwargs is not None


@pytest.mark.asyncio
async def test_tokmok_after_price_is_written_by_ai_with_history(tmp_path):
    review = SupervisorResult(status="approved", risk_level="low")
    responses = FakeResponses(review, draft="В Токмоке можно оплатить при получении.")
    history = [ChatMessage(content="Сколько стоит?", role="user")]
    result = await assistant(tmp_path, responses).reply("Я из Токмока", history)
    assert "при получении" in result.text
    assert responses.create_kwargs is not None


@pytest.mark.asyncio
async def test_long_interested_dialog_is_not_modified_after_ai_response(tmp_path):
    review = SupervisorResult(status="approved", risk_level="low")
    responses = FakeResponses(review, draft="Отвечаю именно на ваш вопрос.")
    history = [
        ChatMessage(role="user", content=f"Вопрос {index}")
        for index in range(5)
    ]
    result = await assistant(tmp_path, responses).reply("Штучно продаёте?", history)
    assert result.text == "Отвечаю именно на ваш вопрос."


@pytest.mark.asyncio
async def test_existing_discount_history_is_passed_to_ai(tmp_path):
    review = SupervisorResult(status="approved", risk_level="low")
    responses = FakeResponses(review)
    history = [
        ChatMessage(role="user", content=f"Вопрос {index}")
        for index in range(5)
    ]
    history.append(ChatMessage(role="assistant", content="Предлагали скидку 20%"))
    result = await assistant(tmp_path, responses).reply("Штучно продаёте?", history)
    assert "скидку 20%" not in result.text
    assert responses.create_kwargs["input"][-2]["content"] == "Предлагали скидку 20%"


@pytest.mark.asyncio
async def test_rewrite_uses_corrected_response(tmp_path):
    review = SupervisorResult(
        status="rewrite",
        risk_level="medium",
        corrected_response="Точный исправленный ответ",
        crm_action=CrmAction(),
    )
    result = await assistant(tmp_path, FakeResponses(review)).reply("Расскажите о товаре", [])
    assert result.text == "Точный исправленный ответ"


@pytest.mark.asyncio
async def test_api_failure_returns_safe_message(tmp_path):
    result = await assistant(tmp_path, FakeResponses(fail=True)).reply("Расскажите о товаре", [])
    assert result.text == FALLBACK_TEXT
    assert "цена, доставка" not in result.text
    assert result.supervisor.status == "rewrite"


@pytest.mark.asyncio
async def test_review_failure_sends_manager_draft_instead_of_generic_error(tmp_path):
    result = await assistant(tmp_path, FakeResponses(fail_review=True)).reply(
        "Расскажите о товаре", []
    )
    assert result.text == "Готовый черновик"
    assert result.text != FALLBACK_TEXT
    assert result.supervisor.status == "approved"


@pytest.mark.asyncio
async def test_order_intent_is_written_by_ai(tmp_path):
    review = SupervisorResult(status="approved", risk_level="low")
    responses = FakeResponses(
        review,
        draft="Напишите, пожалуйста, имя и номер телефона для оформления.",
    )
    result = await assistant(tmp_path, responses).reply("Оформите доставку", [])
    assert "имя и номер телефона" in result.text
    assert responses.create_kwargs is not None


class FakeRouteResponses:
    """Мок только для route()/verify_receipt(): отдельный формат ответа
    (function_call), не пересекается с FakeResponses.create()/.parse() для
    reply()."""

    def __init__(self, function_call: dict | None = None, fail=False, function_name="send_template"):
        self.function_call = function_call
        self.fail = fail
        self.function_name = function_name
        self.create_kwargs = None

    def create(self, **kwargs):
        self.create_kwargs = kwargs
        if self.fail:
            raise RuntimeError("network")
        if self.function_call is None:
            return SimpleNamespace(output=[])
        item = SimpleNamespace(
            type="function_call",
            name=self.function_name,
            arguments=json.dumps(self.function_call),
        )
        return SimpleNamespace(output=[item])


@pytest.mark.asyncio
async def test_route_returns_template_id_on_function_call(tmp_path):
    responses = FakeRouteResponses({"template_id": "effect_duration"})
    result = await assistant(tmp_path, responses).route("На сколько хватает эффекта?", [])
    assert result == "effect_duration"
    assert responses.create_kwargs["tools"][0]["name"] == "send_template"


@pytest.mark.asyncio
async def test_route_returns_none_without_function_call(tmp_path):
    responses = FakeRouteResponses(None)
    result = await assistant(tmp_path, responses).route("Расскажите подробнее о бренде", [])
    assert result is None


@pytest.mark.asyncio
async def test_route_failure_returns_none_instead_of_raising(tmp_path):
    responses = FakeRouteResponses(fail=True)
    result = await assistant(tmp_path, responses).route("Что-то про эффект", [])
    assert result is None


def test_prompts_require_confident_brand_voice_without_internal_wording():
    assert "старший онлайн-менеджер Smilekit" in MANAGER_PROMPT
    assert "Не отвечай шаблонно" in MANAGER_PROMPT
    assert "Молча восстанови наиболее вероятный смысл" in MANAGER_PROMPT
    assert "не добавляй комплектацию" in MANAGER_PROMPT
    assert "ОБЯЗАТЕЛЬНО разделяй ответ" in MANAGER_PROMPT
    assert "очевидный вопрос" in SUPERVISOR_PROMPT
    assert "меню тем" in SUPERVISOR_PROMPT
    assert "добавил условия доставки" in SUPERVISOR_PROMPT
    assert "разные смысловые части слиты" in SUPERVISOR_PROMPT
    assert "возврате со стороны Smilekit" in SUPERVISOR_PROMPT
    assert "по данным компании" in SUPERVISOR_PROMPT


def test_explicit_photo_request_is_detected():
    assert wants_result_photos("Есть фото результатов клиентов?")


def test_complaint_does_not_trigger_result_photos():
    assert not wants_result_photos("У меня боль и плохой результат")


def test_city_after_price_triggers_results_only_for_sales_funnel():
    assert should_send_results_after_city("Я с Бишкека", ["Сколько стоит?", "1490 сом"])
    assert not should_send_results_after_city("Я с Бишкека", ["Как использовать?"])
    assert not should_send_results_after_city("У меня боль, я в Бишкеке", ["1490 сом"])


def test_order_intent_adds_manager_contact():
    response = ensure_manager_contact("Хочу заказать одну упаковку", "Да, можно.")
    assert "0999000845" in response


def test_regular_question_does_not_add_manager_contact():
    assert not wants_to_order("Как правильно использовать полоски?")
    assert ensure_manager_contact("Какая цена?", "1490 сом") == "1490 сом"


def test_existing_manager_contact_is_not_duplicated():
    response = "Напишите менеджеру: +996 999 000 845"
    assert ensure_manager_contact("Хочу купить", response) == response


def test_classify_objection_detects_thinking_and_expensive():
    from smilekit_bot.sales import classify_objection

    assert classify_objection("Я подумаю пока") == "thinking"
    assert classify_objection("Надо подумать") == "thinking"
    assert classify_objection("Ой, дороговато для меня") == "expensive"
    assert classify_objection("Как использовать?") is None


def test_promised_delay_hours_detects_relative_time():
    from smilekit_bot.sales import promised_delay_hours

    assert promised_delay_hours("завтра зп- сразу закажу") == 24.0
    assert promised_delay_hours("скажу в течение завтрашнего дня") == 24.0
    assert promised_delay_hours("послезавтра отвечу") == 48.0
    assert promised_delay_hours("через 3 дня отпишусь") == 72.0
    assert promised_delay_hours("напишу через неделю") == 168.0
    assert promised_delay_hours("хорошо, спасибо") is None


def test_fill_nudge_template_substitutes_and_falls_back():
    from smilekit_bot import templates

    text = templates.fill_nudge_template(templates.FEW_HOURS_FOLLOWUP, "Айгерим")
    assert text.startswith("Здравствуйте, Айгерим.")
    assert "Smilekit" in text

    without_name = templates.fill_nudge_template(templates.LAST_MESSAGE_FOLLOWUP, None)
    assert not without_name.startswith(",")
    assert "Smilekit" in without_name


@pytest.mark.asyncio
async def test_photo_is_sent_as_multimodal_input(tmp_path):
    review = SupervisorResult(status="approved", risk_level="low")
    responses = FakeResponses(review)
    await assistant(tmp_path, responses).reply(
        "Что видно?",
        [],
        image_data_url="data:image/jpeg;base64,abc",
    )
    current = responses.create_kwargs["input"][-1]
    assert current["content"][1]["type"] == "input_image"
    assert current["content"][1]["detail"] == "high"
    assert responses.parse_kwargs["input"][0]["content"][1]["type"] == "input_image"


@pytest.mark.asyncio
async def test_voice_is_transcribed(tmp_path):
    service = assistant(tmp_path, FakeResponses())
    text = await service.transcribe(b"audio", "voice.ogg")
    assert text == "распознанный текст"
