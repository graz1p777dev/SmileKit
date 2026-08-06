import re


MANAGER_CONTACT = "0999000845"
CONTACT_TEXT = (
    "Для оформления можно связаться с менеджером Smilekit: 0999000845."
)

_ORDER_INTENT = re.compile(
    r"\b("
    r"хочу\s+(?:заказать|купить|взять)|"
    r"(?:как|где)\s+(?:заказать|купить|оформить)|"
    r"(?:закажу|заказать|оформить\s+заказ|беру|возьму)|"
    r"заказ\s+(?:берейин|кылайын)|сатып\s+ал|алайын|"
    r"buyurtma|sotib\s+ol"
    r")\b",
    re.IGNORECASE,
)


def wants_to_order(text: str) -> bool:
    return bool(_ORDER_INTENT.search(text))


def ensure_manager_contact(message: str, response: str) -> str:
    if not wants_to_order(message):
        return response
    digits = re.sub(r"\D", "", response)
    if MANAGER_CONTACT in digits or MANAGER_CONTACT[-9:] in digits:
        return response
    return f"{response.rstrip()}\n\n{CONTACT_TEXT}"


_THINKING_OBJECTION = re.compile(
    r"(?:я\s+)?подума(?:ю|ем)|над(?:о|а)\s+подумать|дай(?:те)?\s+подумать|"
    r"мне\s+нужно\s+время|рассмотрю\s+ещ[её]",
    re.IGNORECASE,
)
_EXPENSIVE_OBJECTION = re.compile(
    r"дорог(?:о|овато|ая|ой)|не\s+потяну|не\s+укладываюсь\s+в\s+бюджет|"
    r"дешевле\s+есть|скидк[уи]\s+можно",
    re.IGNORECASE,
)


def classify_objection(text: str) -> str | None:
    """Явное возражение клиента — готовый ответ вместо генерации ИИ."""
    if _THINKING_OBJECTION.search(text):
        return "thinking"
    if _EXPENSIVE_OBJECTION.search(text):
        return "expensive"
    return None


_PROMISED_DAYS = re.compile(r"через\s+(\d+)\s*д(?:ень|ня|ней)\b", re.IGNORECASE)
_PROMISED_WEEK = re.compile(r"через\s+недел|на\s+следующей\s+недел", re.IGNORECASE)
_PROMISED_DAY_AFTER_TOMORROW = re.compile(r"послезавтра", re.IGNORECASE)
_PROMISED_TOMORROW = re.compile(r"завтра", re.IGNORECASE)


def promised_delay_hours(text: str) -> float | None:
    """Клиент сам назвал момент, когда вернётся («завтра зп — закажу») —
    напоминание ставится на этот момент вместо обычной цепочки простоя."""
    match = _PROMISED_DAYS.search(text)
    if match:
        return float(match.group(1)) * 24
    if _PROMISED_DAY_AFTER_TOMORROW.search(text):
        return 48.0
    if _PROMISED_TOMORROW.search(text):
        return 24.0
    if _PROMISED_WEEK.search(text):
        return 7 * 24.0
    return None


# Бишкек = UTC+6 — своя копия константы, чтобы sales.py не тянул зависимость
# от __main__.py ради одного числа.
_BISHKEK_UTC_OFFSET_SECONDS = 6 * 3600
_CLOCK_TIME = re.compile(
    r"(?:^|\s)(?:к|в|до)\s+(\d{1,2})(?:[:\.](\d{2}))?\s*"
    r"(вечера|утра|дня|ночи|часам?|часов)?",
    re.IGNORECASE,
)


def promised_clock_time_hours(text: str, now: float) -> float | None:
    """Клиент назвал конкретное время («напишу к 18:00») — часы до этого
    момента по Бишкеку, с переносом на завтра, если время уже прошло.

    Без минут и без слова-уточнения (вечера/утра/...) число слишком
    двусмысленно («в 2 раза», «в 5 моделях») — такие совпадения игнорируются.
    """
    match = _CLOCK_TIME.search(text)
    if not match:
        return None
    hour_str, minute_str, qualifier = match.groups()
    if minute_str is None and not qualifier:
        return None
    hour = int(hour_str)
    minute = int(minute_str) if minute_str is not None else 0
    if hour > 23 or minute > 59:
        return None
    qualifier = (qualifier or "").lower()
    if qualifier in ("вечера", "ночи") and hour < 12:
        hour += 12
    if hour > 23:
        return None
    shifted_now = now + _BISHKEK_UTC_OFFSET_SECONDS
    day_start = shifted_now - (shifted_now % 86400)
    target = day_start + hour * 3600 + minute * 60
    if target <= shifted_now:
        target += 86400
    target_utc = target - _BISHKEK_UTC_OFFSET_SECONDS
    return (target_utc - now) / 3600


_PAYMENT_INFO_REQUEST = re.compile(
    r"реквизит|куда\s+(?:скинуть|перевести|переводить|оплатить|платить)|"
    r"номер\s+(?:карты|счёта|счета)|как\s+оплатить|способ\s+оплаты",
    re.IGNORECASE,
)


def wants_payment_info(text: str) -> bool:
    """Клиент прямо спрашивает, куда/как платить — сразу шлём QR, а не
    номер менеджера через свободную генерацию."""
    return bool(_PAYMENT_INFO_REQUEST.search(text))


_COMPLAINT_MARKERS = re.compile(
    r"развод|обман(?:ули)?|кинул|мошенник|верните\s+деньги|хочу\s+возврат|"
    r"ужасн(?:о|ый|ая)|отвратительн|никогда\s+(?:больше\s+)?не\s+куплю|"
    r"плохо\s+сработал|не\s+помогл|это\s+не\s+то|разочарован",
    re.IGNORECASE,
)


def is_complaint(text: str) -> bool:
    """Явное недовольство — не додумываем причину, прямо спрашиваем её и
    сохраняем в CRM, чтобы менеджер разобрался."""
    return bool(_COMPLAINT_MARKERS.search(text))
