"""Тесты детерминированного роутера тестового Telegram-бота (без ИИ).
Каждый кейс — это то же самое, что уже живьём проверено в Лаборатории
WhatsApp этой же сессии, просто на Python-стороне."""
from smilekit_bot_template_test.router import ChatState, advance_followup, route
from smilekit_bot_template_test import templates as T


def test_first_message_greeting_gets_price_and_city_question():
    state = ChatState()
    result = route("Здравствуйте", state)
    assert result.texts == [T.PRODUCT_INFO, T.CITY_QUESTION]
    assert state.price_mentioned is True


def test_vague_first_message_also_gets_greeting_not_fallback():
    state = ChatState()
    result = route("Улыбка", state)
    assert result.texts[0] == T.PRODUCT_INFO


def test_bishkek_after_price_gets_delivery_and_results_block():
    state = ChatState()
    route("Здравствуйте, сколько стоит?", state)
    result = route("Бишкек", state)
    assert "Яндекс" in result.texts[0]
    assert "30 минут" in result.texts[0]
    assert result.send_results_block is True
    # Живой баг: "Гарантированно увидите эффект..." должно уйти голосовым
    # (как в WhatsApp), а не текстом — иначе бот "молчит" в чате, где должно
    # быть голосовое сообщение.
    assert result.voice == "first_use_effect.ogg"
    assert not any(T.FIRST_USE_EFFECT in part for part in result.texts)


def test_almaty_after_price():
    state = ChatState()
    route("сколько стоит", state)
    result = route("Алматы", state)
    assert "Алматы" in result.texts[0]


def test_city_before_price_does_not_trigger_delivery_block():
    # Живой баг класса, который уже ловили в WhatsApp: город без
    # предварительного вопроса о цене не должен внезапно продавать доставку.
    state = ChatState()
    result = route("Бишкек", state)
    assert result.texts == [T.PRODUCT_INFO, T.CITY_QUESTION]
    assert result.send_results_block is False


def test_discount_ladder_13_then_20_then_reject():
    state = ChatState()
    route("сколько стоит", state)
    first = route("дорого", state)
    assert first.texts == [T.DISCOUNT_OFFER_13_TEXT]
    second = route("всё равно дорого", state)
    assert second.texts == [T.DISCOUNT_OFFER_20_TEXT]
    third = route("а дешевле никак?", state)
    assert third.texts == [T.DISCOUNT_REJECT_TEXT]


def test_pickup_question():
    state = ChatState()
    result = route("А самовывоз есть?", state)
    assert result.texts == [T.PICKUP_REPLY]


def test_address_question():
    state = ChatState()
    result = route("Адрес?", state)
    assert result.texts == [T.ADDRESS_QUESTION_REPLY]


def test_unsupported_country():
    state = ChatState()
    result = route("А если я из Германии, привезёте?", state)
    assert result.texts == [T.UNSUPPORTED_COUNTRY_REPLY]


def test_safety_harm_doubt_is_voice_only_no_duplicate_text():
    state = ChatState()
    result = route("А не вредно ли для эмали?", state)
    assert result.texts == [T.SAFETY_COMPOSITION_HARM_REPLY]  # для last_bot_text
    assert result.voice == "not_harmful.ogg"
    assert result.send_text is False  # живой баг: раньше уходило и текстом, и голосом


def test_safety_safe_doubt_is_text_not_voice():
    state = ChatState()
    result = route("А состав безопасный?", state)
    assert result.texts == [T.SAFETY_COMPOSITION_SAFE_REPLY]
    assert result.voice is None


def test_safety_safe_doubt_reversed_word_order_is_still_text():
    # Живой баг: "безопасный состав" (прилагательное перед существительным —
    # естественный порядок слов) ловился в _SAFETY_HARM_DOUBT из-за того, что
    # "опасн" совпадало внутри "без[опасн]ый", и уходил только голосом.
    state = ChatState()
    result = route("У вас безопасный состав?", state)
    assert result.texts == [T.SAFETY_COMPOSITION_SAFE_REPLY]
    assert result.voice is None
    assert result.send_text is True


def test_praising_price_does_not_trigger_discount():
    # Живой баг: "недорого"/"не дорого" (клиент хвалит цену) содержит
    # подстроку "дорого" и без всякой просьбы предлагал скидку.
    state = ChatState()
    route("сколько стоит", state)
    result = route("о, это совсем недорого!", state)
    assert result.texts != [T.DISCOUNT_OFFER_13_TEXT]
    result2 = route("даже не дорого как по мне", state)
    assert result2.texts != [T.DISCOUNT_OFFER_13_TEXT]


def test_scam_doubt_is_voice_only():
    state = ChatState()
    result = route("А вы не мошенники?", state)
    assert result.texts == [T.DOUBT_RESULT_REPLY]
    assert result.voice == "not_scam.ogg"
    assert result.send_text is False


