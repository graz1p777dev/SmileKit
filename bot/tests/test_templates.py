from smilekit_bot import templates


def test_bishkek_and_tokmok_use_yandex_delivery():
    assert templates.delivery_template_for_city("Я из Бишкека") == templates.DELIVERY_BISHKEK
    assert templates.delivery_template_for_city("живу в токмоке") == templates.DELIVERY_BISHKEK


def test_other_kg_cities_use_yldam_express():
    assert templates.delivery_template_for_city("Ош") == templates.DELIVERY_OTHER_KG
    assert templates.delivery_template_for_city("я из Каракола") == templates.DELIVERY_OTHER_KG


def test_unrecognized_city_returns_none():
    assert templates.delivery_template_for_city("Алматы") is None
    assert templates.delivery_template_for_city("не знаю") is None
