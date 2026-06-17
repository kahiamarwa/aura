"""Speaker enrollment & verification — DEVICE side.

SÉCURITÉ : le device ne contient AUCUNE clé Supabase ni clé tierce.
Il calcule l'embedding vocal LOCALEMENT (ONNX ECAPA, modèle public, pas de secret)
puis envoie uniquement le vecteur 192-dim au backend cloud, qui détient les secrets
et fait le stockage / la comparaison contre la base de données.

L'audio brut ne quitte pas le device pour la vérification : seul l'embedding part.
Le device s'authentifie auprès du cloud avec son DEVICE_TOKEN (header X-Device-Token)
et transmet le JWT utilisateur (header Authorization) reçu de l'app.
"""

import base64
import logging

import numpy as np
import httpx
from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form

from app.config import get_settings
from app.services.speaker_service import SpeakerService

logger = logging.getLogger(__name__)
router = APIRouter()

_TIMEOUT = httpx.Timeout(30.0)


def _auth_headers(request: Request) -> dict:
    """Headers d'auth vers le cloud : JWT user (passthrough) + device token.

    Le JWT user n'est pas un secret baké : il provient de la session/appairage.
    Le DEVICE_TOKEN identifie le device auprès du cloud (révocable).
    """
    settings = get_settings()
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authorization required")
    headers = {"Authorization": auth_header}
    if settings.DEVICE_TOKEN:
        headers["X-Device-Token"] = settings.DEVICE_TOKEN
    return headers


def _cloud_url(path: str) -> str:
    base = get_settings().CLOUD_BACKEND_URL.rstrip("/")
    return f"{base}{path}"


@router.get("/api/speakers")
async def list_speakers(raw_request: Request):
    """Liste les voix enrôlées (proxy vers le cloud)."""
    headers = _auth_headers(raw_request)
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(_cloud_url("/api/device/speakers"), headers=headers)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=e.response.text[:300])
    except Exception as e:
        logger.error("Error listing speakers via cloud: %s", e)
        raise HTTPException(status_code=502, detail="Cloud backend unreachable")


@router.post("/api/speakers/enroll")
async def enroll_speaker(
    raw_request: Request,
    audio: UploadFile = File(...),
    speaker_name: str = Form(...),
):
    """Enrôle une voix.

    L'embedding est calculé LOCALEMENT sur le device (ONNX), puis envoyé au cloud
    pour stockage. Aucune clé Supabase sur le device.
    """
    headers = _auth_headers(raw_request)
    try:
        wav_bytes = await audio.read()
        if len(wav_bytes) < 1000:
            raise HTTPException(status_code=400, detail="Audio file too small")

        # ── Calcul de l'embedding EN LOCAL (aucun secret requis) ──
        service = SpeakerService.get_instance()
        embedding, ref_audio = service.enroll_from_wav_bytes(wav_bytes)
        embedding_b64 = service.embedding_to_base64(embedding)
        ref_wav_bytes = service.audio_to_wav_bytes(ref_audio)
        ref_audio_b64 = base64.b64encode(ref_wav_bytes).decode("ascii")

        # ── Envoi du vecteur (pas l'audio brut) au cloud pour stockage ──
        payload = {
            "speaker_name": speaker_name,
            "embedding_b64": embedding_b64,
            "reference_audio_b64": ref_audio_b64,
            "embedding_dims": len(embedding),
            "reference_duration_s": round(len(ref_audio) / 16000, 1),
        }
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                _cloud_url("/api/device/speakers/enroll"), headers=headers, json=payload
            )
        resp.raise_for_status()
        return resp.json()

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=e.response.text[:300])
    except Exception as e:
        logger.error("Error enrolling speaker: %s", e, exc_info=True)
        raise HTTPException(status_code=502, detail="Cloud backend unreachable")


@router.delete("/api/speakers/{enrollment_id}")
async def delete_speaker(enrollment_id: str, raw_request: Request):
    """Supprime une voix enrôlée (proxy vers le cloud)."""
    headers = _auth_headers(raw_request)
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.delete(
                _cloud_url(f"/api/device/speakers/{enrollment_id}"), headers=headers
            )
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=e.response.text[:300])
    except Exception as e:
        logger.error("Error deleting speaker via cloud: %s", e)
        raise HTTPException(status_code=502, detail="Cloud backend unreachable")


@router.post("/api/speakers/verify")
async def verify_speaker(raw_request: Request):
    """Vérifie le locuteur.

    Body JSON: { "audio_base64": "<PCM int16 base64>", "sample_rate": 16000 }

    L'embedding de la commande est calculé EN LOCAL, puis comparé côté cloud
    contre les voix enrôlées de l'utilisateur. L'audio brut ne part pas.
    """
    headers = _auth_headers(raw_request)
    try:
        body = await raw_request.json()
        audio_b64 = body.get("audio_base64")
        if not audio_b64:
            raise HTTPException(status_code=400, detail="audio_base64 required")

        service = SpeakerService.get_instance()
        pcm_bytes = base64.b64decode(audio_b64)
        audio = service.pcm_int16_to_float32(pcm_bytes)

        # Resample vers 16kHz si besoin (avant calcul de l'embedding)
        sample_rate = body.get("sample_rate", 16000)
        if sample_rate != 16000:
            duration = len(audio) / sample_rate
            target_len = int(duration * 16000)
            indices = np.linspace(0, len(audio) - 1, target_len)
            audio = np.interp(indices, np.arange(len(audio)), audio).astype(np.float32)

        # ── Calcul de l'embedding de la commande EN LOCAL ──
        processed = service.preprocess_audio(audio)
        if not service.has_speech(processed):
            return {
                "verified": False,
                "speaker_name": None,
                "score": 0.0,
                "threshold": 0.40,
                "reason": "no_speech",
            }
        query_emb = service.get_embedding(processed)
        query_emb_b64 = service.embedding_to_base64(query_emb)

        # ── Comparaison côté cloud (qui détient les embeddings enrôlés) ──
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                _cloud_url("/api/device/speakers/verify"),
                headers=headers,
                json={"embedding_b64": query_emb_b64},
            )
        resp.raise_for_status()
        return resp.json()

    except HTTPException:
        raise
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=e.response.text[:300])
    except Exception as e:
        logger.error("Error verifying speaker: %s", e, exc_info=True)
        raise HTTPException(status_code=502, detail="Cloud backend unreachable")
