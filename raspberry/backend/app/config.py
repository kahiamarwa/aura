from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    """Configuration du backend DEVICE (Raspberry Pi).

    SÉCURITÉ — Le device ne contient AUCUNE clé API tierce.
    Il ne parle qu'au backend cloud, qui détient seul les secrets
    (ElevenLabs, Anthropic, Mistral, Supabase service key, etc.).
    Le device n'a besoin que de :
      - l'URL du backend cloud
      - un DEVICE_TOKEN (identité du device, provisionné à l'appairage,
        PAS baké en usine — révocable côté cloud)
    Ainsi, l'image flashée par le fabricant ne contient aucune donnée sensible.
    """

    # URL du backend cloud (proxy unique vers tous les services tiers)
    CLOUD_BACKEND_URL: str = "https://backend-aura.hallia.ai"

    # Identité du device — provisionnée à l'appairage, jamais en usine
    DEVICE_TOKEN: str = ""

    class Config:
        env_file = ".env"


@lru_cache
def get_settings() -> Settings:
    return Settings()
