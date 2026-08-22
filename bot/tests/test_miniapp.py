import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest

from smilekit_bot import miniapp
from smilekit_bot.miniapp_store import (
    COURSE_DAYS,
    PROCEDURE_DAYS,
    REWARD_PROCEDURES_REQUIRED,
    MiniAppStore,
    bishkek_day_start,
)

BOT_TOKEN = "123456:TEST-TOKEN"


def sign(payload: dict, token: str = BOT_TOKEN) -> str:
    data_check_string = "\n".join(f"{key}={payload[key]}" for key in sorted(payload))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    signature = hmac.new(secret, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**payload, "hash": signature})


def init_data(user_id: int = 42, auth_date: float | None = None) -> str:
    return sign({
        "auth_date": str(int(auth_date or time.time())),
        "query_id": "AAA",
        "user": json.dumps({"id": user_id, "first_name": "Алина"}, separators=(",", ":")),
    })


@pytest.fixture
def store(tmp_path) -> MiniAppStore:
    instance = MiniAppStore(str(tmp_path / "app.db"))
    yield instance
    instance.close()


# ---------------------------------------------------------------- initData


def test_valid_init_data_is_accepted():
    parsed = miniapp.validate_init_data(init_data(), BOT_TOKEN)
    assert parsed is not None
    assert parsed["user"]["id"] == 42


def test_tampered_init_data_is_rejected():
    raw = init_data()
    tampered = raw.replace("Алина", "Мария") if "Алина" in raw else raw + "&x=1"
    assert miniapp.validate_init_data(tampered, BOT_TOKEN) is None


def test_init_data_signed_by_another_token_is_rejected():
    raw = sign(
        {
            "auth_date": str(int(time.time())),
            "user": json.dumps({"id": 42, "first_name": "Алина"}, separators=(",", ":")),
        },
        token="999:OTHER",
    )
    assert miniapp.validate_init_data(raw, BOT_TOKEN) is None


def test_stale_init_data_is_rejected():
    stale = init_data(auth_date=time.time() - miniapp.INIT_DATA_MAX_AGE_SECONDS - 60)
    assert miniapp.validate_init_data(stale, BOT_TOKEN) is None


def test_empty_init_data_is_rejected():
    assert miniapp.validate_init_data("", BOT_TOKEN) is None


def test_signature_field_is_included_in_check():
    """Telegram добавляет отдельное поле signature (Ed25519, для сторонней
    валидации без токена бота) — но в HMAC-проверке по токену бота исключается
    только hash. Подтверждено по офиц. доке (core.telegram.org/bots/webapps:
    Ed25519-метод отдельно исключает "hash и signature" — то есть базовый
    HMAC-метод исключает только hash) и референсной реализацией aiogram
    (aiogram.utils.web_app.check_webapp_signature). Раньше тест ожидал
    обратное и был не в ладах с кодом и реальными живыми запусками."""
    payload = {
        "auth_date": str(int(time.time())),
        "user": json.dumps({"id": 42, "first_name": "Алина"}, separators=(",", ":")),
        "signature": "abcdef",
    }
    raw = sign(payload)  # signature подписан вместе с остальными полями
    assert miniapp.validate_init_data(raw, BOT_TOKEN) is not None
    # Если signature добавили ПОСЛЕ подписи (не входил в hash) — не совпадёт.
    tampered = sign({k: v for k, v in payload.items() if k != "signature"}) + "&signature=abcdef"
    assert miniapp.validate_init_data(tampered, BOT_TOKEN) is None


# -------------------------------------------------------------------- курс


def test_current_day_counts_from_course_start():
    now = time.time()
    user = {"course_started_at": bishkek_day_start(now) - 3 * 86400}
    assert miniapp.current_day(user, now) == 4


def test_current_day_is_clamped_to_course_length():
    now = time.time()
    user = {"course_started_at": bishkek_day_start(now) - 40 * 86400}
    assert miniapp.current_day(user, now) == COURSE_DAYS


