"""Configuration de l'orchestrateur enceinte (device headless).

AUCUN secret tiers ici. Le device ne parle qu'au backend cloud.
Variables provisionnées à l'appairage (pas bakées en usine) :
  - CLOUD_BACKEND_URL : où joindre le cloud
  - DEVICE_TOKEN      : identité du device (révocable côté cloud)
  - USER_TOKEN        : JWT de l'utilisateur appairé (Phase 2 : statique ;
                        Phase 4 : remplacé par un vrai flux d'appairage + refresh)
"""

import os
from pathlib import Path

# ── Cloud ────────────────────────────────────────────────────────────
CLOUD_BACKEND_URL = os.getenv("CLOUD_BACKEND_URL", "http://localhost:8000")
DEVICE_TOKEN = os.getenv("DEVICE_TOKEN", "")
USER_TOKEN = os.getenv("USER_TOKEN", "")  # JWT statique (fallback / dev)

# ── Auth DURABLE : renouvellement automatique du JWT ─────────────────
# Le device stocke un REFRESH TOKEN (longue durée, provisionné à l'appairage)
# et renouvelle le JWT tout seul via Supabase. Le JWT renouvelé est persisté
# dans AURA_TOKEN_FILE → survit aux redémarrages, AUCUN ré-export manuel.
# SUPABASE_URL + ANON_KEY sont PUBLICS (pas des secrets : la RLS protège tout).
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://wdmlgtrjptfhxldmqxzm.supabase.co")
SUPABASE_ANON_KEY = os.getenv(
    "SUPABASE_ANON_KEY",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6IndkbWxndHJqcHRmaHhsZG1xeHptIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzIxOTA3NjAsImV4cCI6MjA4Nzc2Njc2MH0.VHrom_X0cdoDd5Yh04mZnDHQKecdiKH6QxMkmLCgIsM",
)
REFRESH_TOKEN = os.getenv("AURA_REFRESH_TOKEN", "")
TOKEN_FILE = os.path.expanduser(os.getenv("AURA_TOKEN_FILE", "~/.aura/session.json"))

# ── Audio ────────────────────────────────────────────────────────────
SAMPLE_RATE = 16000          # openWakeWord + STT attendent 16 kHz mono
FRAME_SAMPLES = 1280         # 80 ms à 16 kHz (taille de frame openWakeWord)
_input = os.getenv("AUDIO_INPUT_DEVICE")
# sounddevice accepte un index (int) OU un nom (str). On parse l'int si numérique.
INPUT_DEVICE = int(_input) if _input and _input.isdigit() else (_input or None)

# ── Wake word (modèles ONNX locaux) ──────────────────────────────────
_REPO_ROOT = Path(__file__).resolve().parents[1]
OPENWAKE_DIR = Path(os.getenv("OPENWAKE_DIR", _REPO_ROOT / "openwake"))
ACTIVATE_MODEL = os.getenv("ACTIVATE_MODEL", "Aura_test.onnx")  # "Dis Aura"
INTERRUPT_MODEL = os.getenv("INTERRUPT_MODEL", "stop_aura.onnx")  # "Stop Aura"
WAKE_THRESHOLDS = {
    Path(ACTIVATE_MODEL).stem: float(os.getenv("ACTIVATE_THRESHOLD", "0.6")),
    # stop_aura plus strict : il faux-déclenche sur la parole ambiante
    Path(INTERRUPT_MODEL).stem: float(os.getenv("INTERRUPT_THRESHOLD", "0.85")),
}
# Gate vocal sur le wake word : OFF par défaut. La vérif ECAPA sur l'audio court
# du wake word est trop instable (rejette le vrai utilisateur ~0.23). On répond à
# tout le monde (comme Alexa) et on mise sur le traitement du bruit. WAKE_SPEAKER_GATE=1 pour réactiver.
WAKE_SPEAKER_GATE = os.getenv("WAKE_SPEAKER_GATE", "0") == "1"
WAKE_COOLDOWN_S = 1.5

# ── Silero VAD (détection de parole robuste, modèle ONNX) ────────────
# silero_vad.onnx est téléchargé par openwakeword (download_models()).
SILERO_VAD_PATH = Path(os.getenv("SILERO_VAD_PATH", OPENWAKE_DIR.parent / "device" / "silero_vad.onnx"))
VAD_FRAME_SIZE = 512            # 32 ms à 16 kHz (taille de frame Silero)
VAD_PROB_THRESHOLD = 0.5       # proba de parole au-dessus = parole
VAD_SPEECH_FRAMES = 2          # frames consécutives pour démarrer (~hystérésis)
VAD_SILENCE_FRAMES = 20        # frames de silence pour clore (~0.6s à 32ms/frame)

