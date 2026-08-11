from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    telegram_bot_token: SecretStr
    openai_api_key: SecretStr
    openai_model: str = "gpt-5.6-luna"
    transcription_model: str = "gpt-transcribe"
    # Текст (черновик/ревью/роутер шаблонов) уходит в DeepSeek — дешевле и не
    # содержит PII (см. privacy.py); фото чеков и голосовые остаются на
    # OpenAI выше, так как DeepSeek не умеет vision и транскрибацию аудио.
    # Пустой ключ = DeepSeek выключен, текст идёт на openai_model как раньше.
    deepseek_api_key: SecretStr = SecretStr("")
    deepseek_model: str = "deepseek-v4-flash"
    deepseek_base_url: str = "https://api.deepseek.com"
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
    port: int = 8080
    # Доп. модели через запятую, которые можно выбрать в настройках бота, помимо
    # текущей openai_model. По умолчанию пусто: единственная разрешённая модель —
    # та, что уже сконфигурирована в openai_model.
    bot_allowed_models: str = ""
    # Telegram-группа доставщиков, куда бот шлёт оплаченный заказ с чеком.
    # 0 = группа регистрируется администратором командой /delivery_group.
    delivery_group_chat_id: int = 0

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
