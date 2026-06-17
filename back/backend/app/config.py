from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    ELEVENLABS_API_KEY: str = ""
    ELEVENLABS_VOICE_ID: str = ""
    AURA_AGENT_URL: str = ""
    AURA_AGENT_TOKEN: str = ""
    SUPABASE_URL: str = ""
    SUPABASE_ANON_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    GEMINI_API_KEY: str = ""
    MISTRAL_API_KEY: str = ""

    # Token partagé que les devices (Raspberry Pi) présentent au cloud
    # (header X-Device-Token). Si défini, les routes /api/device/* l'exigent.
    # En production : remplacer par une table devices (token unique par device).
    DEVICE_TOKEN: str = ""

    class Config:
        env_file = ".env"


@lru_cache
def get_settings() -> Settings:
    return Settings()
