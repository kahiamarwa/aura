"""Client cloud du device.

Le device ne détient aucune clé tierce : il envoie l'audio au cloud, qui fait
STT + intent + speaker verification + LLM + TTS, et renvoie soit l'audio MP3,
soit un statut (rejeté / pas pour Aura).
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


def _url(path: str) -> str:
    return config.CLOUD_BACKEND_URL.rstrip("/") + path


def converse(command_pcm: np.ndarray, from_conversing: bool, context: list[str],
             tentative: bool = False) -> dict:
    """Envoie la commande au cloud (pipeline gated complet).

    tentative=True : le device a détecté une pause mais n'est pas sûr que la
    phrase soit finie → le cloud peut répondre {status: incomplete} pour qu'on
    continue d'écouter (endpointing sémantique).

    Retourne :
      {"kind": "audio", "mp3": bytes, "transcript": str, "response": str, "speaker": str}
      {"kind": "status", "status": "empty"|"not_directed"|"rejected"|"incomplete"|..., ...}
    """
    import json
    wav = pcm_to_wav_bytes(command_pcm)
    with httpx.Client(timeout=httpx.Timeout(60.0)) as client:
        resp = client.post(
            _url("/api/device/converse"),
            headers=_headers(),
            data={"from_conversing": "true" if from_conversing else "false",
                  "context": json.dumps(context),
                  "tentative": "true" if tentative else "false"},
            files={"audio": ("command.wav", wav, "audio/wav")},
        )
        resp.raise_for_status()
        ctype = resp.headers.get("content-type", "")
        if ctype.startswith("audio/"):
            return {
                "kind": "audio",
                "mp3": resp.content,
                "transcript": unquote(resp.headers.get("X-Transcript", "")),
                "response": unquote(resp.headers.get("X-Response", "")),
                "speaker": unquote(resp.headers.get("X-Speaker", "")),
            }
        data = resp.json()
        return {"kind": "status", **data}


def transcribe(pcm: np.ndarray) -> str:
    """Transcription simple (contexte ambiant). Retourne le texte (ou '')."""
    wav = pcm_to_wav_bytes(pcm)
    with httpx.Client(timeout=httpx.Timeout(40.0)) as client:
        resp = client.post(
            _url("/api/device/transcribe"),
            headers=_headers(),
            files={"audio": ("ambient.wav", wav, "audio/wav")},
        )
        resp.raise_for_status()
        return (resp.json().get("text") or "").strip()


def fetch_user_embeddings() -> list:
    """Récupère les empreintes vocales enrôlées (pour l'endpointing local).

    Retourne une liste de np.ndarray (192-dim, L2-normalisés).
    """
    import io
    import base64
    with httpx.Client(timeout=httpx.Timeout(15.0)) as client:
        resp = client.get(_url("/api/device/speakers/embeddings"), headers=_headers())
        resp.raise_for_status()
        out = []
        for e in resp.json().get("embeddings", []):
            buf = io.BytesIO(base64.b64decode(e["embedding_b64"]))
            emb = np.load(buf).astype(np.float32)
            n = np.linalg.norm(emb)
            out.append(emb / n if n > 0 else emb)
        return out