def test_current_day_is_zero_without_onboarding():
    assert miniapp.current_day({}, time.time()) == 0


def test_procedure_days_get_a_procedure_task():
    tasks = miniapp.day_plan(1, {"drink": "coffee", "sensitivity": "no"})
    assert [task["id"] for task in tasks][:2] == ["procedure", "no_staining"]
    assert "фото" in tasks[2]["text"].lower()  # день 1 — фото «до»


def test_care_days_have_no_procedure():
    tasks = miniapp.day_plan(2, {"drink": "none", "sensitivity": "no"})
    assert "procedure" not in {task["id"] for task in tasks}


def test_sensitivity_adds_a_check_step_only_on_procedure_days():
    procedure = miniapp.day_plan(3, {"drink": "none", "sensitivity": "yes"})
    care = miniapp.day_plan(4, {"drink": "none", "sensitivity": "yes"})
    assert "sensitivity" in {task["id"] for task in procedure}
    assert "sensitivity" not in {task["id"] for task in care}


def test_day_plan_outside_course_is_empty():
    assert miniapp.day_plan(0, {}) == []
    assert miniapp.day_plan(COURSE_DAYS + 1, {}) == []


def test_streak_counts_backwards_from_today():
    check_ins = {3: {}, 4: {}, 5: {}, 7: {}}
    assert miniapp.streak(check_ins, 5) == 3
    assert miniapp.streak(check_ins, 6) == 0


# ------------------------------------------------------------ Smile Score


def test_score_is_zero_for_a_fresh_client():
    assert miniapp.smile_score({}, [], [], {"best": 0, "total": 0}) == 0


def test_score_is_full_for_a_completed_course():
    check_ins = {day: {} for day in range(1, COURSE_DAYS + 1)}
    photos = [{"day": 1}, {"day": 7}, {"day": 14}]
    reads = [f"a{i}" for i in range(miniapp.ACADEMY_SCORE_TARGET)]
    score = miniapp.smile_score(check_ins, photos, reads, {"best": 5, "total": 5})
    assert score == 100


def test_score_ignores_quiz_that_was_never_played():
    check_ins = {day: {} for day in PROCEDURE_DAYS}
    score = miniapp.smile_score(check_ins, [], [], {"best": 0, "total": 0})
    assert score == 50


# --------------------------------------------------------------- хранилище


def test_check_in_is_idempotent(store):
    store.ensure_user(1, "Алина", None)
    store.check_in(1, 3, ["procedure"])
    store.check_in(1, 3, ["procedure", "no_staining"])
    assert store.check_ins(1) == {3: {"tasks": ["procedure", "no_staining"], "done_at": pytest.approx(time.time(), abs=5)}}


def test_photo_replaces_the_previous_one_for_the_same_day(store):
    store.ensure_user(1, "Алина", None)
    first = store.add_photo(1, 1, b"first")
    second = store.add_photo(1, 1, b"second")
    photos = store.photos(1)
    assert len(photos) == 1
    assert store.photo_path(first["id"], first["token"]) is None
    assert store.photo_path(second["id"], second["token"]).read_bytes() == b"second"


def test_photo_path_requires_the_matching_token(store):
    store.ensure_user(1, "Алина", None)
    photo = store.add_photo(1, 1, b"data")
    assert store.photo_path(photo["id"], "wrong-token") is None


def test_reward_code_is_issued_once(store):
    store.ensure_user(1, "Алина", None)
    first = store.issue_reward(1)
    second = store.issue_reward(1)
    assert first["code"] == second["code"]


def test_reward_unlocks_only_after_enough_procedures(store):
    store.ensure_user(1, "Алина", None)
    for day in PROCEDURE_DAYS[:REWARD_PROCEDURES_REQUIRED - 1]:
        store.check_in(1, day, [])
    assert miniapp.reward_state(store, 1, store.check_ins(1))["unlocked"] is False
    store.check_in(1, PROCEDURE_DAYS[REWARD_PROCEDURES_REQUIRED - 1], [])
    assert miniapp.reward_state(store, 1, store.check_ins(1))["unlocked"] is True


