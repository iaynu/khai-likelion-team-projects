from typing import List

from pydantic import field_validator
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

    # Simulation Engine
    SIMULATION_ENGINE_PATH: str = ""  # 절대경로: C:/Users/user/simulation

    # CORS
    # 명시 허용 origin 목록.
    # 환경변수: CORS_ALLOWED_ORIGINS='["http://localhost:3000","https://example.vercel.app"]'
    # 또는 쉼표 구분: CORS_ALLOWED_ORIGINS=http://localhost:3000,https://example.vercel.app
    CORS_ALLOWED_ORIGINS: List[str] = ["http://localhost:3000"]
    # Vercel Preview 도메인을 정규식으로 허용 (프로젝트명 고정 권장).
    # prod에서는 좁은 정규식 사용: ^https://frontend-ten-beta-37\.vercel\.app$
    CORS_ALLOWED_ORIGIN_REGEX: str = r"^https://frontend-.*\.vercel\.app$"

    @field_validator("CORS_ALLOWED_ORIGINS", mode="before")
    @classmethod
    def _parse_cors_origins(cls, v: object) -> object:
        """env에서 쉼표 구분 문자열 또는 JSON 배열 모두 허용."""
        if isinstance(v, str):
            import json
            stripped = v.strip()
            if stripped.startswith("["):
                return json.loads(stripped)
            return [o.strip() for o in stripped.split(",") if o.strip()]
        return v

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
