from __future__ import annotations

import os

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="APP_", extra="ignore")

    app_name: str = "OmniQA Test Management System"
    app_version: str = "0.4.0"
    env: str = Field(default_factory=lambda: os.getenv("APP_ENV", os.getenv("ENV", "dev")))
    database_url: str = Field(default_factory=lambda: os.getenv("APP_DATABASE_URL", os.getenv("DATABASE_URL", "sqlite:///./app_auto.db")))
    secret_key: str = Field(default_factory=lambda: os.getenv("APP_SECRET_KEY", os.getenv("SECRET_KEY", "change_me_in_production")), min_length=16)
    algorithm: str = Field(default_factory=lambda: os.getenv("APP_ALGORITHM", os.getenv("ALGORITHM", "HS256")))
    access_token_expire_minutes: int = Field(default_factory=lambda: int(os.getenv("APP_ACCESS_TOKEN_EXPIRE_MINUTES", os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "720"))))
    wecom_webhook_url: str = Field(default_factory=lambda: os.getenv("APP_WECOM_WEBHOOK_URL", os.getenv("WECHAT_WEBHOOK_URL", "")))
    scheduler_timezone: str = Field(default_factory=lambda: os.getenv("APP_SCHEDULER_TIMEZONE", os.getenv("SCHEDULER_TIMEZONE", "Asia/Shanghai")))

    @model_validator(mode="after")
    def _validate_secret_key(self):
        # 生产环境禁止使用弱默认值，避免部署时静默降级。
        if self.env.lower() != "dev" and self.secret_key == "change_me_in_production":
            raise ValueError("APP_SECRET_KEY/SECRET_KEY 未配置。生产环境必须设置强密钥。")
        return self

    @property
    def allow_default_admin_seed(self) -> bool:
        return self.env.lower() == "dev"


settings = Settings()