def test_care_days_do_not_unlock_the_reward(store):
    store.ensure_user(1, "Алина", None)
    for day in (2, 4, 6, 8, 10, 12, 14):
        store.check_in(1, day, [])
    assert miniapp.reward_state(store, 1, store.check_ins(1))["unlocked"] is False


def test_restart_clears_progress_but_keeps_the_profile(store):
    store.ensure_user(1, "Алина", None)
    store.save_onboarding(1, goal="max", drink="coffee", sensitivity="no")
    store.check_in(1, 1, ["procedure"])
    store.add_photo(1, 1, b"data")
    store.mark_read(1, "how-to-use-step-by-step")
    store.restart_course(1)
    assert store.check_ins(1) == {}
    assert store.photos(1) == []
    assert store.reads(1) == ["how-to-use-step-by-step"]
    assert store.get_user(1)["goal"] == "max"


def test_quiz_keeps_the_best_score(store):
    store.ensure_user(1, "Алина", None)
    store.save_quiz(1, 4, 5)
    store.save_quiz(1, 2, 5)
    result = store.quiz(1)
    assert result["best"] == 4
    assert result["plays"] == 2


# -------------------------------------------------------------- напоминания


def test_reminder_is_scheduled_for_today_when_the_slot_is_ahead():
    now = bishkek_day_start(time.time()) + 15 * 3600  # 15:00 по Бишкеку
    user = {"course_started_at": bishkek_day_start(now), "reminder_hour": 20, "reminders_on": 1}
    due = miniapp.next_reminder_due_at(user, {}, now)
    assert due == bishkek_day_start(now) + 20 * 3600


def test_reminder_moves_to_tomorrow_once_the_day_is_marked():
    now = bishkek_day_start(time.time()) + 15 * 3600
    user = {"course_started_at": bishkek_day_start(now), "reminder_hour": 20, "reminders_on": 1}
    due = miniapp.next_reminder_due_at(user, {1: {}}, now)
    assert due == bishkek_day_start(now) + 20 * 3600 + 86400


def test_reminder_moves_to_tomorrow_when_the_slot_has_passed():
    now = bishkek_day_start(time.time()) + 22 * 3600
    user = {"course_started_at": bishkek_day_start(now), "reminder_hour": 20, "reminders_on": 1}
    due = miniapp.next_reminder_due_at(user, {}, now)
    assert due == bishkek_day_start(now) + 20 * 3600 + 86400


def test_no_reminder_after_the_course_is_finished():
    now = time.time()
    user = {
        "course_started_at": bishkek_day_start(now) - (COURSE_DAYS - 1) * 86400,
        "reminder_hour": 20,
        "reminders_on": 1,
    }
    assert miniapp.next_reminder_due_at(user, {COURSE_DAYS: {}}, now) is None


def test_no_reminder_when_switched_off():
    now = time.time()
    user = {"course_started_at": bishkek_day_start(now), "reminder_hour": 20, "reminders_on": 0}
    assert miniapp.next_reminder_due_at(user, {}, now) is None


def test_no_reminder_before_onboarding():
    assert miniapp.next_reminder_due_at({"reminders_on": 1}, {}, time.time()) is None


def test_due_reminders_skips_clients_who_switched_them_off(store):
    now = time.time()
    store.ensure_user(1, "Алина", None)
    store.save_onboarding(1, goal="max", drink="coffee", sensitivity="no")
    store.schedule_reminder(1, now - 60, 1)
    assert [row["chat_id"] for row in store.due_reminders(now)] == [1]
    store.update_reminders(1, enabled=False)
    assert store.due_reminders(now) == []
