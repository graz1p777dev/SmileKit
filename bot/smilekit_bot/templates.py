"""Готовые тексты ответов бота.

Заданы дословно по ТЗ — не перефразировать. Отправляются напрямую, без
участия ИИ (первый контакт, эффект+доставка после города, благодарность
после заказа) либо через router() с function calling (вопросы про срок
действия и состав) — так экономим полный цикл черновик+проверка ИИ там,
где ответ и так один и тот же текст.
"""

import re

PRODUCT_INFO = (
    "Здравствуйте! 😊\n\n"
    "SmileKit помогает заметно отбелить зубы уже после первого применения.\n\n"
    "📦 В наборе 14 отбеливающих полосок — это полный курс.\n\n"
    "💰 ЦЕНА — 1490 СОМ.🇰🇬"
)

CITY_QUESTION = "Подскажите, пожалуйста, из какого вы города?"

RETURNING_GREETING_REPLIES = (
    "Здравствуйте! 😊 Рада снова вас видеть. Чем могу помочь?",
    "Здравствуйте! 😊 Конечно, я на связи. Что подсказать?",
    "Здравствуйте! 😊 Продолжим — чем могу помочь?",
)

RETURNING_KYRGYZ_GREETING_REPLIES = (
    "Салам! 😊 Кайрадан көргөнүмө кубанычтамын. Кантип жардам берейин?",
    "Салам! 😊 Мен байланышта элем. Эмне боюнча жардам керек?",
)

FIRST_USE_EFFECT = (
    "Гарантированно увидите эффект с первого раза 😁. "
    "После курса эффект будет постоянный✅😊"
)

EFFECT_CAPTION = "Вот такой эффект будет уже после первого применения 👇"

REVIEWS_CAPTION = "Вот отзывы наших клиентов 👇"

DELIVERY_BISHKEK = (
    "Да, у нас есть доставка, по Бишкеку осуществляется через Яндекс 🚚\n"
    "После оформления будет у вас через 20–40 минут ✅"
)

DELIVERY_OTHER_KG = (
    "Да, у нас есть доставка, осуществляется через Ылдам Экспресс 🚚✅\n"
    "За 1 день доставим прямо до дома."
)

# Клиент пишет из Казахстана/Узбекистана — своя валюта в рассылке; по
# умолчанию (КР, Россия и остальные) — сомы, это домашний рынок.
_KZ_CURRENCY_MARKERS = ("алмат", "астан", "шымкент", "казахст")
_UZ_CURRENCY_MARKERS = ("ташкент", "узбекист", "самарканд", "бухар", "андижан", "наманган", "фергана")

_DAILY_DISCOUNT_PRICE_LINES = {
    "KGS": "Со скидкой 1290 сом🇰🇬",
    "UZS": "Со скидкой 155000 сум🇺🇿",
    "KZT": "Со скидкой 7000 тенге🇰🇿",
}


def currency_for_history(texts: list[str]) -> str:
    normalized = " ".join(text.lower() for text in texts)
    if any(marker in normalized for marker in _KZ_CURRENCY_MARKERS):
        return "KZT"
    if any(marker in normalized for marker in _UZ_CURRENCY_MARKERS):
        return "UZS"
    return "KGS"


def daily_discount_offer(currency: str = "KGS") -> str:
    price_line = _DAILY_DISCOUNT_PRICE_LINES.get(currency, _DAILY_DISCOUNT_PRICE_LINES["KGS"])
    return (
        "Кстати, остались 3 пачки, и на них сделаем скидку 13%\n"
        f"{price_line}\n\n"
        "Если давно смущает желтизна зубов на фото или вживую — это можно "
        "быстро исправить, результат уже за 30 минут 😍\n"
        "Оформить вам одну пачку?"
    )

EFFECT_DURATION = "Эффект держится в среднем 3–5 месяцев 😊"

SINGLE_PIECE_POLICY = (
    "Поштучно, к сожалению, не продаём — только коробкой, 14 полосок на "
    "курс. При регулярном применении эффект закрепляется и держится дольше."
)

# Скидка даётся в два шага: сначала 13%, и только если клиент и после этого
# не берёт — 20%. Раньше первая же просьба сразу получала максимальные 20%,
# и торговаться дальше было нечем.
DISCOUNT_OFFER = (
    "Хорошо, сделаю для вас скидку 13% — 1290 сом вместо 1490 сом. "
    "Оформляем?"
)

