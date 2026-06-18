from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    ELEVENLABS_API_KEY: str = ""
    ELEVENLABS_VOICE_ID: str = ""
    AURA_AGENT_URL: str = ""
    AURA_AGENT_TOKEN: str = ""
    SUPABASE_URL: str = ""
    SUPABASE_ANON_KEY: str = ""
    # Clé service role (SERVEUR uniquement, jamais sur le device) : permet de
    # résoudre device→utilisateur et d'agir pour l'utilisateur appairé.
    SUPABASE_SERVICE_ROLE_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    GEMINI_API_KEY: str = ""
    MISTRAL_API_KEY: str = ""

    # Token présenté par les enceintes (devices headless) via X-Device-Token.
    # Si défini, les routes /api/device/* l'exigent. Production : table devices.
    DEVICE_TOKEN: str = ""

    class Config:
        env_file = ".env"


@lru_cache
def get_settings() -> Settings:
    return Settings()
