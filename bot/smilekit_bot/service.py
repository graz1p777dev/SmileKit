import asyncio
import io
import json
import logging
from pathlib import Path

from openai import OpenAI

from .models import AssistantReply, ChatMessage, CrmAction, SupervisorResult
from .prompts import MANAGER_PROMPT, SUPERVISOR_PROMPT


FALLBACK_TEXT = (
    "Не совсем понял вас. Уточните, пожалуйста, что вы имеете в виду?"
)
LOGGER = logging.getLogger(__name__)
DRAFT_TIMEOUT_SECONDS = 20
REVIEW_TIMEOUT_SECONDS = 15
ROUTE_TIMEOUT_SECONDS = 8

# Роутер шаблонов: дешёвый вызов с function calling вместо полного цикла
# черновик+проверка там, где ответ и так один и тот же готовый текст.
# Если сомневается — не вызывает функцию, и reply() идёт по обычному пути.
ROUTE_PROMPT = (
    "Ты — маршрутизатор ответов Smilekit. Если сообщение клиента дословно "
    "попадает под один из готовых сценариев — вызови send_template с его id. "
    "Вызывай, только если уверен; при малейшем сомнении, шутке, жалобе или "
    "составном вопросе с другими темами — не вызывай функцию вообще."
)

ROUTE_TOOL = {
    "type": "function",
    "name": "send_template",
    "description": "Отправить готовый шаблонный ответ вместо генерации нового текста.",
    "parameters": {
        "type": "object",
        "properties": {
            "template_id": {
                "type": "string",
                "enum": ["effect_duration", "safety_composition"],
                "description": (
                    "effect_duration — клиент спрашивает, на сколько хватает эффекта, "
                    "сколько он держится или сколько длится результат. "
                    "safety_composition — клиент спрашивает про состав, безопасность "
                    "состава, вред для эмали или дёсен."
                ),
            }
        },
        "required": ["template_id"],
        "additionalProperties": False,
    },
}


