"""Детерминированный роутер тестового бота — ни одного вызова ИИ. Логика и
порядок веток списаны с продовского WhatsApp-бота (smilekit-crm/src/lib/
greenapi/bot-reply.ts), но переиспользует уже готовые regex-детекторы
намерений из smilekit_bot.sales (та же деловая логика, тот же язык).

route() — чистая функция (текст + состояние чата -> список ответов), без
side-effects и без сети — поэтому легко гоняется в юнит-тестах и в CLI без
поднятия самого Telegram-бота.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from smilekit_bot import sales

from . import templates as T


@dataclass
class ChatState:
    """Состояние одного тестового чата — держим в памяти процесса, без БД:
    это одноразовый тестовый инструмент для одного владельца, не прод."""

    history: list[str] = field(default_factory=list)  # только текст клиента, по порядку
    last_bot_text: str = ""
    price_mentioned: bool = False
    discount_stage: int = 0  # 0 = не предлагали, 1 = предложили 13%, 2 = предложили 20%
    # Цепочка дожима после блока результата: None = не запущена, 0..4 = номер
    # следующего шага, который отправит /repeat (см. advance_followup ниже).
    followup_stage: int | None = None
    # True только сразу ПОСЛЕ того, как /repeat реально отправил шаг дожима —
    # именно на это время следующий ответ клиента считается ответом в цепочке
    # (живой баг, найденный тестом: без этого флага обычное сообщение сразу
    # после блока результата, ДО первого /repeat, тоже ложно улетало в
    # передачу менеджеру).
    followup_awaiting_reply: bool = False
    handed_off: bool = False  # клиент ответил во время дожима — бот дальше молчит


@dataclass
class RouteResult:
    texts: list[str]
    send_results_block: bool = False  # фото до/после + голосовое "первый раз"
    voice: str | None = None  # имя файла в assets/voice, если ответ должен быть голосом
    # False = texts используется только для last_bot_text (память диалога),
    # клиенту НЕ отправляется текстом — голосовое ПОЛНОСТЬЮ заменяет текст,
    # а не дублирует его (п. 28–29 ТЗ: "отправить голосовым сообщением", а
    # не "текстом и голосовым").
    send_text: bool = True


_EFFECT_DURATION_QUESTION = re.compile(
    r"на\s*сколько\s*хватает|сколько\s*держ\w*|сколько\s*(?:длится|хватит)|как\s*долго\s*(?:держ\w*|длится|хватает)",
    re.IGNORECASE,
)
_SAFETY_HARM_DOUBT = re.compile(
    # (?<!без) — живой баг: "опасн\w*" матчился и внутри "безопасный", из-за
    # чего "Безопасный состав?" (нейтральный вопрос, п.30 ТЗ — должен быть
    # текстом) ловился как испуганное сомнение (уходит только голосом).
    r"вред(?:ит|но|ен)?[^.!?]{0,15}(?:эмал|дёсн|десн)|(?:эмал|дёсн|десн)[^.!?]{0,15}вред|(?<!без)опасн\w*[^.!?]{0,15}(?:эмал|дёсн|десн|состав)",
    re.IGNORECASE,
)
_SAFETY_SAFE_DOUBT = re.compile(r"безопасн\w*[^.!?]{0,15}состав|состав[^.!?]{0,15}безопасн", re.IGNORECASE)
_PICKUP_QUESTION = re.compile(r"самовывоз|сам\w*\s*забра\w*|забра\w*\s*сам\w*", re.IGNORECASE)
_ADDRESS_QUESTION = re.compile(
    r"^\s*(?:а\s+)?как(?:ой|ая)?\s+адрес\s*[?？]?\s*$|^\s*адрес\s*[?？]\s*$", re.IGNORECASE
)
_UNSUPPORTED_COUNTRY = re.compile(T.UNSUPPORTED_COUNTRY_MARKERS, re.IGNORECASE)
_DISCOUNT_REQUEST = re.compile(
    # (?<!не)(?<!не ) перед "дорого" — живой баг: "недорого"/"не дорого"
    # (клиент ХВАЛИТ цену) содержит подстроку "дорого" и раньше само по
    # себе предлагало скидку без просьбы — тот же класс бага, что и
    # самопроизвольная скидка в WhatsApp-боте.
    r"скидк|дешевле|подешевле|уступ\w*\s*(?:в\s*цен|цену)|акци[юя]|(?<!не)(?<!не )дорого|не\s*хватает\s*денег|нет\s*денег",
    re.IGNORECASE,
)
_PAY_ON_DELIVERY_REQUEST = re.compile(
    r"(?:оплат[а-яё]*|заплач[а-яё]*)\s*(?:ли\s+)?(?:после|как\s+только|когда|при)[^.!?\n]{0,25}(?:приед|получ|доставк|привез)|"
    r"оплат[ауы]?\s*при\s*получении|наличными\s*(?:курьеру|при\s*получении)|оплачу\s*курьеру",
    re.IGNORECASE,
)
_BISHKEK_MARKER = re.compile(r"бишкек|токмок", re.IGNORECASE)
# п. 17 ТЗ: «доставка бесплатно?» — отдельный, более конкретный вопрос, чем
# просто «платная ли доставка» — проверяется РАНЬШЕ общего _DELIVERY_COST_QUESTION.
_DELIVERY_FREE_QUESTION = re.compile(r"достав\w*[^?]{0,15}бесплат|бесплат\w*[^?]{0,15}достав", re.IGNORECASE)
_DELIVERY_COST_QUESTION = re.compile(
    r"сколько\s*(?:будет|стоит|выйдет)[^?]{0,20}достав|достав\w*[^?]{0,15}плат|плат\w*[^?]{0,15}достав",
    re.IGNORECASE,
)
# Прямой вопрос о цене без слова "доставка" — раньше ничего не ловил и
# улетал в FALLBACK_CLARIFY_REPLY (живой баг: "Здравствуйте" + "А сколько
# стоит" почти одновременно — см. tests).
_PRICE_QUESTION = re.compile(r"сколько\s*(?:стоит|будет|выйдет)\b|почём|почем|какая\s*цена|\bцена\s*[?？]", re.IGNORECASE)


def _match_delivery_template(text: str) -> str | None:
    lowered = text.lower()
    for pattern, reply in T.DELIVERY_TEMPLATES:
        if re.search(pattern, lowered, re.IGNORECASE):
            return reply
    return None


def route(text: str, state: ChatState) -> RouteResult:
    if state.handed_off:
        return RouteResult([T.HANDOFF_NOTICE])
    if state.followup_awaiting_reply:
        # Клиент ответил на реально отправленный шаг цепочки дожима — в
        # реальном боте это передача менеджеру, дальше бот сам не отвечает
        # (см. TEMPLATES.md A.7).
        state.handed_off = True
        state.followup_stage = None
        state.followup_awaiting_reply = False
        state.history.append(text)
        state.last_bot_text = T.HANDOFF_NOTICE
        return RouteResult([T.HANDOFF_NOTICE])

    stripped = text.strip()
    is_first_message = len(state.history) == 0

    result: RouteResult | None = None

    if _UNSUPPORTED_COUNTRY.search(stripped):
        result = RouteResult([T.UNSUPPORTED_COUNTRY_REPLY])
    elif sales.asks_scam_doubt(stripped):
        result = RouteResult([T.DOUBT_RESULT_REPLY], voice="not_scam.ogg", send_text=False)
    elif _SAFETY_HARM_DOUBT.search(stripped):
        result = RouteResult([T.SAFETY_COMPOSITION_HARM_REPLY], voice="not_harmful.ogg", send_text=False)
    elif _SAFETY_SAFE_DOUBT.search(stripped):
        result = RouteResult([T.SAFETY_COMPOSITION_SAFE_REPLY])
    elif _PICKUP_QUESTION.search(stripped):
        result = RouteResult([T.PICKUP_REPLY])
    elif _ADDRESS_QUESTION.search(stripped):
        result = RouteResult([T.ADDRESS_QUESTION_REPLY])
    elif sales.is_complaint(stripped):
        result = RouteResult([T.COMPLAINT_PROMPT])
    elif sales.mentions_cooperation_inquiry(stripped):
        result = RouteResult([T.COOPERATION_REPLY])
    elif _EFFECT_DURATION_QUESTION.search(stripped):
        result = RouteResult([T.EFFECT_DURATION_REPLY])
    elif _PAY_ON_DELIVERY_REQUEST.search(stripped):
        result = RouteResult([T.PAY_ON_DELIVERY_ACK_REPLY])
    elif _DELIVERY_FREE_QUESTION.search(stripped):
        result = RouteResult([T.DELIVERY_FREE_DISCOUNT_REPLY])
    elif _DELIVERY_COST_QUESTION.search(stripped):
        if _BISHKEK_MARKER.search(stripped):
            # п. 20 ТЗ: этот конкретный ответ должен уходить голосовым, не текстом.
            result = RouteResult([T.DELIVERY_PAYMENT_BISHKEK], voice="delivery_price_bishkek.ogg")
        else:
            result = RouteResult([T.DELIVERY_PAYMENT_REGIONS])
    elif _PRICE_QUESTION.search(stripped):
        result = RouteResult([T.PRODUCT_INFO, T.CITY_QUESTION])
    elif sales.wants_payment_info(stripped) or sales.wants_to_order(stripped):
        result = RouteResult([T.ORDER_CHECKLIST], voice="order_checklist.ogg", send_text=False)
    elif sales.confirms_order_offer(stripped, state.last_bot_text):
        result = RouteResult([T.ORDER_CHECKLIST], voice="order_checklist.ogg", send_text=False)
    elif _DISCOUNT_REQUEST.search(stripped):
        if state.discount_stage == 0:
            state.discount_stage = 1
            result = RouteResult([T.DISCOUNT_OFFER_13_TEXT])
        elif state.discount_stage == 1:
            state.discount_stage = 2
            result = RouteResult([T.DISCOUNT_OFFER_20_TEXT])
        else:
            result = RouteResult([T.DISCOUNT_REJECT_TEXT])
    else:
        delivery_reply = _match_delivery_template(stripped)
        if delivery_reply and state.price_mentioned:
            # FIRST_USE_EFFECT уходит голосовым (как в WhatsApp после правки
            # «второе голосовое убрать») — не дублируем его же текстом.
            result = RouteResult(
                [delivery_reply],
                send_results_block=True,
                voice="first_use_effect.ogg",
            )
            state.followup_stage = 0  # блок результата отправлен — можно /repeat
        elif not is_first_message and sales.is_greeting_only(stripped):
            index = len(state.history) % len(T.RETURNING_GREETING_REPLIES)
            result = RouteResult([T.RETURNING_GREETING_REPLIES[index]])
        elif is_first_message:
            # Первое сообщение — обычное знакомство, последний общий вариант,
            # как и в WhatsApp-роутере. Живой баг (найден этим же тестом):
            # раньше это была ветка "else" без условия is_first_message —
            # любое нераспознанное сообщение В СЕРЕДИНЕ диалога (после
            # города/цены) тоже улетало в приветствие с ценой ещё раз,
            # вместо честного "не поняла, уточните".
            result = RouteResult([T.PRODUCT_INFO, T.CITY_QUESTION])
        else:
            result = RouteResult([T.FALLBACK_CLARIFY_REPLY])

    if result is None:
        result = RouteResult([T.FALLBACK_CLARIFY_REPLY])

    if any(T.PRODUCT_INFO in part or "1490" in part for part in result.texts):
        state.price_mentioned = True

    state.history.append(text)
    state.last_bot_text = result.texts[-1]
    return result


def advance_followup(state: ChatState) -> RouteResult:
    """/repeat — перемотка времени: сразу шлёт следующий шаг цепочки дожима,
    вместо реального ожидания 3/10/15/15/15 минут между шагами."""
    if state.handed_off:
        return RouteResult([T.HANDOFF_NOTICE])
    if state.followup_stage is None:
        return RouteResult([T.NO_ACTIVE_FOLLOWUP_REPLY])
    if state.followup_stage >= len(T.FOLLOWUP_STAGES):
        return RouteResult([T.FOLLOWUP_EXHAUSTED_REPLY])
    text = T.FOLLOWUP_STAGES[state.followup_stage]
    state.followup_stage += 1
    state.followup_awaiting_reply = True
    state.last_bot_text = text
    return RouteResult([text])