DISCOUNT_OFFER_FINAL = (
    "Хорошо, для вас сделаю специальную цену — 1190 сом вместо 1490 сом. "
    "Такое предлагаю нечасто. Оформляем?"
)

# Третья и все следующие просьбы о скидке после 13% и 20% — фиксированный
# отказ, не ИИ-генерация: раньше отсюда разговор уходил в обычную генерацию,
# и модель сама решала, что ответить (риск придумать третью скидку или цену
# ниже 1190 вопреки правилу в MANAGER_PROMPT). Текст решает код по счётчику
# уступок, а не модель по своему усмотрению — так у неё нет возможности
# самой скорректировать цену.
DISCOUNT_REJECT = (
    "Дешевле, к сожалению, не могу — 1190 сом уже максимальная скидка. "
    "Оформляем по этой цене?"
)

USAGE_INSTRUCTIONS = (
    "1. Высушите зубы, отклейте полоски и плотно нанесите их на верхние и нижние зубы.\n"
    "2. Засеките 30 минут.\n"
    "3. Старайтесь держать рот открытым, чтобы не скапливалась влага.\n"
    "4. Снимите прозрачную плёнку, удалите отбеливатель салфеткой и смойте остатки."
)

THANK_YOU_ORDER = (
    "Спасибо! Заказ принят ✅\n\n"
    "Сейчас передам менеджеру по продажам. Для связи: 0999 000 845."
)

SAFETY_COMPOSITION = (
    "У нас абсолютно безопасный состав, который никак не вредит зубной эмали. "
    "Результат, который я вам отправлял, — это мои собственные зубы. "
    "Если хотите, могу отправить состав."
)

BRACES_REPLY = (
    "Наши полоски эластичные, поэтому многие люди с брекетами тоже используют их."
)

DOUBT_RESULT_REPLY = (
    "Вы можете зайти в наш Instagram и посмотреть результаты других клиентов. "
    "Мы гарантируем эффект уже после первого применения."
)

NO_RESULT_REFUND_REPLY = "Если результата не будет, мы вернём деньги."

OFFLINE_STORE_COD_REPLY = "Можно сделать оплату при получении, просто у нас онлайн-магазин."

ADDRESS_COD_REPLY = "У нас онлайн-магазин, но можем оформить оплату при получении."

CLARIFY_VAGUE_DATE_REPLY = "Хорошо 😊 Подскажите примерно какого числа вам лучше написать?"

IDLE_FOLLOWUP = (
    "Здравствуйте! 😊 Хотела уточнить, успели ли вы ознакомиться со {product_name}.\n"
    "Если у вас остались вопросы, я с удовольствием отвечу на них. Многие клиенты "
    "уже оценили результат, поэтому, если вы всё ещё думаете, могу подробнее "
    "рассказать о применении и эффекте. ✨"
)

DISCOUNT_FOLLOWUP = (
    "Кстати, сейчас на товар действует скидка 20%. Вместо 1490 сом вы можете "
    "купить его за 1190 сом. Скидка действует только сегодня. Оформить заказ?"
)

AFTER_CONSULT_FOLLOWUP = (
    "Добрый день. Хотела узнать, удалось ли вам определиться после нашей "
    "консультации? Могу ещё раз коротко сравнить подходящие варианты."
)

LAST_MESSAGE_FOLLOWUP = (
    "{client_name}, больше не буду отвлекать. Если вопрос по {product_name} "
    "ещё актуален, просто напишите — продолжим с того места, где остановились."
)

ORDER_INCOMPLETE_FOLLOWUP = (
    "Здравствуйте. Вижу, что мы не закончили оформление {product_name}. "
    "Хотите продолжить или заказ уже неактуален?"
)

PROMISED_FOLLOWUP = (
    "Здравствуйте 😊 Кстати, сегодня на SmileKit действует скидка 20% — набор "
    "можно оформить за 1190 сом. Скидка действует только сегодня. Вам оформить доставку?"
)

THINKING_REPLY = (
    "Хорошо, понимаю. Подскажите только, что пока останавливает: цена, "
    "сомнения в результате или хотите сравнить с другими вариантами?"
)

