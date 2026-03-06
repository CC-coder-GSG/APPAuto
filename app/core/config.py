from __future__ import annotations

import os

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="APP_", extra="ignore")

    app_name: str = "OmniQA Test Management System"
    app_version: str = "0.4.0"
    env: str = "dev"
    database_url: str = "sqlite:///./app_auto.db"
    secret_key: str = Field(default=os.getenv("APP_SECRET_KEY", "change_me_in_production"), min_length=16)
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 720
    wecom_webhook_url: str = ""
    scheduler_timezone: str = "Asia/Shanghai"

    @property
    def allow_default_admin_seed(self) -> bool:
        return self.env.lower() == "dev"


settings = Settings()
