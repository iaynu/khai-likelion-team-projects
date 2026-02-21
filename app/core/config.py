from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./backbone.db"  # 기본값을 SQLite로 변경

    # Redis & Celery
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/1"

    # API Security
    WORKER_API_KEY: str = "change-me-in-production"  # Worker가 /save 엔드포인트 호출 시 사용

    # OpenAI
    OPENAI_API_KEY: str = ""  # OpenAI API Key for GPT

    # App
    APP_TITLE: str = "Backbone Backend"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = True
    SQL_ECHO: bool = False  # Set to True to enable SQL query logging

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,  # 대소문자 구분 안함
        extra="ignore"  # 추가 필드 무시
    )


settings = Settings()