EXPENSIVE_REPLY = (
    "Понимаю вас. Могу подобрать более доступный вариант с похожим "
    "назначением. На какую сумму вы примерно рассчитываете?"
)

PRODUCT_NAME = "Smilekit"

PAYMENT_QR_MESSAGE = (
    "Чтобы оформить заказ, пожалуйста, отправьте 😊\n\n"
    "• чек об оплате;\n\n"
    "• адрес доставки;\n\n"
    "• номер телефона."
)

# Клиент прямо спросил, куда платить / реквизиты — раньше это уходило в
# обычную генерацию ИИ, и тот просто называл номер менеджера вместо того,
# чтобы сразу дать QR. Теперь это отдельный детерминированный сценарий.
PAYMENT_INFO_REQUEST = PAYMENT_QR_MESSAGE

# Несколько формулировок дожима по оплате — чтобы подряд идущие напоминания
# не выглядели как три одинаковых сообщения подряд.
AWAITING_PAYMENT_REMINDERS = (
    "Здравствуйте! Оплатили заказ? Жду фото чека, чтобы передать его в доставку 🙏",
    "Добрый день! Уточните, пожалуйста, получилось оплатить? Как только пришлёте чек — сразу оформлю доставку.",
    "Здравствуйте, ещё актуально? Если оплатили — пришлите, пожалуйста, чек, и я оформлю заказ.",
)

RECEIPT_MISMATCH = (
    "Не получилось распознать оплату на чеке. Проверьте, пожалуйста, что "
    "сумма верная, и пришлите чек ещё раз."
)


def receipt_mismatch_reply(result: dict) -> str:
    if result.get("is_real_receipt_screenshot") and result.get("amount_ok") and result.get("recipient_ok") and not result.get("date_ok"):
        return (
            "Сумма и получатель на чеке верные, но дата чека не сегодняшняя. "
            "Пришлите, пожалуйста, чек текущей оплаты."
        )
    if result.get("is_real_receipt_screenshot") and not result.get("amount_ok"):
        return "Чек распознан, но сумма оплаты не совпадает. Проверьте сумму и пришлите чек ещё раз."
    if result.get("is_real_receipt_screenshot") and not result.get("recipient_ok"):
        return "Чек распознан, но получатель не совпадает. Проверьте реквизиты и пришлите чек ещё раз."
    return RECEIPT_MISMATCH

# Клиент написал с явным недовольством — не оправдываемся и не додумываем
# причину, а прямо просим её назвать, чтобы было что передать в CRM.
COMPLAINT_PROMPT = "Подскажите, пожалуйста, что вас не устроило — для улучшения нашего сервиса."

# Опт/сотрудничество/реклама — не розничный сценарий, передаём человеку.
COOPERATION_INQUIRY_REPLY = (
    "Спасибо за интерес! Такими вопросами у нас занимается отдельный менеджер — "
    "уже передал ему ваше сообщение, он скоро с вами свяжется."
)


def fill_nudge_template(template: str, client_name: str | None) -> str:
    text = template.replace("{product_name}", PRODUCT_NAME)
    if client_name:
        return text.replace("{client_name}", client_name)
    return text.replace(", {client_name}", "").replace("{client_name}, ", "")


# Города Бишкек/Токмок — доставка Яндекс за 30 минут; остальные города КР —
# Ылдам Экспресс. Список синхронизирован с _CITY_MARKERS в media.py.
BISHKEK_MARKERS = ("бишкек", "токмок")
# "ош" — не в этом кортеже: короткая подстрока ловила бы "хорошо", "тошнит",
# "картошка" и т.п. как упоминание города. Матчится отдельно, через regex
# с границами слова (см. _OSH_RE ниже) — остальные названия достаточно
# длинные и специфичные, чтобы не давать ложных срабатываний подстрокой.
OTHER_KG_CITY_MARKERS = (
    "джалал-абад", "джалалабад", "каракол", "нарын", "талас", "баткен", "манас",
)
_OSH_RE = re.compile(r"\bош\b", re.IGNORECASE | re.UNICODE)
KZ_ALMATY_MARKERS = ("алмат",)
KZ_OTHER_MARKERS = ("астан",)
UZ_TASHKENT_MARKERS = ("ташкент",)
UZ_OTHER_MARKERS = ("узбекист", "самарканд", "бухар", "андижан", "наманган", "фергана")
RU_MARKERS = ("москв", "росси")

