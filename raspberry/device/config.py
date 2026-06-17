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
USER_TOKEN = os.getenv("USER_TOKEN", "")  # JWT de l'utilisateur appairé

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
    Path(ACTIVATE_MODEL).stem: float(os.getenv("ACTIVATE_THRESHOLD", "0.5")),
    Path(INTERRUPT_MODEL).stem: float(os.getenv("INTERRUPT_THRESHOLD", "0.7")),
}
WAKE_COOLDOWN_S = 1.5

# ── Capture de la commande (VAD énergie simple) ──────────────────────
CMD_SILENCE_RMS = float(os.getenv("CMD_SILENCE_RMS", "300"))   # seuil silence (int16)
CMD_SILENCE_HANG_S = 1.0      # silence consécutif pour clore la commande
CMD_MAX_S = 12.0              # durée max d'une commande
CMD_MIN_SPEECH_S = 0.3        # parole min pour considérer une vraie commande
