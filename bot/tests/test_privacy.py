from smilekit_bot.privacy import apply_mapping, build_mapping, restore_mapping


def test_build_mapping_detects_phone_and_assigns_placeholder():
    mapping = build_mapping("Мой номер 0700922622, жду звонка")
    assert mapping == {"0700922622": "{{PHONE_1}}"}


def test_build_mapping_detects_international_format_phone():
    mapping = build_mapping("+996 700 92 26 22 — звоните")
    assert list(mapping.values()) == ["{{PHONE_1}}"]


def test_build_mapping_detects_address_and_name():
    mapping = build_mapping("меня зовут Халикхан, живу на ул. Токтогула 5")
    assert mapping.get("Халикхан") == "{{NAME_1}}"
    assert any(v == "{{ADDRESS_1}}" for k, v in mapping.items() if "Токтогула" in k)


def test_build_mapping_reuses_placeholder_for_repeated_value():
    mapping = build_mapping("Номер 0700922622", "Ещё раз: 0700922622")
    assert mapping["0700922622"] == "{{PHONE_1}}"
    assert len(mapping) == 1


def test_apply_and_restore_roundtrip():
    text = "Позвоните мне на 0700922622, пожалуйста"
    mapping = build_mapping(text)
    redacted = apply_mapping(text, mapping)
    assert "0700922622" not in redacted
    assert "{{PHONE_1}}" in redacted
    restored = restore_mapping(redacted, mapping)
    assert restored == text


def test_no_pii_leaves_text_unchanged():
    mapping = build_mapping("Здравствуйте, сколько стоит набор?")
    assert mapping == {}
    assert apply_mapping("Здравствуйте, сколько стоит набор?", mapping) == "Здравствуйте, сколько стоит набор?"


def test_restore_handles_missing_placeholder_gracefully():
    assert restore_mapping(None, {"x": "{{PHONE_1}}"}) is None
    assert restore_mapping("текст без плейсхолдеров", {}) == "текст без плейсхолдеров"
