from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    telegram_bot_token: SecretStr
    openai_api_key: SecretStr
    openai_model: str = "gpt-5.6-luna"
    # whisper-1 — модель транскрибации OpenAI, доступная на всех аккаунтах.
    # Прежним значением стояло "gpt-transcribe" — такой модели у OpenAI нет,
    # и запасной путь транскрибации всё равно упал бы с 404.
    transcription_model: str = "whisper-1"
    # Текст (черновик/ревью/роутер шаблонов) уходит в DeepSeek — дешевле и не
    # содержит PII (см. privacy.py); DeepSeek не умеет vision и транскрибацию
    # аудио, поэтому фото чеков и голосовые — на Gemini (умеет оба, дешевле
    # OpenAI). Пустой ключ = DeepSeek выключен, текст идёт на openai_model.
    deepseek_api_key: SecretStr = SecretStr("")
    deepseek_model: str = "deepseek-v4-flash"
    deepseek_base_url: str = "https://api.deepseek.com"
    # Проверка чеков (vision). Для транскрибации голосовых Gemini теперь
    # ЗАПАСНОЙ вариант: сначала пробуется OpenAI (см. transcribe в service.py),
    # потому что бесплатная квота Gemini регулярно упирается в 429.
    gemini_api_key: SecretStr = SecretStr("")
    # "latest"-алиас, а не конкретная версия: pinned gemini-2.5-flash отдавал
    # 404 "no longer available to new users" на этом ключе (несмотря на то,
    # что models.list его перечисляет) — алиас сам указывает на актуальную
    # доступную версию (сейчас резолвится в gemini-3.7-flash).
    gemini_model: str = "gemini-flash-latest"
    database_path: str = "data/smilekit.db"
    rate_limit_per_minute: int = 10
    max_voice_seconds: int = 180
    # Отправка заказов в CRM. Пустые значения = функция выключена,
    # бот работает как раньше и ничего никуда не шлёт.
    crm_base_url: str = ""
    crm_internal_token: SecretStr = SecretStr("")
    # HTTP-слой /internal/* для CRM (настройки бота + лаборатория). Пустой токен
    # выключает вход в /internal/settings и /internal/lab, кроме /internal/health.
    bot_internal_token: SecretStr = SecretStr("")
    # Второй принимаемый токен на время ротации BOT_INTERNAL_TOKEN без
    # простоя (см. docs/ROTATION.md). Пусто = принимается только основной.
    bot_internal_token_next: SecretStr = SecretStr("")
    port: int = 8080
    # Доп. модели через запятую, которые можно выбрать в настройках бота, помимо
    # текущей openai_model. По умолчанию пусто: единственная разрешённая модель —
    # та, что уже сконфигурирована в openai_model.
    bot_allowed_models: str = ""
    # Telegram-группа доставщиков, куда бот шлёт оплаченный заказ с чеком.
    # 0 = группа регистрируется администратором командой /delivery_group.
    delivery_group_chat_id: int = 0
    # Публичный HTTPS-адрес Mini App «Моя программа» (страница /app этого же
    # сервиса, например https://smilekit-bot.up.railway.app/app). Telegram
    # открывает Web App только по HTTPS, поэтому localhost сюда не годится.
    # Пусто = кнопки приложения и напоминания по курсу выключены, бот работает
    # ровно как раньше.
    miniapp_url: str = ""
    # Телеметрия Control Center («коннектор CRM»): heartbeat, расход LLM,
    # бизнес-события (сделка/заказ/ошибка). Пустые значения = коннектор
    # выключен, бот работает ровно как раньше и никуда не шлёт.
    control_center_url: str = ""
    control_center_token: SecretStr = SecretStr("")
    # Резервные копии SQLite в S3-совместимое хранилище (Cloudflare R2 /
    # Railway bucket), см. backup.py. Все пустые = загрузка выключена,
    # копии остаются только локально в <dirname(database_path)>/backups/.
    backup_s3_endpoint: str = ""
    backup_s3_bucket: str = ""
    backup_s3_access_key_id: str = ""
    backup_s3_secret_access_key: SecretStr = SecretStr("")
    backup_s3_region: str = "auto"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
