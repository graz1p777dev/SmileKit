from smilekit_bot.__main__ import next_allowed_send_time


def test_quiet_hours_blocks_late_night_and_early_morning():
    # 23:00 Бишкек = 17:00 UTC в тот же день (эпоха 0 = 1970-01-01 00:00 UTC).
    late_night = 17 * 3600
    assert next_allowed_send_time(late_night) is not None

    # 04:00 Бишкек = 22:00 UTC предыдущего дня.
    early_morning = 22 * 3600 - 24 * 3600
    assert next_allowed_send_time(early_morning) is not None


def test_daytime_is_allowed():
    # 12:00 Бишкек = 06:00 UTC.
    midday = 6 * 3600
    assert next_allowed_send_time(midday) is None


def test_resume_time_is_nine_am_bishkek():
    # 23:00 Бишкек — до утра 9 часов.
    late_night = 17 * 3600
    resume = next_allowed_send_time(late_night)
    assert resume is not None
    assert next_allowed_send_time(resume) is None
    # Между текущим моментом и разрешённым временем должно быть не больше суток.
    assert 0 < resume - late_night <= 24 * 3600
