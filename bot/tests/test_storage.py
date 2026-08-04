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


def test_daily_nudge_schedule_is_not_reset_by_second_call(tmp_path):
    storage = BotStorage(str(tmp_path / "bot.db"))
    storage.schedule_daily_nudge(1, 100.0)
    storage.schedule_daily_nudge(1, 999.0)  # не должно сдвинуть уже заведённое
    assert storage.due_daily_nudges(now=100.0, max_sends=7) == [1]
    assert storage.due_daily_nudges(now=50.0, max_sends=7) == []
    storage.close()


def test_daily_nudge_advance_moves_next_due_and_counts_sends(tmp_path):
    storage = BotStorage(str(tmp_path / "bot.db"))
    storage.schedule_daily_nudge(1, 100.0)
    storage.advance_daily_nudge(1, 200.0)
    assert storage.due_daily_nudges(now=150.0, max_sends=7) == []
    assert storage.due_daily_nudges(now=200.0, max_sends=7) == [1]
    row = storage._db.execute("SELECT sent_count FROM daily_nudges WHERE chat_id = 1").fetchone()
    assert row["sent_count"] == 1
    storage.close()


def test_daily_nudge_cap_excludes_chat_after_enough_sends(tmp_path):
    storage = BotStorage(str(tmp_path / "bot.db"))
    storage.schedule_daily_nudge(1, 100.0)
    for i in range(3):
        storage.advance_daily_nudge(1, 100.0 + i)
    assert storage.due_daily_nudges(now=1000.0, max_sends=3) == []
    assert storage.due_daily_nudges(now=1000.0, max_sends=4) == [1]
    storage.close()


def test_cancel_daily_nudge_removes_schedule(tmp_path):
    storage = BotStorage(str(tmp_path / "bot.db"))
    storage.schedule_daily_nudge(1, 100.0)
    storage.cancel_daily_nudge(1)
    assert storage.due_daily_nudges(now=100.0, max_sends=7) == []
    storage.close()


def test_clear_chat_removes_daily_nudge_schedule(tmp_path):
    storage = BotStorage(str(tmp_path / "bot.db"))
    storage.schedule_daily_nudge(1, 100.0)
    storage.clear_chat(1)
    assert storage.due_daily_nudges(now=100.0, max_sends=7) == []
    storage.close()
