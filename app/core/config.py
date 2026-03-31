from __future__ import annotations

import os

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in (value or '').split(',') if item.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_prefix='APP_', extra='ignore')

    app_name: str = 'OmniQA Test Management System'
    app_version: str = '0.4.0'
    env: str = Field(default_factory=lambda: os.getenv('APP_ENV', os.getenv('ENV', 'dev')))
    database_url: str = Field(default_factory=lambda: os.getenv('APP_DATABASE_URL', os.getenv('DATABASE_URL', 'sqlite:///./app_auto.db')))
    secret_key: str = Field(default_factory=lambda: os.getenv('APP_SECRET_KEY', os.getenv('SECRET_KEY', 'change_me_in_production')), min_length=16)
    algorithm: str = Field(default_factory=lambda: os.getenv('APP_ALGORITHM', os.getenv('ALGORITHM', 'HS256')))
    access_token_expire_minutes: int = Field(default_factory=lambda: int(os.getenv('APP_ACCESS_TOKEN_EXPIRE_MINUTES', os.getenv('ACCESS_TOKEN_EXPIRE_MINUTES', '720'))))
    wecom_webhook_url: str = Field(default_factory=lambda: os.getenv('APP_WECOM_WEBHOOK_URL', os.getenv('WECHAT_WEBHOOK_URL', '')))
    scheduler_timezone: str = Field(default_factory=lambda: os.getenv('APP_SCHEDULER_TIMEZONE', os.getenv('SCHEDULER_TIMEZONE', 'Asia/Shanghai')))

    # Zentao browser sync integration
    zentao_sync_enabled: bool = Field(default_factory=lambda: os.getenv('APP_ZENTAO_SYNC_ENABLED', os.getenv('ZENTAO_SYNC_ENABLED', 'true')).lower() == 'true')
    zentao_sync_api_key: str = Field(default_factory=lambda: os.getenv('APP_ZENTAO_SYNC_API_KEY', os.getenv('ZENTAO_SYNC_API_KEY', '')))
    zentao_sync_auto_map_testcase: bool = Field(default_factory=lambda: os.getenv('APP_ZENTAO_SYNC_AUTO_MAP_TESTCASE', os.getenv('ZENTAO_SYNC_AUTO_MAP_TESTCASE', 'true')).lower() == 'true')
    zentao_sync_auto_apply_testcase: bool = Field(default_factory=lambda: os.getenv('APP_ZENTAO_SYNC_AUTO_APPLY_TESTCASE', os.getenv('ZENTAO_SYNC_AUTO_APPLY_TESTCASE', 'true')).lower() == 'true')
    zentao_sync_auto_apply_bug: bool = Field(default_factory=lambda: os.getenv('APP_ZENTAO_SYNC_AUTO_APPLY_BUG', os.getenv('ZENTAO_SYNC_AUTO_APPLY_BUG', 'true')).lower() == 'true')

    # CORS
    cors_allowed_origins_raw: str = Field(default_factory=lambda: os.getenv('APP_CORS_ALLOWED_ORIGINS', os.getenv('CORS_ALLOWED_ORIGINS', '')))

    @property
    def cors_allowed_origins(self) -> list[str]:
        origins = _split_csv(self.cors_allowed_origins_raw)
        if origins:
            return origins
        if self.env.lower() == 'dev':
            return ['*']
        return []

    @model_validator(mode='after')
    def _validate_secret_key(self):
        if self.env.lower() != 'dev' and self.secret_key == 'change_me_in_production':
            raise ValueError('APP_SECRET_KEY/SECRET_KEY 未配置。生产环境必须设置强密钥。')
        return self

    @property
    def allow_default_admin_seed(self) -> bool:
        return self.env.lower() == 'dev'


settings = Settings()
