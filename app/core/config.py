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

    # Zentao API direct integration
    # Used to encrypt/decrypt per-user Zentao passwords stored in user_zentao_bindings.
    # Must be a URL-safe base64-encoded 32-byte key (Fernet key).
    # Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    # If not set, a deterministic fallback derived from secret_key is used (development only).
    zentao_binding_secret: str = Field(default_factory=lambda: os.getenv('APP_ZENTAO_BINDING_SECRET', os.getenv('ZENTAO_BINDING_SECRET', '')))

    # Jenkins 集成：用户用各自的 Jenkins 账号 + API Token 触发自动化测试 Job。
    # base_url / view 仅用于前端预填默认值；凭据按用户加密存储在 user_jenkins_bindings。
    jenkins_default_base_url: str = Field(default_factory=lambda: os.getenv('APP_JENKINS_BASE_URL', os.getenv('JENKINS_BASE_URL', 'http://192.168.2.229:8080')))
    jenkins_default_view: str = Field(default_factory=lambda: os.getenv('APP_JENKINS_DEFAULT_VIEW', os.getenv('JENKINS_DEFAULT_VIEW', '自动化测试')))

    # Zentao browser sync integration
    zentao_sync_enabled: bool = Field(default_factory=lambda: os.getenv('APP_ZENTAO_SYNC_ENABLED', os.getenv('ZENTAO_SYNC_ENABLED', 'true')).lower() == 'true')
    zentao_sync_api_key: str = Field(default_factory=lambda: os.getenv('APP_ZENTAO_SYNC_API_KEY', os.getenv('ZENTAO_SYNC_API_KEY', '')))
    zentao_sync_auto_map_testcase: bool = Field(default_factory=lambda: os.getenv('APP_ZENTAO_SYNC_AUTO_MAP_TESTCASE', os.getenv('ZENTAO_SYNC_AUTO_MAP_TESTCASE', 'true')).lower() == 'true')
    zentao_sync_auto_apply_testcase: bool = Field(default_factory=lambda: os.getenv('APP_ZENTAO_SYNC_AUTO_APPLY_TESTCASE', os.getenv('ZENTAO_SYNC_AUTO_APPLY_TESTCASE', 'true')).lower() == 'true')
    zentao_sync_auto_apply_bug: bool = Field(default_factory=lambda: os.getenv('APP_ZENTAO_SYNC_AUTO_APPLY_BUG', os.getenv('ZENTAO_SYNC_AUTO_APPLY_BUG', 'true')).lower() == 'true')
    zentao_background_sync_enabled: bool = Field(default_factory=lambda: os.getenv('APP_ZENTAO_BACKGROUND_SYNC_ENABLED', os.getenv('ZENTAO_BACKGROUND_SYNC_ENABLED', 'true')).lower() == 'true')
    zentao_background_sync_username: str = Field(default_factory=lambda: os.getenv('APP_ZENTAO_BACKGROUND_SYNC_USERNAME', os.getenv('ZENTAO_BACKGROUND_SYNC_USERNAME', '')).strip())
    zentao_workbench_recent_sync_interval_minutes: int = Field(default_factory=lambda: int(os.getenv('APP_ZENTAO_WORKBENCH_RECENT_SYNC_INTERVAL_MINUTES', os.getenv('ZENTAO_WORKBENCH_RECENT_SYNC_INTERVAL_MINUTES', '10'))))
    # On-enter freshness thresholds. If the local mirror has not been touched
    # within this window when a workbench page opens, a lightweight pull is
    # triggered before the page renders. Bug data changes more often than
    # testcase data, so they get separate knobs.
    zentao_workbench_bug_recent_ttl_seconds: int = Field(default_factory=lambda: int(os.getenv('APP_ZENTAO_WORKBENCH_BUG_RECENT_TTL_SECONDS', os.getenv('ZENTAO_WORKBENCH_BUG_RECENT_TTL_SECONDS', '180'))))
    zentao_workbench_testcase_recent_ttl_seconds: int = Field(default_factory=lambda: int(os.getenv('APP_ZENTAO_WORKBENCH_TESTCASE_RECENT_TTL_SECONDS', os.getenv('ZENTAO_WORKBENCH_TESTCASE_RECENT_TTL_SECONDS', '300'))))
    zentao_testcase_detail_refresh_ttl_seconds: int = Field(default_factory=lambda: int(os.getenv('APP_ZENTAO_TESTCASE_DETAIL_REFRESH_TTL_SECONDS', os.getenv('ZENTAO_TESTCASE_DETAIL_REFRESH_TTL_SECONDS', '600'))))
    zentao_nightly_full_sync_hour: int = Field(default_factory=lambda: int(os.getenv('APP_ZENTAO_NIGHTLY_FULL_SYNC_HOUR', os.getenv('ZENTAO_NIGHTLY_FULL_SYNC_HOUR', '2'))))
    zentao_nightly_full_sync_minute: int = Field(default_factory=lambda: int(os.getenv('APP_ZENTAO_NIGHTLY_FULL_SYNC_MINUTE', os.getenv('ZENTAO_NIGHTLY_FULL_SYNC_MINUTE', '20'))))

    # Zentao → n8n AI webhook forwarder
    # Access is gated by the "zentao-ai" tab permission (managed via the admin UI).
    # The webhook URL is where assembled story payloads get POSTed; leaving it empty
    # disables the feature server-side even for permitted users.
    n8n_zentao_ai_webhook_url: str = Field(default_factory=lambda: os.getenv('APP_N8N_ZENTAO_AI_WEBHOOK_URL', os.getenv('N8N_ZENTAO_AI_WEBHOOK_URL', '')))
    n8n_zentao_ai_webhook_token: str = Field(default_factory=lambda: os.getenv('APP_N8N_ZENTAO_AI_WEBHOOK_TOKEN', os.getenv('N8N_ZENTAO_AI_WEBHOOK_TOKEN', '')))
    # Rough AI run duration used by the frontend countdown toast. Seconds. Default 5 min.
    zentao_ai_expected_duration_seconds: int = Field(default_factory=lambda: int(os.getenv('APP_ZENTAO_AI_EXPECTED_DURATION_SECONDS', os.getenv('ZENTAO_AI_EXPECTED_DURATION_SECONDS', '300'))))

    # CORS
    cors_allowed_origins_raw: str = Field(default_factory=lambda: os.getenv('APP_CORS_ALLOWED_ORIGINS', os.getenv('CORS_ALLOWED_ORIGINS', '')))

    @property
    def cors_allowed_origins(self) -> list[str]:
        origins = _split_csv(self.cors_allowed_origins_raw)
        if origins:
            return origins
        return ['*']

    @model_validator(mode='after')
    def _validate_secret_key(self):
        if self.env.lower() != 'dev' and self.secret_key == 'change_me_in_production':
            raise ValueError('APP_SECRET_KEY/SECRET_KEY 未配置。生产环境必须设置强密钥。')
        return self

    @property
    def allow_default_admin_seed(self) -> bool:
        return self.env.lower() == 'dev'


settings = Settings()