class SmilekitAssistant:
    def __init__(
        self,
        api_key: str,
        model: str = "gpt-5.6-luna",
        knowledge_path: Path | None = None,
        client: OpenAI | None = None,
    ) -> None:
        self.model = model
        self.client = client or OpenAI(api_key=api_key)
        path = knowledge_path or Path(__file__).resolve().parent.parent / "knowledge_base.md"
        self.knowledge = path.read_text(encoding="utf-8")

    async def reply(
        self,
        message: str,
        history: list[ChatMessage],
        *,
        customer_memory: str = "",
        image_data_url: str | None = None,
        manager_prompt: str | None = None,
        supervisor_prompt: str | None = None,
        model: str | None = None,
    ) -> AssistantReply:
        """Собирает ответ менеджера.

        manager_prompt/supervisor_prompt/model — override из настроек CRM
        (см. api.py). None означает «использовать встроенный дефолт» — так
        живой бот и лаборатория используют один и тот же пайплайн.
        """
        effective_manager_prompt = manager_prompt or MANAGER_PROMPT
        effective_supervisor_prompt = supervisor_prompt or SUPERVISOR_PROMPT
        effective_model = model or self.model
        try:
            draft = await asyncio.wait_for(
                asyncio.to_thread(
                    self._draft,
                    message,
                    history,
                    customer_memory,
                    image_data_url,
                    effective_manager_prompt,
                    effective_model,
                ),
                timeout=DRAFT_TIMEOUT_SECONDS,
            )
        except Exception:
            LOGGER.exception("AI draft generation failed")
            review = SupervisorResult(
                status="rewrite",
                risk_level="high",
                issues=["Внутренняя ошибка генерации"],
                corrected_response=FALLBACK_TEXT,
                handoff_reason="",
                crm_action=CrmAction(
                    stage="Ошибка ответа",
                    tags=["system_error"],
                    note="Черновик модели не был создан",
                ),
            )
            return AssistantReply(text=FALLBACK_TEXT, supervisor=review)

        try:
            review = await asyncio.wait_for(
                asyncio.to_thread(
                    self._review,
                    message,
                    history,
                    draft,
                    customer_memory,
                    image_data_url,
                    effective_supervisor_prompt,
                    effective_model,
                ),
                timeout=REVIEW_TIMEOUT_SECONDS,
            )
        except Exception:
            LOGGER.exception("AI review failed; sending manager draft")
            review = SupervisorResult(
                status="approved",
                risk_level="medium",
                issues=["Проверка гипервизора не завершилась"],
                corrected_response="",
                handoff_reason="",
                crm_action=CrmAction(
                    stage="Диалог",
                    tags=["review_timeout"],
                    note="Клиенту отправлен черновик менеджера",
                ),
            )

        if review.status == "approved":
            text = draft
        elif review.status == "rewrite":
            text = review.corrected_response.strip() or FALLBACK_TEXT
        return AssistantReply(text=text, supervisor=review)

    async def route(
        self, message: str, history: list[ChatMessage], *, model: str | None = None
    ) -> str | None:
        """template_id, если сообщение точно попадает под готовый шаблон,
        иначе None — тогда reply() идёт по обычному пути. Никогда не бросает
        исключение наружу: сбой роутера не должен ронять обычный ответ."""
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._route, message, history, model),
                timeout=ROUTE_TIMEOUT_SECONDS,
            )
        except Exception:
            LOGGER.exception("Template routing failed")
            return None

    def _route(
        self, message: str, history: list[ChatMessage], model: str | None
    ) -> str | None:
        history_text = "\n".join(
            f"{item.role}: {item.content}" for item in history[-6:]
        ) or "(новый диалог)"
        response = self.client.responses.create(
            model=model or self.model,
            reasoning={"effort": "low"},
            instructions=ROUTE_PROMPT,
            input=[{
                "role": "user",
                "content": f"ИСТОРИЯ:\n{history_text}\n\nСООБЩЕНИЕ КЛИЕНТА:\n{message}",
            }],
            tools=[ROUTE_TOOL],
            tool_choice="auto",
            store=False,
        )
        for item in response.output:
            if getattr(item, "type", None) != "function_call" or item.name != "send_template":
                continue
            try:
                return json.loads(item.arguments).get("template_id")
            except (json.JSONDecodeError, AttributeError):
                return None
        return None

    async def transcribe(
        self, audio: bytes, filename: str, model: str = "gpt-transcribe"
    ) -> str:
        return await asyncio.to_thread(self._transcribe, audio, filename, model)

    def _transcribe(self, audio: bytes, filename: str, model: str) -> str:
        stream = io.BytesIO(audio)
        stream.name = filename
        response = self.client.audio.transcriptions.create(
            model=model,
            file=stream,
        )
        text = (response.text or "").strip()
        if not text:
            raise RuntimeError("OpenAI returned an empty transcription")
        return text

    @staticmethod
    def _current_input(message: str, image_data_url: str | None):
        if not image_data_url:
            return {"role": "user", "content": message}
        return {
            "role": "user",
            "content": [
                {"type": "input_text", "text": message},
                {
                    "type": "input_image",
                    "image_url": image_data_url,
                    "detail": "high",
                },
            ],
        }

    def _draft(
        self,
        message: str,
        history: list[ChatMessage],
        customer_memory: str,
        image_data_url: str | None,
        manager_prompt: str = MANAGER_PROMPT,
        model: str | None = None,
    ) -> str:
        memory = customer_memory.strip() or "(память пока пуста)"
        response = self.client.responses.create(
            model=model or self.model,
            reasoning={"effort": "low"},
            text={"verbosity": "low"},
            instructions=(
                f"{manager_prompt}\n\nБАЗА ЗНАНИЙ:\n{self.knowledge}\n\n"
                f"ПАМЯТЬ О КЛИЕНТЕ:\n{memory}"
            ),
            input=[
                *[item.model_dump() for item in history[-16:]],
                self._current_input(message, image_data_url),
            ],
            store=False,
        )
        text = response.output_text.strip()
        if not text:
            raise RuntimeError("OpenAI returned an empty manager response")
        return text

    def _review(
        self,
        message: str,
        history: list[ChatMessage],
        draft: str,
        customer_memory: str,
        image_data_url: str | None,
        supervisor_prompt: str = SUPERVISOR_PROMPT,
        model: str | None = None,
    ) -> SupervisorResult:
        history_text = "\n".join(
            f"{item.role}: {item.content}" for item in history[-16:]
        ) or "(новый диалог)"
        review_text = (
            f"БАЗА ЗНАНИЙ:\n{self.knowledge}\n\n"
            f"ПАМЯТЬ О КЛИЕНТЕ:\n{customer_memory or '(пусто)'}\n\n"
            f"ИСТОРИЯ:\n{history_text}\n\n"
            f"СООБЩЕНИЕ КЛИЕНТА:\n{message}\n\n"
            f"ЧЕРНОВИК:\n{draft}"
        )
        review_input = self._current_input(review_text, image_data_url)
        response = self.client.responses.parse(
            model=model or self.model,
            reasoning={"effort": "low"},
            instructions=supervisor_prompt,
            input=[review_input],
            text_format=SupervisorResult,
            store=False,
        )
        result = response.output_parsed
        if result is None:
            raise RuntimeError("OpenAI returned no supervisor result")
        return result
