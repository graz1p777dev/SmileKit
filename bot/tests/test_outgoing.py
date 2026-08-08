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


def test_hallucinated_tags_are_stripped_entirely():
    """Модель иногда сама выдумывает теги вроде <НЕ_ВИДНО_КЛИЕНТУ>...</...>,
    пытаясь пометить кусок текста как внутренний — таких тегов система не
    поддерживает, и без защиты они уходят клиенту буквально (баг на проде)."""
    text = (
        "Хорошо, поняла вас."
        "<НЕ_ВИДНО_КЛИЕНТУ>Если решите попробовать — мы рядом, поможем "
        "с заказом и подскажем по применению.</НЕ_ВИДНО_КЛИЕНТУ>"
    )
    result = split_outgoing_messages(text)
    assert result == ["Хорошо, поняла вас."]
    assert "НЕ_ВИДНО_КЛИЕНТУ" not in result[0]


def test_stray_unmatched_tag_is_stripped():
    result = split_outgoing_messages("Цена 1490 сом.</ВНУТРЕННЯЯ_ЗАМЕТКА>")
    assert result == ["Цена 1490 сом."]


def test_next_message_separator_survives_tag_stripping():
    result = split_outgoing_messages(
        f"Цена 1490 сом.{MESSAGE_SEPARATOR}Из какого вы города?"
    )
    assert result == ["Цена 1490 сом.", "Из какого вы города?"]


def test_long_safety_advice_stays_together():
    text = (
        "При сильной боли прекратите применение. Это может требовать осмотра. "
        "Лучше обратиться к стоматологу, чтобы безопасно оценить состояние зубов."
        " Дополнительные рекомендации зависят от результатов осмотра специалиста."
    )
    assert split_outgoing_messages(text) == [text]
