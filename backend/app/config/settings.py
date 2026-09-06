from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


_BACKEND_DIR = Path(__file__).resolve().parents[2]
_PROJECT_DIR = _BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(_PROJECT_DIR / ".env", _BACKEND_DIR / ".env", ".env"),
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
    groq_api_key: str = ""
    elevenlabs_api_key: str = ""
    enable_spoken_summaries: bool = True
    spoken_summary_interval: int = 180
    
    # ── Sentinel Preferences ─────────────────────────────────────────────────────
    default_mode: str = "occasional"  # "frequent" or "occasional"
    enable_mute_control: bool = True
    

    # Optional external action integrations; mocked when unset.
    slack_webhook_url: str = ""
    slack_bot_token: str = ""
    slack_default_channel: str = "#incidents"
    pagerduty_routing_key: str = ""
    jira_api_token: str = ""
    jira_domain: str = ""           # e.g. yourorg.atlassian.net
    jira_user_email: str = ""       # Atlassian account email
    jira_project_key: str = "INC"   # Default Jira project key

    # ── CORS ──────────────────────────────────────────────────────────────────
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


settings = Settings()