DELIVERY_KZ_ALMATY = (
    "Да, у нас есть доставка в Алматы 😇📍\n"
    "В течение дня доставим или же в течение 30–40 минут через Яндекс."
)
DELIVERY_KZ_OTHER = "Да, у нас есть доставка, осуществляется через СДЭК ✅"
DELIVERY_UZ_TASHKENT = (
    "Да, у нас есть доставка в Ташкенте 😇📍\n"
    "В течение дня доставим или же в течение 30–40 минут через Яндекс."
)
DELIVERY_UZ_OTHER = (
    "Да, у нас есть доставка. В течение 1 дня доставим.\n"
    "Осуществляется через БТС Экспресс 🚚"
)
DELIVERY_RU = "Да, у нас есть доставка, осуществляется через СДЭК ✅"


def delivery_template_for_city(text: str) -> str | None:
    """Шаблон доставки по упомянутому городу/стране. None — не распознан
    (например иностранный, не входящий в список ниже) или не назван вообще."""
    normalized = " ".join(text.lower().split())
    # Живой баг: опечатка «бищкека» (щ вместо ш) не совпадала с "бишкек" —
    # клиент выпадал из готового сценария в свободный ответ ИИ, который сам
    # придумывал вопрос вроде «вам удобно оформить заказ сейчас?» вместо
    # утверждённого текста. Складываем щ→ш только для Бишкека/Токмока —
    # самая частая опечатка на этих двух словах.
    if any(marker in normalized.replace("щ", "ш") for marker in BISHKEK_MARKERS):
        return DELIVERY_BISHKEK
    if _OSH_RE.search(normalized) or any(marker in normalized for marker in OTHER_KG_CITY_MARKERS):
        return DELIVERY_OTHER_KG
    if any(marker in normalized for marker in KZ_ALMATY_MARKERS):
        return DELIVERY_KZ_ALMATY
    if any(marker in normalized for marker in KZ_OTHER_MARKERS):
        return DELIVERY_KZ_OTHER
    if any(marker in normalized for marker in UZ_TASHKENT_MARKERS):
        return DELIVERY_UZ_TASHKENT
    if any(marker in normalized for marker in UZ_OTHER_MARKERS):
        return DELIVERY_UZ_OTHER
    if any(marker in normalized for marker in RU_MARKERS):
        return DELIVERY_RU
    return None


# ---------------------------------------------------------------------------
# Локализация (RU/KY/KK/UZ/EN) для готовых текстов, отправляемых без ИИ.
# Черновики ИИ переводятся самой моделью (см. MANAGER_PROMPT) — здесь только
# зашитые в код шаблоны первого контакта, доставки, скидок и оплаты.
# ---------------------------------------------------------------------------

LANGUAGES = ("ru", "ky", "kk", "uz", "en")

_KAZAKH_ONLY_LETTERS = set("әғқұhі")
_CYRILLIC_RE = re.compile(r"[а-яёіңөүғqk]", re.IGNORECASE | re.UNICODE)
_LATIN_RE = re.compile(r"[a-z]", re.IGNORECASE)


def detect_language(text: str = "", language_code: str | None = None) -> str:
    """Определяет язык клиента: сперва по языку интерфейса Telegram
    (message.from_user.language_code), затем по тексту сообщения как fallback."""
    code = (language_code or "").lower()
    if code.startswith("en"):
        return "en"
    if code.startswith("kk") or code.startswith("kz"):
        return "kk"
    if code.startswith("uz"):
        return "uz"
    lowered = (text or "").lower()
    if any(ch in lowered for ch in _KAZAKH_ONLY_LETTERS):
        return "kk"
    if "салам" in lowered:
        return "ky"
    if lowered and _LATIN_RE.search(lowered) and not _CYRILLIC_RE.search(lowered):
        # Латиница без кириллицы — считаем узбекским (современная узбекская
        # письменность латинская); отличить от английского текстом сложно,
        # поэтому окончательное слово остаётся за language_code выше.
        return "uz"
    return "ru"


def t(mapping: dict, lang: str):
    """Достаёт перевод из словаря {"ru": ..., "ky": ..., ...} с фолбэком на ru."""
    return mapping.get(lang, mapping["ru"])


