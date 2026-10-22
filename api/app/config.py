from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_provider: str = "gemini"
    gemini_api_key: str = ""
    gemini_chat_model: str = "gemini-3.5-flash-lite"

    database_url: str = "postgresql://postgres:postgres@localhost:5432/inboxgate"

    # Shared secret n8n sends in the X-Workflow-Token header.
    workflow_token: str = ""
    # n8n webhook that receives new emails. When empty, the app triages emails
    # itself and skips n8n (direct mode), which is handy for local runs.
    n8n_inbound_url: str = ""

    # Optional password for the review screens. Leave empty for an open demo.
    review_password: str = ""

    # Spam above this confidence is archived without review.
    auto_archive_confidence: float = 0.85
    # Emails simulated per minute per visitor on the public demo.
    simulate_rate_limit: int = 6


@lru_cache
def get_settings() -> Settings:
    return Settings()