def test_complaint():
    state = ChatState()
    result = route("Это ужасно, не помогло вообще", state)
    assert result.texts == [T.COMPLAINT_PROMPT]


def test_cooperation():
    state = ChatState()
    result = route("Хочу стать вашим дилером, интересует опт", state)
    assert result.texts == [T.COOPERATION_REPLY]


def test_effect_duration_question():
    state = ChatState()
    result = route("На сколько хватает одной коробки?", state)
    assert result.texts == [T.EFFECT_DURATION_REPLY]


def test_effect_duration_question_common_misspelling():
    # Живой баг: "держиться" (частая ошибка -ться/-тся) не матчился regex'ом,
    # который знал только правильное "держится".
    state = ChatState()
    result = route("А эффект сколько держиться", state)
    assert result.texts == [T.EFFECT_DURATION_REPLY]


def test_pay_on_delivery_request():
    state = ChatState()
    result = route("Я хочу оплатить после того как приеду", state)
    assert result.texts == [T.PAY_ON_DELIVERY_ACK_REPLY]


def test_order_intent_sends_checklist():
    state = ChatState()
    result = route("Хочу заказать", state)
    assert result.texts == [T.ORDER_CHECKLIST]


def test_confirms_order_offer_after_cta():
    state = ChatState()
    state.last_bot_text = T.LOW_STOCK_DISCOUNT_CTA  # содержит "оформить вам"
    result = route("давайте", state)
    assert result.texts == [T.ORDER_CHECKLIST]


def test_returning_greeting_after_history():
    state = ChatState()
    route("Здравствуйте", state)
    result = route("Здравствуйте", state)
    assert result.texts[0] in T.RETURNING_GREETING_REPLIES


def test_delivery_free_question_offers_discount_not_payment_method():
    state = ChatState()
    result = route("а доставка бесплатная?", state)
    assert result.texts == [T.DELIVERY_FREE_DISCOUNT_REPLY]


def test_bishkek_payment_question_is_voice():
    state = ChatState()
    result = route("а доставка по Бишкеку платная?", state)
    assert result.texts == [T.DELIVERY_PAYMENT_BISHKEK]
    assert result.voice == "delivery_price_bishkek.ogg"


def test_regions_payment_question_is_text():
    state = ChatState()
    result = route("доставка по регионам платная?", state)
    assert result.texts == [T.DELIVERY_PAYMENT_REGIONS]
    assert result.voice is None


def test_repeat_without_active_chain():
    state = ChatState()
    result = advance_followup(state)
    assert result.texts == [T.NO_ACTIVE_FOLLOWUP_REPLY]


def test_repeat_advances_all_5_stages_then_exhausted():
    state = ChatState()
    route("сколько стоит", state)
    route("Бишкек", state)  # запускает цепочку дожима (followup_stage = 0)
    assert advance_followup(state).texts == [T.ORDER_CTA_TEXT]
    assert advance_followup(state).texts == [T.RELEVANCE_CHECK_TEXT]
    assert advance_followup(state).texts == [T.DISCOUNT_OFFER_13_TEXT]
    assert advance_followup(state).texts == [T.DISCOUNT_OFFER_20_TEXT]
    assert advance_followup(state).texts == [T.LOW_STOCK_DISCOUNT_CTA]
    assert advance_followup(state).texts == [T.FOLLOWUP_EXHAUSTED_REPLY]


def test_client_reply_during_followup_chain_hands_off():
    state = ChatState()
    route("сколько стоит", state)
    route("Бишкек", state)
    advance_followup(state)  # CTA отправлен, ждём "ответ клиента"
    result = route("да, давайте", state)
    assert result.texts == [T.HANDOFF_NOTICE]
    assert state.handed_off is True
    # После передачи бот дальше молчит любым текстом, даже не похожим на ответ
    assert route("привет ещё раз", state).texts == [T.HANDOFF_NOTICE]
    assert advance_followup(state).texts == [T.HANDOFF_NOTICE]


def test_bare_price_question_after_greeting_does_not_fall_back():
    # Живой баг: "Здравствуйте" + "А сколько стоит" почти одновременно —
    # второе сообщение ни на что не матчилось (не было слова "доставка") и
    # улетало в FALLBACK_CLARIFY_REPLY вместо повторного ответа с ценой.
    state = ChatState()
    route("Здравствуйте", state)
    result = route("А сколько стоит", state)
    assert result.texts == [T.PRODUCT_INFO, T.CITY_QUESTION]


def test_fallback_for_unrecognized_message_with_history():
    state = ChatState()
    route("Здравствуйте", state)
    route("Бишкек", state)
    result = route("а что если я передумаю через месяц вернуть частично товар обратно продавцу", state)
    # Не должно совпасть ни с одной веткой (нет явного "возврат"/"жалоба"/...)
    assert result.texts == [T.FALLBACK_CLARIFY_REPLY]
