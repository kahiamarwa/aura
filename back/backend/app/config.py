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
    # résoudre device→utilisateur (lecture de la table devices, bypass RLS).
    SUPABASE_SERVICE_ROLE_KEY: str = ""
    # Secret JWT du projet (SERVEUR uniquement) : permet de FORGER un JWT
    # utilisateur court pour l'enceinte appairée — session dédiée par device,
    # indépendante du web (aucun refresh token partagé, zéro conflit).
    SUPABASE_JWT_SECRET: str = ""
    ANTHROPIC_API_KEY: str = ""
    GEMINI_API_KEY: str = ""
    MISTRAL_API_KEY: str = ""
    DEEPGRAM_API_KEY: str = ""   # Flux (STT streaming + turn detection) — chemin B

    # Token présenté par les enceintes (devices headless) via X-Device-Token.
    # Si défini, les routes /api/device/* l'exigent. Production : table devices.
    DEVICE_TOKEN: str = ""

    # Rejeter une commande si le locuteur n'est pas l'utilisateur enrôlé (sécurité) ?
    # OFF par défaut : l'ECAPA backend rejette à tort le vrai utilisateur (score ~0)
    # → bloquait des réponses légitimes. Le WAKE WORD est la garde (modèle Alexa) ;
    # la voix reste AFFICHÉE (badge ✓/✗ + score) mais ne BLOQUE plus la réponse.
    # VERIFY_SPEAKER_ENFORCE=1 pour re-bloquer (quand l'enrôlement sera fiabilisé).
    VERIFY_SPEAKER_ENFORCE: bool = False

    # Seuil de confiance pour REJETER une phrase "pas pour Aura" (en mode suivi).
    # Plus BAS = filtre plus agressif (utile en milieu bruyant / discussions).
    # 0.6 par défaut : on coupe le suivi dès que Haiku est raisonnablement sûr.
    INTENT_CONFIDENCE: float = 0.6

    # Deepgram Flux (chemin B) : seuil de confiance fin de tour (0.5-0.9 ; ↑ = plus
    # patient, attend que tu aies vraiment fini).
    FLUX_EOT_THRESHOLD: float = 0.7
    # Filet : fin de tour forcée après ce silence (ms), même sous le seuil. 5000 =
    # défaut Deepgram ; 2500-3000 plus réactif en français. Configurable (I8).
    FLUX_EOT_TIMEOUT_MS: int = 3000

    class Config:
        env_file = ".env"


@lru_cache
def get_settings() -> Settings:
    return Settings()
