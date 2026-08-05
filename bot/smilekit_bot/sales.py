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
