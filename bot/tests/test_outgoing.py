from smilekit_bot.outgoing import MESSAGE_SEPARATOR, split_outgoing_messages


def test_internal_separator_creates_separate_messages():
    result = split_outgoing_messages(
        f"Одна упаковка — 1490 сом.{MESSAGE_SEPARATOR}Из какого вы города?"
    )
    assert result == [
        "Одна упаковка — 1490 сом.",
        "Из какого вы города?",
    ]


def test_separator_is_never_visible_and_empty_parts_are_removed():
    result = split_outgoing_messages(
        f"Первое{MESSAGE_SEPARATOR}{MESSAGE_SEPARATOR}Второе"
    )
    assert result == ["Первое", "Второе"]
    assert all(MESSAGE_SEPARATOR not in part for part in result)


def test_output_is_limited_to_three_messages():
    text = MESSAGE_SEPARATOR.join(["1", "2", "3", "4"])
    assert split_outgoing_messages(text) == ["1", "2", "3\n\n4"]


def test_regular_answer_stays_one_message():
    assert split_outgoing_messages("Короткий ответ") == ["Короткий ответ"]


def test_short_paragraphs_are_also_sent_separately():
    assert split_outgoing_messages("Цена — 1490 сом.\n\nИз какого вы города?") == [
        "Цена — 1490 сом.",
        "Из какого вы города?",
    ]


def test_long_answer_and_explanation_are_split_after_direct_answer():
    text = (
        "Синее или фиолетовое на фото — это цвет геля на полосках, а не оттенок зубов. "
        "После 30 минут полоски снимают, гель удаляют салфеткой и смывают водой. "
        "Сами полоски осветляют натуральную эмаль, поэтому зубы выглядят светлее."
    )
    assert split_outgoing_messages(text) == [
        "Синее или фиолетовое на фото — это цвет геля на полосках, а не оттенок зубов.",
        (
            "После 30 минут полоски снимают, гель удаляют салфеткой и смывают водой. "
            "Сами полоски осветляют натуральную эмаль, поэтому зубы выглядят светлее."
        ),
    ]


def test_long_safety_advice_stays_together():
    text = (
        "При сильной боли прекратите применение. Это может требовать осмотра. "
        "Лучше обратиться к стоматологу, чтобы безопасно оценить состояние зубов."
        " Дополнительные рекомендации зависят от результатов осмотра специалиста."
    )
    assert split_outgoing_messages(text) == [text]
