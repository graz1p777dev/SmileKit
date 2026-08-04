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
