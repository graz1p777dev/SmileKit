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


def test_pending_payment_collects_delivery_details_after_qr(tmp_path):
    storage = BotStorage(str(tmp_path / "bot.db"))
    storage.save_pending_payment(
        42,
        order_summary="1 коробка",
        amount=1490,
        currency="KGS",
        customer_name=None,
        customer_phone=None,
        city="Бишкек",
        address=None,
        customer_username="client",
        source="Telegram",
    )

    storage.update_pending_payment_details(
        42,
        order_summary=None,
        customer_name="Алихан",
        customer_phone="+996 500 000 000",
        city=None,
        address="ул. Манаса, 10",
    )

    payment = storage.get_pending_payment(42)
    assert payment is not None
    assert payment["customer_name"] == "Алихан"
    assert payment["customer_phone"] == "+996 500 000 000"
    assert payment["city"] == "Бишкек"
    assert payment["address"] == "ул. Манаса, 10"
    storage.close()


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


def test_has_event_does_not_consume_it(tmp_path):
    storage = BotStorage(str(tmp_path / "bot.db"))
    assert not storage.has_event(1, "discount_offered")
    assert storage.claim_event(1, "discount_offered")
    assert storage.has_event(1, "discount_offered")
    assert storage.has_event(1, "discount_offered")  # повторная проверка не потребляет событие
    storage.close()


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


def test_nudge_chain_schedules_three_stages(tmp_path):
    storage = BotStorage(str(tmp_path / "bot.db"))
    storage.schedule_nudge_chain(1, now=1000.0)
    assert storage.due_nudge_follow_ups(now=1000.0) == []
    kinds = sorted(kind for _id, _chat, kind in storage.due_nudge_follow_ups(now=1000.0 + 100 * 3600))
    assert kinds == ["day", "hours", "last"]
    storage.close()


def test_nudge_chain_is_replaced_not_duplicated_by_second_call(tmp_path):
    storage = BotStorage(str(tmp_path / "bot.db"))
    storage.schedule_nudge_chain(1, now=1000.0)
    storage.schedule_nudge_chain(1, now=2000.0)
    due = storage.due_nudge_follow_ups(now=2000.0 + 100 * 3600)
    assert len(due) == 3
    storage.close()


def test_order_incomplete_nudge_replaces_chain(tmp_path):
    storage = BotStorage(str(tmp_path / "bot.db"))
    storage.schedule_nudge_chain(1, now=1000.0)
    storage.schedule_order_incomplete_nudge(1, now=1000.0)
    due = storage.due_nudge_follow_ups(now=1000.0 + 100 * 3600)
    assert [kind for _id, _chat, kind in due] == ["order_incomplete"]
    storage.close()


def test_cancel_nudge_follow_ups_removes_pending(tmp_path):
    storage = BotStorage(str(tmp_path / "bot.db"))
    storage.schedule_nudge_chain(1, now=1000.0)
    storage.cancel_nudge_follow_ups(1)
    assert storage.due_nudge_follow_ups(now=1000.0 + 100 * 3600) == []
    storage.close()


def test_clear_chat_removes_nudge_follow_ups(tmp_path):
    storage = BotStorage(str(tmp_path / "bot.db"))
    storage.schedule_nudge_chain(1, now=1000.0)
    storage.clear_chat(1)
    assert storage.due_nudge_follow_ups(now=1000.0 + 100 * 3600) == []
    storage.close()


def test_promised_follow_up_replaces_chain_and_fires_at_exact_time(tmp_path):
    storage = BotStorage(str(tmp_path / "bot.db"))
    storage.schedule_nudge_chain(1, now=1000.0)
    storage.schedule_promised_follow_up(1, due_at=1000.0 + 24 * 3600)
    assert storage.due_nudge_follow_ups(now=1000.0 + 23 * 3600) == []
    due = storage.due_nudge_follow_ups(now=1000.0 + 24 * 3600)
    assert [kind for _id, _chat, kind in due] == ["promised"]
    storage.close()


