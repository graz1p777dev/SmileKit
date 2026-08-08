from smilekit_bot import templates


def test_bishkek_and_tokmok_use_yandex_delivery():
    assert templates.delivery_template_for_city("Я из Бишкека") == templates.DELIVERY_BISHKEK
    assert templates.delivery_template_for_city("живу в токмоке") == templates.DELIVERY_BISHKEK


def test_other_kg_cities_use_yldam_express():
    assert templates.delivery_template_for_city("Ош") == templates.DELIVERY_OTHER_KG
    assert templates.delivery_template_for_city("я из Каракола") == templates.DELIVERY_OTHER_KG


def test_unrecognized_city_returns_none():
    assert templates.delivery_template_for_city("Париж") is None
    assert templates.delivery_template_for_city("не знаю") is None


def test_kz_uz_ru_cities_use_matching_delivery():
    assert templates.delivery_template_for_city("я из Алматы") == templates.DELIVERY_KZ_ALMATY
    assert templates.delivery_template_for_city("живу в Астане") == templates.DELIVERY_KZ_OTHER
    assert templates.delivery_template_for_city("я из ташкента") == templates.DELIVERY_UZ_TASHKENT
    assert templates.delivery_template_for_city("живу в Москве") == templates.DELIVERY_RU
    assert templates.delivery_template_for_city("я из России") == templates.DELIVERY_RU


def test_currency_for_history_detects_country_by_city():
    assert templates.currency_for_history(["Я из Бишкека"]) == "KGS"
    assert templates.currency_for_history(["Пишу из Алматы"]) == "KZT"
    assert templates.currency_for_history(["Живу в Ташкенте"]) == "UZS"
    assert templates.currency_for_history([]) == "KGS"


def test_daily_discount_offer_uses_matching_price_line():
    assert "1290 сом" in templates.daily_discount_offer("KGS")
    assert "155000 сум" in templates.daily_discount_offer("UZS")
    assert "7000 тенге" in templates.daily_discount_offer("KZT")
    assert "скидку 13%" in templates.daily_discount_offer()