PRODUCT_INFO_I18N = {
    "ru": PRODUCT_INFO,
    "ky": (
        "Салам! 😊\n\n"
        "SmileKit тиштериңизди биринчи колдонуудан эле кыйла агартууга жардам берет.\n\n"
        "📦 Кутучада 14 агартуучу тилке бар — бул толук курс.\n\n"
        "💰 БААСЫ — 1490 СОМ.🇰🇬"
    ),
    "kk": (
        "Сәлем! 😊\n\n"
        "SmileKit тістеріңізді бірінші қолданыстан-ақ айтарлықтай ағартуға көмектеседі.\n\n"
        "📦 Қорапта 14 ағартқыш жолақша бар — бұл толық курс.\n\n"
        "💰 БАҒАСЫ — 1490 сом.🇰🇬"
    ),
    "uz": (
        "Salom! 😊\n\n"
        "SmileKit tishlaringizni birinchi qo'llashdanoq sezilarli darajada oqartirishga yordam beradi.\n\n"
        "📦 Qutida 14 ta oqartiruvchi tasma bor — bu to'liq kurs.\n\n"
        "💰 NARXI — 1490 som.🇰🇬"
    ),
    "en": (
        "Hello! 😊\n\n"
        "SmileKit noticeably whitens your teeth from the very first use.\n\n"
        "📦 The box contains 14 whitening strips — a full course.\n\n"
        "💰 PRICE — 1490 KGS.🇰🇬"
    ),
}

CITY_QUESTION_I18N = {
    "ru": CITY_QUESTION,
    "ky": "Сураныч, кайсы шаардан болосуз?",
    "kk": "Айтыңызшы, қай қаладансыз?",
    "uz": "Marhamat, qaysi shahardansiz?",
    "en": "Could you tell me which city you're in?",
}

FIRST_USE_EFFECT_I18N = {
    "ru": FIRST_USE_EFFECT,
    "ky": "Биринчи колдонуудон эле натыйжаны 100% көрөсүз 😁. Курстан кийин эффект туруктуу болот✅😊",
    "kk": "Бірінші қолданыстан-ақ нәтижені міндетті түрде көресіз 😁. Курстан кейін әсер тұрақты болады✅😊",
    "uz": "Birinchi qo'llashdanoq natijani albatta ko'rasiz 😁. Kursdan keyin ta'sir doimiy bo'ladi✅😊",
    "en": "You're guaranteed to see the effect from the very first use 😁. After the full course, the effect stays permanent ✅😊",
}

EFFECT_CAPTION_I18N = {
    "ru": EFFECT_CAPTION,
    "ky": "Биринчи колдонуудон кийин мындай эффект болот 👇",
    "kk": "Бірінші қолданыстан кейін осындай нәтиже болады 👇",
    "uz": "Birinchi qo'llashdan keyin shunday natija bo'ladi 👇",
    "en": "Here's the effect you'll get after just the first use 👇",
}

REVIEWS_CAPTION_I18N = {
    "ru": REVIEWS_CAPTION,
    "ky": "Биздин кардарлардын пикирлери 👇",
    "kk": "Клиенттеріміздің пікірлері 👇",
    "uz": "Mijozlarimizning fikr-mulohazalari 👇",
    "en": "Here are reviews from our customers 👇",
}

DELIVERY_BISHKEK_I18N = {
    "ru": DELIVERY_BISHKEK,
    "ky": "Ооба, жеткирүү бар, Бишкек боюнча Яндекс аркылуу жеткирилет 🚚\nБуйрутма таштагандан кийин 20–40 мүнөттөн кийин алат ✅",
    "kk": "Иә, жеткізу бар, Бішкек бойынша Яндекс арқылы жеткіземіз 🚚\nТапсырыс бергеннен кейін 20–40 минутта жеткіземіз ✅",
    "uz": "Ha, yetkazib berish bor, Bishkek bo'ylab Yandex orqali amalga oshiriladi 🚚\nBuyurtma bergandan so'ng 20–40 daqiqada yetkazamiz ✅",
    "en": "Yes, we deliver — within Bishkek it's via Yandex 🚚\nYou'll get it in 20–40 minutes after ordering ✅",
}

