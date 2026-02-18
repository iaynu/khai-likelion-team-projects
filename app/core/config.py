from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./backbone.db"  # 기본값을 SQLite로 변경

    # App
    APP_TITLE: str = "Backbone Backend"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = True

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,  # 대소문자 구분 안함
        extra="ignore"  # 추가 필드 무시
    )


settings = Settings()
