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


def _load_env_file():
    """Charge un fichier d'env persistant (provisionné à l'installation) pour ne
    PAS avoir à exporter les variables à chaque démarrage du Pi.

    Cherche ~/.aura/env puis device/.env. Format : KEY=VALUE (une par ligne).
    Les variables déjà exportées dans le shell ont priorité (setdefault).
    """
    for p in (os.path.expanduser("~/.aura/env"), str(Path(__file__).resolve().parent / ".env")):
        try:
            with open(p) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        except FileNotFoundError:
            continue
        except Exception:
            continue


_load_env_file()

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

# ── AEC : annulation d'écho (le micro ne capte plus le haut-parleur) ──
# Quand activé (après avoir lancé setup_aec.sh sur le Pi), capture ET lecture
# passent par le périphérique "pulse" (ALSA → PipeWire) où vit module-echo-cancel
# (WebRTC AEC). Aura cesse de s'entendre elle-même → barge-in/« Stop Aura »
# fiables PENDANT qu'elle parle. OFF par défaut (rien ne change tant que le setup
# n'est pas fait). L'alignement temporel écho est géré par l'OS (éprouvé).
AEC_ENABLED = os.getenv("AEC_ENABLED", "0") == "1"

# ── LED d'états (KY-016 RGB sur GPIO) ────────────────────────────────
# Reflète l'état d'Aura sur une LED physique (comme l'orbe). Optionnel.
LED_ENABLED = os.getenv("LED_ENABLED", "0") == "1"
LED_R_PIN = int(os.getenv("LED_R_PIN", "13"))   # broche physique 33
LED_G_PIN = int(os.getenv("LED_G_PIN", "19"))   # broche physique 35
LED_B_PIN = int(os.getenv("LED_B_PIN", "26"))   # broche physique 37

# Détection « micro coupé » : durée de silence PLAT (que des zéros = source morte,
# cas AEC où PipeWire envoie du silence au lieu de couper le flux) → LED rouge.
MIC_DEAD_S = float(os.getenv("MIC_DEAD_S", "3.0"))

# ── Mute logiciel à distance (mode confidentiel, piloté par le web) ──
# Le device interroge le cloud tous les MUTE_POLL_S s ; si muté → coupe micro +
# ambiant (rien n'est envoyé au cloud). MUTE_POLL=0 désactive le poll.
MUTE_POLL_S = float(os.getenv("MUTE_POLL_S", "2.0"))
# Nom du PCM ALSA qui ponte vers PipeWire (où vit l'annulateur). "pulse" par
# défaut (plugin libasound2-plugins). Configurable si le setup expose un autre nom.
AEC_ALSA_DEVICE = os.getenv("AEC_ALSA_DEVICE", "pulse")

_input = os.getenv("AUDIO_INPUT_DEVICE")
# sounddevice accepte un index (int) OU un nom (str). On parse l'int si numérique.
if _input:
    INPUT_DEVICE = int(_input) if _input.isdigit() else _input
elif AEC_ENABLED:
    INPUT_DEVICE = AEC_ALSA_DEVICE    # capture via l'annulateur d'écho (PipeWire)
else:
    INPUT_DEVICE = None

# Sortie audio EXPLICITE (mpg123/aplay). Priorité :
#   1. bridge pulse si AEC activé (référence d'écho)
#   2. AUDIO_OUTPUT_DEVICE si défini (ex: "plughw:0" pour le jack 3,5mm) — BYPASSE PipeWire,
#      utile quand le défaut système (sink PipeWire/echo-cancel) est cassé ou n'est pas le jack
#   3. sinon None = défaut ALSA
AUDIO_OUTPUT_DEVICE = os.getenv("AUDIO_OUTPUT_DEVICE", "")
PLAYBACK_ALSA_DEVICE = (AEC_ALSA_DEVICE if AEC_ENABLED
                        else (AUDIO_OUTPUT_DEVICE or None))