DELIVERY_OTHER_KG_I18N = {
    "ru": DELIVERY_OTHER_KG,
    "ky": "Ооба, жеткирүү бар, Ылдам Экспресс аркылуу жеткирилет 🚚✅\n1 күндүн ичинде так эле үйгө жеткиребиз.",
    "kk": "Иә, жеткізу бар, Ылдам Экспресс арқылы жеткіземіз 🚚✅\n1 күн ішінде тікелей үйіңізге жеткіземіз.",
    "uz": "Ha, yetkazib berish bor, Yldam Ekspress orqali amalga oshiriladi 🚚✅\n1 kun ichida to'g'ridan-to'g'ri uyingizga yetkazib beramiz.",
    "en": "Yes, we deliver via Yldam Express 🚚✅\nWe'll deliver straight to your door within 1 day.",
}

DELIVERY_KZ_ALMATY_I18N = {
    "ru": DELIVERY_KZ_ALMATY,
    "ky": "Ооба, Алматыга жеткирүү бар 😇📍\nКүн ичинде же Яндекс аркылуу 30–40 мүнөттө жеткиребиз.",
    "kk": "Иә, Алматыға жеткізу бар 😇📍\nКүн ішінде немесе Яндекс арқылы 30–40 минутта жеткіземіз.",
    "uz": "Ha, Almatiga yetkazib berish bor 😇📍\nKun davomida yoki Yandex orqali 30–40 daqiqada yetkazamiz.",
    "en": "Yes, we deliver to Almaty 😇📍\nWithin the day, or in 30–40 minutes via Yandex.",
}

DELIVERY_KZ_OTHER_I18N = {
    "ru": DELIVERY_KZ_OTHER,
    "ky": "Ооба, жеткирүү бар, СДЭК аркылуу жеткирилет ✅",
    "kk": "Иә, жеткізу бар, СДЭК арқылы жеткіземіз ✅",
    "uz": "Ha, yetkazib berish bor, SDEK orqali amalga oshiriladi ✅",
    "en": "Yes, we deliver via CDEK ✅",
}

DELIVERY_UZ_TASHKENT_I18N = {
    "ru": DELIVERY_UZ_TASHKENT,
    "ky": "Ооба, Ташкентке жеткирүү бар 😇📍\nКүн ичинде же Яндекс аркылуу 30–40 мүнөттө жеткиребиз.",
    "kk": "Иә, Ташкентке жеткізу бар 😇📍\nКүн ішінде немесе Яндекс арқылы 30–40 минутта жеткіземіз.",
    "uz": "Ha, Toshkentga yetkazib berish bor 😇📍\nKun davomida yoki Yandex orqali 30–40 daqiqada yetkazamiz.",
    "en": "Yes, we deliver to Tashkent 😇📍\nWithin the day, or in 30–40 minutes via Yandex.",
}

DELIVERY_UZ_OTHER_I18N = {
    "ru": DELIVERY_UZ_OTHER,
    "ky": "Ооба, жеткирүү бар. 1 күндүн ичинде жеткиребиз.\nБТС Экспресс аркылуу ишке ашырылат 🚚",
    "kk": "Иә, жеткізу бар. 1 күн ішінде жеткіземіз.\nБТС Экспресс арқылы жүзеге асырылады 🚚",
    "uz": "Ha, yetkazib berish bor. 1 kun ichida yetkazamiz.\nBTS Ekspress orqali amalga oshiriladi 🚚",
    "en": "Yes, we deliver. We'll deliver within 1 day.\nHandled via BTS Express 🚚",
}

DELIVERY_RU_I18N = {
    "ru": DELIVERY_RU,
    "ky": "Ооба, жеткирүү бар, СДЭК аркылуу жеткирилет ✅",
    "kk": "Иә, жеткізу бар, СДЭК арқылы жеткіземіз ✅",
    "uz": "Ha, yetkazib berish bor, SDEK orqali amalga oshiriladi ✅",
    "en": "Yes, we deliver via CDEK ✅",
}