# ── Capture de la commande ───────────────────────────────────────────
CMD_SILENCE_RMS = float(os.getenv("CMD_SILENCE_RMS", "300"))   # fallback énergie si pas de VAD
CMD_SILENCE_HANG_S = 1.0      # silence consécutif pour clore la commande
CMD_MAX_S = 12.0              # durée max d'une commande (cap de sécurité absolu)
CMD_MIN_SPEECH_S = 0.3        # parole min pour considérer une vraie commande

# ── Endpointing par LOCUTEUR CIBLE (robuste en milieu bruyant) ───────
# ON : marche bien sur une commande medium/longue (assez d'audio pour identifier
# l'utilisateur). Seul le wake word (audio court 1,5s) était instable → lui seul
# est désactivé (WAKE_SPEAKER_GATE). TARGET_ENDPOINTING=0 pour repasser énergie/VAD.
TARGET_ENDPOINTING = os.getenv("TARGET_ENDPOINTING", "1") == "1"
# L'enceinte s'arrête quand TA voix s'arrête, en ignorant les autres voix.
TARGET_WINDOW_S = 1.5          # fenêtre glissante pour décider "c'est lui ?"
TARGET_HOP_S = 0.4            # cadence de décision (toutes les 0.4 s)
TARGET_HANG_S = float(os.getenv("TARGET_HANG_S", "2.0"))   # absence de TA voix pour clore (tolère les pauses de réflexion)
TARGET_MISS_HYSTERESIS = 2    # fenêtres "pas lui" consécutives avant de compter l'absence
TARGET_WAIT_START_S = 4.0     # si TA voix n'apparaît jamais après le wake word → abandon
# Une fois la commande DÉMARRÉE, on garde tant que le score reste au-dessus de ce
# seuil (bas) : la voix de l'utilisateur varie, on ne le coupe que si c'est
# CLAIREMENT quelqu'un d'autre (score nettement négatif). Évite de couper au milieu.
TARGET_KEEP_THRESHOLD = float(os.getenv("TARGET_KEEP_THRESHOLD", "0.0"))

# ── Endpointing SÉMANTIQUE (tolère les pauses de réflexion) ──────────
# À chaque pause, le cloud vérifie si la phrase est finie (Haiku). Si tu
# réfléchissais (phrase incomplète), on garde l'écoute ouverte.
# OFF par défaut : il bouclait (Haiku « incomplet » sur des phrases complètes).
# Le hang locuteur cible (2s) couvre déjà les pauses de réflexion normales.
SEMANTIC_ENDPOINTING = os.getenv("SEMANTIC_ENDPOINTING", "0") == "1"
SEMANTIC_MAX_CHECKS = 3        # nb max de vérifications de complétude par commande
SEMANTIC_MAX_S = 15.0          # au-delà → on traite (cap de sécurité)
TARGET_WAIT_CONTINUE_S = 3.0   # délai d'attente de la suite après une pause de réflexion

# ── Gardes anti-boucle (le device revient TOUJOURS à IDLE) ───────────
MAX_WASTED = int(os.getenv("MAX_WASTED", "2"))        # cycles sans réponse → IDLE
MAX_CONV_TURNS = int(os.getenv("MAX_CONV_TURNS", "8"))  # tours max en conversation → IDLE

# ── Conversation continue (parité web) ───────────────────────────────
CONVERSATION_WINDOW_S = 12.0   # fenêtre pour répondre sans wake word (conversing)
CONVERSING_RMS = float(os.getenv("CONVERSING_RMS", "350"))   # seuil parole en conversing
SPEAKING_RMS = float(os.getenv("SPEAKING_RMS", "600"))       # seuil barge-in pendant TTS (> écho)
FOLLOWUP_SPEECH_FRAMES = 3     # frames consécutives pour déclencher un follow-up/barge-in

# ── Contexte ambiant (STT passif) ────────────────────────────────────
AMBIENT_ENABLED = os.getenv("AMBIENT_ENABLED", "1") == "1"
AMBIENT_BATCH_S = 15.0         # envoi d'un batch ambiant toutes les 15 s
AMBIENT_PREFIX = "[Conversation ambiante]: "
MAX_CONTEXT_SEGMENTS = 50      # taille max du buffer de contexte
MAX_CONTEXT_AGE_S = 30 * 60    # 30 min
