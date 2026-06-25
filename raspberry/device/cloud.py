"""Client cloud du device.

Le device ne détient aucune clé tierce : il envoie l'audio au cloud, qui fait
STT + intent + speaker verification + LLM + TTS, et renvoie soit l'audio MP3,
soit un statut (rejeté / pas pour Aura).
"""

import io
import os
import json
import time
import wave
import logging
import threading
from urllib.parse import unquote

import httpx
import numpy as np

from . import config

logger = logging.getLogger(__name__)


# ── Auth durable : JWT renouvelé automatiquement via refresh token ──────
_tok_lock = threading.Lock()
_access_token = config.USER_TOKEN or None
_access_exp = 0.0
_refresh_token = config.REFRESH_TOKEN or None


def _jwt_exp(token: str) -> float:
    """Lit le champ exp d'un JWT (sans vérif signature) pour connaître sa validité."""
    try:
        import base64
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload)).get("exp", 0))
    except Exception:
        return 0.0


def _load_session():
    """Charge le dernier JWT/refresh persisté (survit aux redémarrages)."""
    global _access_token, _refresh_token, _access_exp
    try:
        if os.path.exists(config.TOKEN_FILE):
            with open(config.TOKEN_FILE) as f:
                d = json.load(f)
            # env (AURA_REFRESH_TOKEN) a priorité ; sinon le token rotaté du fichier
            _refresh_token = _refresh_token or d.get("refresh_token")
            _access_token = _access_token or d.get("access_token")
    except Exception:
        pass
    # On connaît l'expiration du JWT en cache → on NE refresh PAS tant qu'il est valide
    if _access_token:
        exp = _jwt_exp(_access_token)
        if exp:
            _access_exp = exp - 120.0


def _save_session():
    try:
        os.makedirs(os.path.dirname(config.TOKEN_FILE), exist_ok=True)
        with open(config.TOKEN_FILE, "w") as f:
            json.dump({"access_token": _access_token, "refresh_token": _refresh_token}, f)
    except Exception as e:
        logger.debug("[auth] persistance session impossible: %s", e)


def _refresh_access() -> str | None:
    """Échange le refresh token contre un JWT frais (Supabase). Persiste le tout."""
    global _access_token, _refresh_token, _access_exp
    if not _refresh_token or not config.SUPABASE_URL or not config.SUPABASE_ANON_KEY:
        return _access_token
    r = httpx.post(
        config.SUPABASE_URL.rstrip("/") + "/auth/v1/token",
        params={"grant_type": "refresh_token"},
        headers={"apikey": config.SUPABASE_ANON_KEY, "Content-Type": "application/json"},
        json={"refresh_token": _refresh_token},
        timeout=10.0,
    )
    r.raise_for_status()
    d = r.json()
    _access_token = d["access_token"]
    _refresh_token = d.get("refresh_token", _refresh_token)   # rotation
    _access_exp = time.time() + int(d.get("expires_in", 3600)) - 120
    _save_session()
    logger.info("[auth] JWT renouvelé automatiquement (expire dans %ss)", d.get("expires_in"))
    return _access_token


def get_access_token() -> str | None:
    """JWT courant, renouvelé tout seul si expiré. Plus jamais d'export manuel.

    Avec l'appairage, le device n'a PAS besoin de token utilisateur (le backend
    résout via DEVICE_TOKEN). On abandonne donc un refresh token révoqué.
    """
    global _access_exp, _refresh_token
    with _tok_lock:
        if _access_token and time.time() < _access_exp:
            return _access_token       # cache valide → AUCUN appel réseau
        if _refresh_token:
            try:
                return _refresh_access()
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (400, 401, 403):
                    logger.warning("[auth] refresh token révoqué → abandon (le device s'authentifie par son DEVICE_TOKEN)")
                    _refresh_token = None          # on ne retente plus
                _access_exp = time.time() + 300.0
            except Exception as e:
                logger.warning("[auth] échec du refresh: %s", e)
                _access_exp = time.time() + 60.0
        return _access_token