DISCOUNT_OFFER_I18N = {
    "ru": DISCOUNT_OFFER,
    "ky": "Макул, сиз үчүн 13% арзандатуу жасайм — 1490 сомдун ордуна 1290 сом. Тариптейбизби?",
    "kk": "Жарайды, сіз үшін 13% жеңілдік жасаймын — 1490 сом орнына 1290 сом. Рәсімдейміз бе?",
    "uz": "Yaxshi, siz uchun 13% chegirma qilaman — 1490 som o'rniga 1290 som. Rasmiylashtiramizmi?",
    "en": "Alright, I'll give you a 13% discount — 1290 KGS instead of 1490 KGS. Shall we place the order?",
}

DISCOUNT_OFFER_FINAL_I18N = {
    "ru": DISCOUNT_OFFER_FINAL,
    "ky": "Макул, сиз үчүн атайын баа жасайм — 1490 сомдун ордуна 1190 сом. Мындайды сейрек сунуштайм. Тариптейбизби?",
    "kk": "Жарайды, сіз үшін арнайы баға жасаймын — 1490 сом орнына 1190 сом. Мұны сирек ұсынамын. Рәсімдейміз бе?",
    "uz": "Yaxshi, siz uchun maxsus narx qilaman — 1490 som o'rniga 1190 som. Bunday narxni kamdan-kam taklif qilaman. Rasmiylashtiramizmi?",
    "en": "Alright, I'll offer you a special price — 1190 KGS instead of 1490 KGS. I don't offer this often. Shall we place the order?",
}

DISCOUNT_REJECT_I18N = {
    "ru": DISCOUNT_REJECT,
    "ky": "Тилекке каршы, андан арзан кыла албайм — 1190 сом эң чоң арзандатуу. Ушул баада тариптейбизби?",
    "kk": "Өкінішке орай, одан арзан ете алмаймын — 1190 сом ең үлкен жеңілдік. Осы бағамен рәсімдейміз бе?",
    "uz": "Afsuski, bundan arzon qila olmayman — 1190 som eng maksimal chegirma. Shu narxda rasmiylashtiramizmi?",
    "en": "Unfortunately I can't go any lower — 1190 KGS is already the maximum discount. Shall we place the order at this price?",
}

PAYMENT_QR_MESSAGE_I18N = {
    "ru": PAYMENT_QR_MESSAGE,
    "ky": "Буйрутманы тариптеш үчүн, сураныч, жөнөтүңүз 😊\n\n• төлөм чеги;\n\n• жеткирүү дареги;\n\n• телефон номери.",
    "kk": "Тапсырысты рәсімдеу үшін, өтінемін, жіберіңіз 😊\n\n• төлем түбіртегі;\n\n• жеткізу мекенжайы;\n\n• телефон нөмірі.",
    "uz": "Buyurtmani rasmiylashtirish uchun, iltimos, yuboring 😊\n\n• to'lov cheki;\n\n• yetkazib berish manzili;\n\n• telefon raqami.",
    "en": "To place your order, please send 😊\n\n• payment receipt;\n\n• delivery address;\n\n• phone number.",
}

THANK_YOU_ORDER_I18N = {
    "ru": THANK_YOU_ORDER,
    "ky": "Рахмат! Буйрутма кабыл алынды ✅\n\nАзыр сатуу менеджерине өткөрүп берем. Байланыш үчүн: 0999 000 845.",
    "kk": "Рахмет! Тапсырыс қабылданды ✅\n\nҚазір сату менеджеріне беремін. Байланыс үшін: 0999 000 845.",
    "uz": "Rahmat! Buyurtma qabul qilindi ✅\n\nHozir savdo menejeriga uzataman. Aloqa uchun: 0999 000 845.",
    "en": "Thank you! Your order has been received ✅\n\nI'll pass it to our sales manager now. Contact: 0999 000 845.",
}

COMPLAINT_PROMPT_I18N = {
    "ru": COMPLAINT_PROMPT,
    "ky": "Сураныч, эмне жакпай калганын айтып бериңизчи — кызматыбызды жакшыртуу үчүн.",
    "kk": "Өтінемін, не ұнамағанын айтып беріңізші — қызметімізді жақсарту үшін.",
    "uz": "Iltimos, nima yoqmaganini ayting — xizmatimizni yaxshilash uchun.",
    "en": "Could you tell me what you weren't happy with — it'll help us improve our service.",
}

