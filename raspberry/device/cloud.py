"""Client cloud du device : envoie l'audio commande, reçoit le MP3 TTS.

Le device ne détient aucune clé tierce : il s'authentifie au cloud avec
son DEVICE_TOKEN + le JWT de l'utilisateur appairé, et le cloud fait
STT + LLM + TTS.
"""

import io
import wave
import logging
from urllib.parse import unquote

import httpx
import numpy as np

from . import config

logger = logging.getLogger(__name__)


def pcm_to_wav_bytes(pcm: np.ndarray, sample_rate: int = config.SAMPLE_RATE) -> bytes:
    """int16 mono -> conteneur WAV."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.astype(np.int16).tobytes())
    return buf.getvalue()


def _headers() -> dict:
    h = {}
    if config.DEVICE_TOKEN:
        h["X-Device-Token"] = config.DEVICE_TOKEN
    if config.USER_TOKEN:
        h["Authorization"] = f"Bearer {config.USER_TOKEN}"
    return h


def converse(command_pcm: np.ndarray) -> tuple[bytes, str, str]:
    """Envoie l'audio commande au cloud. Retourne (mp3, transcript, réponse).

    Lève httpx.HTTPStatusError en cas d'erreur (ex: 422 si transcription vide).
    """
    wav = pcm_to_wav_bytes(command_pcm)
    url = config.CLOUD_BACKEND_URL.rstrip("/") + "/api/device/converse"
    with httpx.Client(timeout=httpx.Timeout(60.0)) as client:
        resp = client.post(
            url,
            headers=_headers(),
            files={"audio": ("command.wav", wav, "audio/wav")},
        )
        resp.raise_for_status()
        transcript = unquote(resp.headers.get("X-Transcript", ""))
        response_text = unquote(resp.headers.get("X-Response", ""))
        return resp.content, transcript, response_text
