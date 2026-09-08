#!/usr/bin/env python3
"""Локальный CLI-тест роутера тестового бота — без Telegram-токена и без
сети. Два режима:
  python -m smilekit_bot_template_test.chat_cli            — интерактивный чат
  python -m smilekit_bot_template_test.chat_cli --batch     — прогон сценариев,
                                                                печатает всё, exit 1 при ошибке
"""
import sys

from .router import ChatState, route

SCENARIOS: list[tuple[str, list[str]]] = [
    ("greeting_price_bishkek", ["Здравствуйте", "Бишкек", "Оформляйте"]),
    ("greeting_vague_word", ["Улыбка"]),
    ("almaty_flow", ["Сколько стоит?", "Алматы"]),
    ("tashkent_flow", ["Здравствуйте, сколько стоит?", "Ташкент"]),
    ("russia_flow", ["сколько стоит", "Москва"]),
    ("unsupported_country", ["сколько стоит", "Германия"]),
    ("discount_ladder", ["сколько стоит", "дорого", "всё равно дорого", "а дешевле никак"]),
    ("pickup_question", ["А самовывоз есть?"]),
    ("address_question", ["Адрес?"]),
    ("safety_harm", ["А не вредно ли для эмали?"]),
    ("safety_safe", ["А состав безопасный?"]),
    ("scam_doubt", ["А вы не мошенники?"]),
    ("complaint", ["Это ужасно, ничего не помогло"]),
    ("cooperation", ["Хочу стать вашим дилером"]),
    ("effect_duration", ["На сколько хватает одной коробки?"]),
    ("pay_on_delivery", ["Хочу оплатить после того как приеду"]),
    ("order_intent", ["Хочу заказать"]),
    ("returning_greeting", ["Здравствуйте", "Здравствуйте"]),
    ("unrecognized_midway", ["Здравствуйте", "Бишкек", "а можно про вашу маму спросить"]),
]


def run_batch() -> int:
    had_error = False
    for name, messages in SCENARIOS:
        print(f"\n=== {name} ===")
        state = ChatState()
        for text in messages:
            result = route(text, state)
            print(f"Клиент: {text}")
            for part in result.texts:
                print(f"Бот: {part}")
            if result.send_results_block:
                print("Бот: [фото до/после]")
            if result.voice:
                print(f"Бот: [голосовое {result.voice}]")
        if not state.history:
            had_error = True
            print("ОШИБКА: пустая история после сценария")
    return 1 if had_error else 0


def run_interactive() -> int:
    state = ChatState()
    print("Тестовый бот SmileKit (CLI, без Telegram). /reset — сброс, /exit — выход.\n")
    while True:
        try:
            text = input("Клиент: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        if text == "/exit":
            break
        if text == "/reset":
            state = ChatState()
            print("— диалог сброшен —\n")
            continue
        result = route(text, state)
        for part in result.texts:
            print(f"Бот: {part}")
        if result.send_results_block:
            print("Бот: [фото до/после]")
        if result.voice:
            print(f"Бот: [голосовое {result.voice}]")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(run_batch() if "--batch" in sys.argv else run_interactive())