COOPERATION_INQUIRY_REPLY_I18N = {
    "ru": COOPERATION_INQUIRY_REPLY,
    "ky": "Кызыгууңуз үчүн рахмат! Мындай суроолор менен өзүнчө менеджер алектенет — билдирүүңүздү ага өткөрүп бердим, жакында сиз менен байланышат.",
    "kk": "Қызығушылығыңыз үшін рахмет! Мұндай сұрақтармен бөлек менеджер айналысады — хабарламаңызды оған жеткіздім, жақында сізбен байланысады.",
    "uz": "Qiziqishingiz uchun rahmat! Bunday savollar bilan alohida menejer shug'ullanadi — xabaringizni unga uzatdim, tez orada siz bilan bog'lanadi.",
    "en": "Thanks for your interest! A dedicated manager handles these questions — I've passed your message along, they'll reach out to you soon.",
}

# Цепочка дожима после блока результатов (3/10/15/15/15 минут) — тексты по
# языкам в том же порядке, что RESULTS_FOLLOW_UP_STEPS в __main__.py.
RESULTS_FOLLOW_UP_TEXTS_I18N = {
    "ru": (
        "Вам оформить доставку?",
        "Для вас актуально?",
        DISCOUNT_OFFER,
        DISCOUNT_OFFER_FINAL,
        "Кстати, у нас осталось буквально 2 упаковки, поэтому мы их отдаем по скидке 10% ☺️\nОформить вам доставку ?)",
    ),
    "ky": (
        "Сизге жеткирүүнү тариптеп берейинби?",
        "Сиз үчүн азырынча актуалдуубу?",
        DISCOUNT_OFFER_I18N["ky"],
        DISCOUNT_OFFER_FINAL_I18N["ky"],
        "Айтпакчы, бизде болгону 2 упаковка калды, ошондуктан аларды 10% арзандатуу менен беребиз ☺️\nСизге жеткирүүнү тариптеп берейинби?)",
    ),
    "kk": (
        "Сізге жеткізуді рәсімдеп берейін бе?",
        "Сіз үшін әлі де өзекті ме?",
        DISCOUNT_OFFER_I18N["kk"],
        DISCOUNT_OFFER_FINAL_I18N["kk"],
        "Айтпақшы, бізде небәрі 2 қаптама қалды, сондықтан оларды 10% жеңілдікпен береміз ☺️\nСізге жеткізуді рәсімдеп берейін бе?)",
    ),
    "uz": (
        "Sizga yetkazib berishni rasmiylashtirib qo'yaymi?",
        "Siz uchun hali ham dolzarbmi?",
        DISCOUNT_OFFER_I18N["uz"],
        DISCOUNT_OFFER_FINAL_I18N["uz"],
        "Aytgancha, bizda atigi 2 ta qadoq qoldi, shuning uchun ularni 10% chegirma bilan beryapmiz ☺️\nSizga yetkazib berishni rasmiylashtirib qo'yaymi?)",
    ),
    "en": (
        "Would you like me to arrange delivery for you?",
        "Is this still relevant for you?",
        DISCOUNT_OFFER_I18N["en"],
        DISCOUNT_OFFER_FINAL_I18N["en"],
        "By the way, we only have 2 packs left, so we're offering them with a 10% discount ☺️\nShall I arrange delivery for you?)",
    ),
}


_DELIVERY_I18N_BY_RU = {
    DELIVERY_BISHKEK: DELIVERY_BISHKEK_I18N,
    DELIVERY_OTHER_KG: DELIVERY_OTHER_KG_I18N,
    DELIVERY_KZ_ALMATY: DELIVERY_KZ_ALMATY_I18N,
    DELIVERY_KZ_OTHER: DELIVERY_KZ_OTHER_I18N,
    DELIVERY_UZ_TASHKENT: DELIVERY_UZ_TASHKENT_I18N,
    DELIVERY_UZ_OTHER: DELIVERY_UZ_OTHER_I18N,
    DELIVERY_RU: DELIVERY_RU_I18N,
}


def localize_delivery(delivery_text: str | None, lang: str) -> str | None:
    """delivery_template_for_city() всегда возвращает русский текст (по нему
    также логируется регион) — здесь переводим его под язык клиента."""
    if delivery_text is None:
        return None
    mapping = _DELIVERY_I18N_BY_RU.get(delivery_text)
    return t(mapping, lang) if mapping else delivery_text