# ── Wake word (modèles ONNX locaux) ──────────────────────────────────
_REPO_ROOT = Path(__file__).resolve().parents[1]
OPENWAKE_DIR = Path(os.getenv("OPENWAKE_DIR", _REPO_ROOT / "openwake"))
ACTIVATE_MODEL = os.getenv("ACTIVATE_MODEL", "Aura_test.onnx")  # "Dis Aura"
INTERRUPT_MODEL = os.getenv("INTERRUPT_MODEL", "stop_aura.onnx")  # "Stop Aura"
WAKE_THRESHOLDS = {
    # 0.4 (était 0.6) : « Dis Aura » dépassait rarement 0.6 → 3-4 essais. Affiner
    # avec WAKE_DEBUG=1 (qui logge le pic réel) puis figer au point de séparation.
    Path(ACTIVATE_MODEL).stem: float(os.getenv("ACTIVATE_THRESHOLD", "0.4")),
    # stop_aura plus strict : il faux-déclenche sur la parole ambiante
    Path(INTERRUPT_MODEL).stem: float(os.getenv("INTERRUPT_THRESHOLD", "0.85")),
}
# WAKE_DEBUG=1 : logge le pic de score du wake word à CHAQUE frame (>0.1) pour
# calibrer le seuil empiriquement. À couper en prod (verbeux).
WAKE_DEBUG = os.getenv("WAKE_DEBUG", "0") == "1"
# Gate vocal sur le wake word : OFF par défaut. La vérif ECAPA sur l'audio court
# du wake word est trop instable (rejette le vrai utilisateur ~0.23). On répond à
# tout le monde (comme Alexa) et on mise sur le traitement du bruit. WAKE_SPEAKER_GATE=1 pour réactiver.
WAKE_SPEAKER_GATE = os.getenv("WAKE_SPEAKER_GATE", "0") == "1"
WAKE_COOLDOWN_S = 1.5

# ── Silero VAD (détection de parole robuste, modèle ONNX) ────────────
# silero_vad.onnx est téléchargé par openwakeword (download_models()).
SILERO_VAD_PATH = Path(os.getenv("SILERO_VAD_PATH", OPENWAKE_DIR.parent / "device" / "silero_vad.onnx"))
VAD_FRAME_SIZE = 512            # 32 ms à 16 kHz (taille de frame Silero)
VAD_PROB_THRESHOLD = float(os.getenv("VAD_PROB_THRESHOLD", "0.5"))   # proba parole (Silero) au-dessus = parole
# 1 = endpointing piloté par Silero VAD ; 0 = ancien comportement ÉNERGIE (RMS).
# Mets 0 si le VAD ne détecte plus ta parole (retour au connu-bon).
ENDPOINT_VAD = os.getenv("ENDPOINT_VAD", "1") == "1"

# ── Smart Turn v3 — détection de fin de tour (chemin B, façon Alexa) ──
# Modèle audio local (BSD-2) qui décide « l'utilisateur a fini ? » aux pauses VAD.
# OFF par défaut (chantier en cours). pip install onnxruntime huggingface_hub.
SMART_TURN_ENABLED = os.getenv("SMART_TURN_ENABLED", "0") == "1"
SMART_TURN_MODEL = os.getenv("SMART_TURN_MODEL", "")          # vide → download HF (variante CPU int8)
SMART_TURN_THRESHOLD = float(os.getenv("SMART_TURN_THRESHOLD", "0.5"))   # proba > seuil = fini

# ── Mode STREAMING (chemin B complet : WS + Scribe + Smart Turn) ──────
# 1 = nouveau flux temps réel (remplace capture-WAV-puis-envoi). Nécessite le
# backend déployé (route /api/device-stream) + pip install websocket-client.
# OFF par défaut → l'ancien flux (cloud.converse) reste le fallback.
STREAMING_MODE = os.getenv("STREAMING_MODE", "0") == "1"
STREAM_PAUSE_S = float(os.getenv("STREAM_PAUSE_S", "0.4"))   # silence avant de tester Smart Turn
# Endpointing au SILENCE en mode streaming (quand Smart Turn est OFF) : durée de
# silence tolérée avant de clore le tour. Généreux (1,5s) → tu peux hésiter sans
# être coupé. C'est l'approche FIABLE (Smart Turn v3 est trop pressé en français).
STREAM_SILENCE_S = float(os.getenv("STREAM_SILENCE_S", "1.5"))
# Garde-fou lecture : si le backend n'envoie RIEN pendant ce délai (réponse/audio),
# on abandonne la lecture au lieu de rester bloqué sur SPEAKING. Couvre LLM + TTS lents.
STREAM_RESPONSE_TIMEOUT_S = float(os.getenv("STREAM_RESPONSE_TIMEOUT_S", "60"))
# Garde-fou : durée MAX de la lecture (SPEAKING). Si mpg123 fige sur une sortie audio
# cassée, on coupe au lieu de rester bloqué pour toujours. Large (5 min) pour ne pas
# couper une réponse longue légitime ; c'est juste un backstop anti-blocage.
SPEAK_MAX_S = float(os.getenv("SPEAK_MAX_S", "300"))
VAD_SPEECH_FRAMES = 2          # frames consécutives pour démarrer (~hystérésis)
VAD_SILENCE_FRAMES = 20        # frames de silence pour clore (~0.6s à 32ms/frame)