_load_session()


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
    tok = get_access_token()
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def _url(path: str) -> str:
    return config.CLOUD_BACKEND_URL.rstrip("/") + path


# Client persistant (keep-alive) pour les pushs d'état — évite un handshake TLS
# à chaque transition, timeout court pour ne jamais retarder le pipeline.
_state_client = httpx.Client(timeout=httpx.Timeout(connect=2.0, read=2.0, write=2.0, pool=2.0))


def push_state(state: str, transcript: str = "", seq: int = 0) -> None:
    """Pousse l'état courant du device vers le cloud (affichage live front).

    Appelé par UN SEUL thread (sérialisé) → ordre garanti. seq monotone permet
    au backend de rejeter un état arrivé en retard (anti-désordre).
    """
    if not (config.DEVICE_TOKEN or get_access_token()):
        return
    try:
        _state_client.post(_url("/api/device/state"), headers=_headers(),
                           data={"state": state, "transcript": transcript, "seq": str(seq)})
    except Exception:
        pass


def get_mute_state() -> bool:
    """Lit le flag mute distant (mode confidentiel), poussé par le web. Poll léger.

    True → le device doit COUPER micro + ambiant (rien n'est envoyé au cloud).
    Réseau injoignable → False (on ne bloque pas le device sur une erreur réseau).
    """
    return bool(get_control().get("muted"))


def get_control() -> dict:
    """Lit le contrôle distant (mute + demande d'enrôlement) en UN poll.
    Renvoie {"muted": bool, "enroll_request": dict|None}. Réseau KO → tout neutre."""
    neutral = {"muted": False, "enroll_request": None}
    if not (config.DEVICE_TOKEN or get_access_token()):
        return neutral
    try:
        r = _state_client.get(_url("/api/device/control"), headers=_headers())
        if r.status_code == 200:
            d = r.json()
            return {"muted": bool(d.get("muted")), "enroll_request": d.get("enroll_request")}
    except Exception:
        pass
    return neutral


def enroll(name: str, wav_bytes: bytes) -> dict:
    """Envoie l'audio capté par l'enceinte → empreinte vocale (ECAPA côté serveur).
    Renvoie {"ok": bool, "status": "ok"|"not_enough_speech"|"error", ...}."""
    if not (config.DEVICE_TOKEN or get_access_token()):
        return {"ok": False, "status": "error"}
    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.post(_url("/api/device/enroll"), headers=_headers(),
                            data={"name": name},
                            files={"audio": ("enroll.wav", wav_bytes, "audio/wav")})
        if r.status_code == 200:
            return {"ok": True, "status": "ok", **r.json()}
        if r.status_code == 422:
            return {"ok": False, "status": "not_enough_speech"}
        logger.warning("[enroll] HTTP %d", r.status_code)
        return {"ok": False, "status": "error"}
    except Exception as e:
        logger.warning("[enroll] erreur réseau: %s", e)
        return {"ok": False, "status": "error"}


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
    # Timeouts granulaires. read=180s : générer un rapport/présentation peut
    # prendre 30s+ (l'agent ne renvoie l'audio qu'à la fin) → ne PAS couper avant.
    # Le front montre la progression (animation d'outil) pendant ce temps.
    client = httpx.Client(timeout=httpx.Timeout(connect=5.0, read=180.0, write=10.0, pool=5.0))
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
        # Erreur HTTP (502 proxy, 500…) : lire le corps PUIS fermer, et renvoyer un
        # statut d'erreur propre (PAS de raise → évite le crash ResponseNotRead).
        if resp.status_code >= 400:
            body = ""
            try:
                body = (resp.read() or b"")[:200].decode("utf-8", "replace")
            except Exception:
                pass
            cm.__exit__(None, None, None)
            client.close()
            logger.error("[cloud] converse HTTP %s: %s", resp.status_code, body)
            return {"kind": "status", "status": "error", "code": resp.status_code}
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
