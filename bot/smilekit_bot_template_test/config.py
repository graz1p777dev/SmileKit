"""Своя, минимальная конфигурация — сознательно НЕ переиспользует
smilekit_bot.config.Settings (тот требует openai_api_key, которого этому
боту вообще не нужно — здесь ни одного вызова ИИ)."""
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class TemplateTestSettings(BaseSettings):
    telegram_bot_token: SecretStr

    model_config = SettingsConfigDict(env_file=".env.template_test", env_file_encoding="utf-8", extra="ignore")