def test_nudge_follow_ups_table_migrates_old_check_constraint(tmp_path):
    path = tmp_path / "bot.db"
    storage = BotStorage(str(path))
    storage.close()

    import sqlite3
    raw = sqlite3.connect(path)
    raw.execute("DROP TABLE nudge_follow_ups")
    raw.execute(
        """
        CREATE TABLE nudge_follow_ups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('hours', 'day', 'last', 'order_incomplete')),
            due_at REAL NOT NULL,
            sent_at REAL
        )
        """
    )
    raw.execute("INSERT INTO nudge_follow_ups (chat_id, kind, due_at) VALUES (1, 'hours', 500.0)")
    raw.commit()
    raw.close()

    reopened = BotStorage(str(path))
    due = reopened.due_nudge_follow_ups(now=500.0)
    assert [kind for _id, _chat, kind in due] == ["hours"]
    reopened.schedule_promised_follow_up(2, due_at=600.0)
    assert [kind for _id, _chat, kind in reopened.due_nudge_follow_ups(now=600.0) if _chat == 2] == ["promised"]
    reopened.close()


def test_incoming_message_count(tmp_path):
    storage = BotStorage(str(tmp_path / "bot.db"))
    assert storage.incoming_message_count(1) == 0
    storage.add_exchange(1, 1, "Привет", "Здравствуйте", "text")
    storage.add_exchange(1, 2, "Цена?", "1490 сом", "text")
    assert storage.incoming_message_count(1) == 2
    storage.close()


def test_log_event_and_list_events(tmp_path):
    storage = BotStorage(str(tmp_path / "bot.db"))
    storage.log_event(1, "info", "reply", "reply.ai_generated", "Ответ отправлен", {"parts": 2})
    storage.log_event(2, "error", "order", "order.receipt_mismatch", "Чек не прошёл проверку")

    events = storage.list_events()
    assert len(events) == 2
    # Новые сверху.
    assert events[0]["event"] == "order.receipt_mismatch"
    assert events[0]["level"] == "error"
    assert events[1]["event"] == "reply.ai_generated"
    assert events[1]["details"] == {"parts": 2}

    only_errors = storage.list_events(level="error")
    assert [e["event"] for e in only_errors] == ["order.receipt_mismatch"]

    newest_id = events[0]["id"]
    storage.log_event(1, "info", "nudge", "nudge.idle", "Напоминание отправлено")
    fresh = storage.list_events(since_id=newest_id)
    assert [e["event"] for e in fresh] == ["nudge.idle"]
    storage.close()


def test_log_event_failure_does_not_raise(tmp_path):
    storage = BotStorage(str(tmp_path / "bot.db"))
    storage.close()
    # После close() запись должна тихо проглотить ошибку, а не уронить бота.
    storage.log_event(1, "info", "reply", "reply.ai_generated", "Ответ отправлен")


def test_sender_distinguishes_bot_and_operator(tmp_path):
    storage = BotStorage(str(tmp_path / "bot.db"))
    storage.add_exchange(1, 10, "Сколько стоит?", "1490 сом", "text")
    storage.add_operator_message(1, 11, "Напоминание бота")  # sender по умолчанию bot
    storage.add_operator_message(1, 12, "Здравствуйте, это менеджер", sender="operator")

    messages = storage.chat_messages(1)
    assert [m["sender"] for m in messages] == ["client", "bot", "bot", "operator"]
    assert messages[-1]["content"] == "Здравствуйте, это менеджер"

    chats = storage.list_chats()
    assert len(chats) == 1
    assert chats[0]["chatId"] == 1
    assert chats[0]["lastSender"] == "operator"
    assert chats[0]["paused"] is False

    storage.pause_chat(1, 1000.0, paused_by="operator")
    assert storage.list_chats()[0]["paused"] is True
    storage.close()


def test_sender_migration_backfills_existing_rows(tmp_path):
    import sqlite3

    path = str(tmp_path / "bot.db")
    # База старого формата - без колонки sender.
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            telegram_message_id INTEGER NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
            content TEXT NOT NULL,
            kind TEXT NOT NULL,
            created_at REAL NOT NULL,
            UNIQUE (chat_id, telegram_message_id, role)
        );
        INSERT INTO messages (chat_id, telegram_message_id, role, content, kind, created_at)
        VALUES (5, 1, 'user', 'привет', 'text', 1.0),
               (5, 1, 'assistant', 'здравствуйте', 'text', 1.0);
        """
    )
    conn.commit()
    conn.close()

    storage = BotStorage(path)
    messages = storage.chat_messages(5)
    assert [m["sender"] for m in messages] == ["client", "bot"]
    storage.close()
