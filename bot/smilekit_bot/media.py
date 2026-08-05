from pathlib import Path

from aiogram.types import FSInputFile, InputMediaPhoto


RESULTS_DIR = Path(__file__).resolve().parent.parent / "assets" / "results"

_PHOTO_REQUESTS = (
    "покажи фото",
    "показать фото",
    "есть фото",
    "фото результата",
    "фото результатов",
    "результаты клиентов",
    "результат клиентов",
    "до и после",
    "до/после",
    "фото отзыв",
    "сүрөт көрсөт",
    "жыйынтык сүрөт",
    "натыйжа сүрөт",
)

_COMPLAINT_MARKERS = (
    "боль",
    "болит",
    "жжение",
    "раздраж",
    "воспален",
    "кров",
    "жалоб",
    "плохой результат",
    "не помог",
    "не работает",
)

_CITY_MARKERS = (
    "бишкек", "токмок", "ош", "джалал-абад", "джалалабад", "каракол", "нарын",
    "талас", "баткен", "алмат", "астан", "ташкент", "москв", "росси",
)
_PRICE_MARKERS = ("сколько стоит", "какая цена", "цена?", "1490 сом")


def wants_result_photos(text: str) -> bool:
    normalized = " ".join(text.lower().split())
    if any(marker in normalized for marker in _COMPLAINT_MARKERS):
        return False
    return any(phrase in normalized for phrase in _PHOTO_REQUESTS)


def should_send_results_after_city(text: str, history: list[str]) -> bool:
    normalized = " ".join(text.lower().split())
    if any(marker in normalized for marker in _COMPLAINT_MARKERS):
        return False
    has_city = any(marker in normalized for marker in _CITY_MARKERS)
    prior_text = " ".join(item.lower() for item in history)
    return has_city and any(marker in prior_text for marker in _PRICE_MARKERS)


def result_photo_group() -> list[InputMediaPhoto]:
    return [
        InputMediaPhoto(
            media=FSInputFile(RESULTS_DIR / "before.jpg"),
        ),
        InputMediaPhoto(
            media=FSInputFile(RESULTS_DIR / "after.jpg"),
        ),
        InputMediaPhoto(
            media=FSInputFile(RESULTS_DIR / "resultotzyv.jpeg"),
        ),
    ]
