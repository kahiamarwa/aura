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


def push_state(state: str, transcript: str = "") -> None:
    """Pousse l'état courant du device vers le cloud (pour l'affichage live web).

    Fire-and-forget : on n'attend pas, on ne bloque jamais l'orchestrateur.
    """
    if not config.USER_TOKEN:
        return
    try:
        with httpx.Client(timeout=httpx.Timeout(3.0)) as client:
            client.post(_url("/api/device/state"), headers=_headers(),
                        data={"state": state, "transcript": transcript})
    except Exception:
        pass


def converse(command_pcm: np.ndarray, from_conversing: bool, context: list[str],
             tentative: bool = False) -> dict:
    """Envoie la commande au cloud (pipeline gated complet).

    tentative=True : le device a détecté une pause mais n'est pas sûr que la
    phrase soit finie → le cloud peut répondre {status: incomplete} pour qu'on
    continue d'écouter (endpointing sémantique).

    Retourne :
      {"kind": "audio", "chunks": <générateur de bytes MP3>, "close": fn,
       "transcript": str, "response": str, "speaker": str}
      {"kind": "status", "status": "empty"|"not_directed"|"rejected"|"incomplete"|..., ...}

    Pour "audio", on STREAME le MP3 : Aura commence à parler dès le 1er chunk.
    L'appelant DOIT consommer "chunks" puis appeler "close()" (ferme la connexion).
    """
    import json
    wav = pcm_to_wav_bytes(command_pcm)
    client = httpx.Client(timeout=httpx.Timeout(60.0))
    cm = client.stream(
        "POST",
        _url("/api/device/converse"),
        headers=_headers(),
        data={"from_conversing": "true" if from_conversing else "false",
              "context": json.dumps(context),
              "tentative": "true" if tentative else "false"},
        files={"audio": ("command.wav", wav, "audio/wav")},
    )
    resp = cm.__enter__()
    try:
        resp.raise_for_status()
        ctype = resp.headers.get("content-type", "")
        if not ctype.startswith("audio/"):
            data = json.loads(resp.read() or b"{}")
            cm.__exit__(None, None, None)
            client.close()
            return {"kind": "status", **data}

        def _close():
            try:
                cm.__exit__(None, None, None)
            finally:
                client.close()

        return {
            "kind": "audio",
            "chunks": resp.iter_bytes(),
            "close": _close,
            "transcript": unquote(resp.headers.get("X-Transcript", "")),
            "response": unquote(resp.headers.get("X-Response", "")),
            "speaker": unquote(resp.headers.get("X-Speaker", "")),
        }
    except Exception:
        try:
            cm.__exit__(None, None, None)
        finally:
            client.close()
        raise


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
