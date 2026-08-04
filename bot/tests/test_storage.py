from smilekit_bot.storage import BotStorage


def test_duplicate_claim_survives_restart(tmp_path):
    path = tmp_path / "bot.db"
    first = BotStorage(str(path))
    assert first.claim_message(10, 20)
    assert not first.claim_message(10, 20)
    first.close()

    reopened = BotStorage(str(path))
    assert not reopened.claim_message(10, 20)
    assert reopened.claim_message(10, 21)
    reopened.close()


def test_history_and_memory_are_persistent(tmp_path):
    path = tmp_path / "bot.db"
    storage = BotStorage(str(path))
    storage.add_exchange(1, 2, "Цена?", "1490 сом", "text")
    storage.update_memory(1, "Клиент находится в Бишкеке", "1 упаковка")
    storage.close()

    reopened = BotStorage(str(path))
    assert [item.content for item in reopened.history(1)] == [
        "Цена?",
        "1490 сом",
    ]
    assert reopened.memory(1) == "Клиент находится в Бишкеке"
    order = reopened._db.execute(
        "SELECT summary FROM orders WHERE chat_id = 1"
    ).fetchone()
    assert order["summary"] == "1 упаковка"
    reopened.close()


def test_rate_limit_blocks_extra_messages(tmp_path):
    storage = BotStorage(str(tmp_path / "bot.db"))
    assert storage.allow_request(1, limit=2)
    assert storage.allow_request(1, limit=2)
    assert not storage.allow_request(1, limit=2)
    assert storage.allow_request(2, limit=2)
    storage.close()


def test_persistent_event_is_claimed_once(tmp_path):
    path = tmp_path / "bot.db"
    storage = BotStorage(str(path))
    assert storage.claim_event(1, "results_after_city")
    assert not storage.claim_event(1, "results_after_city")
    storage.close()

    reopened = BotStorage(str(path))
    assert not reopened.claim_event(1, "results_after_city")
    reopened.clear_chat(1)
    assert reopened.claim_event(1, "results_after_city")
    reopened.close()


def test_new_message_cancels_pending_follow_up(tmp_path):
    storage = BotStorage(str(tmp_path / "bot.db"))
    assert storage.claim_message(1, 100)
    assert not storage.has_message_after(1, 100)
    assert storage.claim_message(1, 101)
    assert storage.has_message_after(1, 100)
    storage.close()


def test_clear_chat_keeps_duplicate_guard(tmp_path):
    storage = BotStorage(str(tmp_path / "bot.db"))
    assert storage.claim_message(1, 99)
    storage.add_exchange(1, 99, "Привет", "Здравствуйте", "text")
    storage.update_memory(1, "Факт", "Заказ")
    storage.clear_chat(1)
    assert storage.history(1) == []
    assert storage.memory(1) == ""
    assert not storage.claim_message(1, 99)
    storage.close()