# ── Capture de la commande ───────────────────────────────────────────
CMD_SILENCE_RMS = float(os.getenv("CMD_SILENCE_RMS", "300"))   # seuil énergie de SECOURS (si Silero VAD indispo)
CMD_SILENCE_HANG_S = 1.0      # silence consécutif pour clore la commande
CMD_MAX_S = float(os.getenv("CMD_MAX_S", "20"))   # cap de sécurité (ANCIEN chemin non-streaming uniquement)
CMD_MIN_SPEECH_S = 0.3        # parole min pour considérer une vraie commande
# Chemin STREAMING (Flux) : capture ILLIMITÉE — clôture par « Stop Aura » ou Flux.
# Filet anti-blocage très long qui SOUMET la commande (jamais jeter). 0 = désactivé.
CMD_HARD_CAP_S = float(os.getenv("CMD_HARD_CAP_S", "300"))
# Grâce après un force EOT : temps laissé au backend pour renvoyer turn_end.
CMD_FORCE_GRACE_S = float(os.getenv("CMD_FORCE_GRACE_S", "8"))
# Seuil « Stop Aura » PENDANT LA CAPTURE (Aura muette → pas d'écho TTS) : bien plus bas
# que le 0.85 anti-écho du SPEAKING. Terrain 06/07 : vrais « Stop Aura » à 0.65-0.99.
# Un faux positif ici SOUMET la commande (bénin) — il ne la jette pas.
STOP_CAPTURE_THRESHOLD = float(os.getenv("STOP_CAPTURE_THRESHOLD", "0.5"))

# ── Endpointing par LOCUTEUR CIBLE (robuste en milieu bruyant) ───────
# ON : marche bien sur une commande medium/longue (assez d'audio pour identifier
# l'utilisateur). Seul le wake word (audio court 1,5s) était instable → lui seul
# est désactivé (WAKE_SPEAKER_GATE). TARGET_ENDPOINTING=0 pour repasser énergie/VAD.
TARGET_ENDPOINTING = os.getenv("TARGET_ENDPOINTING", "0") == "1"
# L'enceinte s'arrête quand TA voix s'arrête, en ignorant les autres voix.
TARGET_WINDOW_S = 1.5          # fenêtre glissante pour décider "c'est lui ?"
TARGET_HOP_S = 0.4            # cadence de décision (toutes les 0.4 s)
TARGET_HANG_S = float(os.getenv("TARGET_HANG_S", "2.0"))   # absence de TA voix pour clore (tolère les pauses de réflexion)
TARGET_MISS_HYSTERESIS = 2    # fenêtres "pas lui" consécutives avant de compter l'absence
TARGET_WAIT_START_S = 4.0     # si TA voix n'apparaît jamais après le wake word → abandon
# Une fois la commande DÉMARRÉE, on garde tant qu'il y a de la PAROLE, et on finit
# sur le SILENCE — on ne coupe PAS sur le score locuteur (qui fluctue, surtout pour
# une 2e voix enrôlée plus faible → coupait au milieu, "trop restrictif"). Défaut
# -1.0 = ne jamais couper sur le score. Mets-le à 0.0+ pour filtrer les autres voix.
TARGET_KEEP_THRESHOLD = float(os.getenv("TARGET_KEEP_THRESHOLD", "-1.0"))

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
MAX_WASTED = int(os.getenv("MAX_WASTED", "3"))        # cycles sans réponse → IDLE
MAX_CONV_TURNS = int(os.getenv("MAX_CONV_TURNS", "8"))  # tours max en conversation → IDLE

# ── Conversation continue (parité web) ───────────────────────────────
CONVERSATION_WINDOW_S = float(os.getenv("CONVERSATION_WINDOW_S", "8.0"))   # fenêtre suivi sans wake word
CONVERSING_RMS = float(os.getenv("CONVERSING_RMS", "350"))   # seuil parole en conversing
SPEAKING_RMS = float(os.getenv("SPEAKING_RMS", "600"))       # seuil barge-in pendant TTS (> écho)
FOLLOWUP_SPEECH_FRAMES = 3     # frames consécutives pour déclencher un follow-up/barge-in
# Barge-in pendant que Aura PARLE : nb de hops (×0.4s) de TA voix avant de basculer
# en écoute. Volontairement HAUT (1.6s) pour laisser « Stop Aura » (~1-1.2s) GAGNER
# la course — sinon dire « Stop Aura » est pris pour un barge-in et lance l'écoute.
BARGE_STREAK = int(os.getenv("BARGE_STREAK", "4"))
# 0 = désactive le barge-in vocal pendant le TTS → SEUL « Stop Aura » interrompt.
BARGE_IN_ENABLED = os.getenv("BARGE_IN_ENABLED", "1") == "1"

# ── Contexte ambiant (STT passif) ────────────────────────────────────
AMBIENT_ENABLED = os.getenv("AMBIENT_ENABLED", "1") == "1"
AMBIENT_BATCH_S = 15.0         # envoi d'un batch ambiant toutes les 15 s
AMBIENT_PREFIX = "[Conversation ambiante]: "
MAX_CONTEXT_SEGMENTS = 50      # taille max du buffer de contexte
MAX_CONTEXT_AGE_S = 30 * 60    # 30 min
