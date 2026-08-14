from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ───────────────────────────────────────────────────────────────────
    app_env: str = "development"
    secret_key: str = "changeme"

    # ── Database ──────────────────────────────────────────────────────────────
    database_url: str = (
        "postgresql+asyncpg://incident:incident@localhost:5432/incident_commander"
    )

    # ── Redis ─────────────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"

    # ── Agora ─────────────────────────────────────────────────────────────────
    agora_app_id: str = ""
    agora_app_certificate: str = ""

    # ── AI / ML ───────────────────────────────────────────────────────────────
    deepgram_api_key: str = ""
    gemini_api_key: str = ""
    elevenlabs_api_key: str = ""

    # ── CORS ──────────────────────────────────────────────────────────────────
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]


settings = Settings()
