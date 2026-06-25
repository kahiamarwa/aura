"""Enrôlement vocal DEPUIS l'enceinte (le bon micro → score fiable).

Le WEB demande l'enrôlement (nom) → on l'écrit dans device_status.enroll_request.
Le DEVICE le lit (poll /api/device/control), guide la capture avec SON micro (LED + voix),
puis POST l'audio à /api/device/enroll. ECAPA tourne CÔTÉ SERVEUR (aucune clé sur l'appareil).
But : enrôler avec le MÊME chemin audio que la vérif → cosine fiable (vs micro navigateur).
"""
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form

from app.routes.device_pairing import resolve_user_token
from app.routes.gemini_stt import pcm_to_wav
from app.services.speaker_service import SpeakerService
from app.services.supabase_client import get_supabase_client, get_user_id, get_service_client

logger = logging.getLogger(__name__)
router = APIRouter()


def _clear_enroll_request(user_id: str):
    try:
        get_service_client().table("device_status").update(
            {"enroll_request": None}).eq("user_id", user_id).execute()
    except Exception as e:
        logger.debug("[enroll] clear request error: %s", e)


@router.post("/api/web/device-enroll")
async def request_device_enroll(raw_request: Request):
    """Le WEB demande un enrôlement depuis l'enceinte. Auth = JWT user. Body {name}.
    Écrit enroll_request dans device_status ; le device le récupère au prochain poll."""
    auth = raw_request.headers.get("authorization", "")
    user_token = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else None
    if not user_token:
        raise HTTPException(status_code=401, detail="login required")
    body = await raw_request.json()
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    try:
        supabase = get_supabase_client(user_token)
        user_id = get_user_id(supabase, user_token)
        req = {"id": uuid.uuid4().hex, "name": name[:40],
               "requested_at": datetime.now(timezone.utc).isoformat()}
        supabase.table("device_status").upsert({
            "user_id": user_id,
            "enroll_request": req,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
        logger.info("[enroll] demande web → enceinte : %s", name)
        return {"ok": True, "request": req}
    except Exception as e:
        logger.warning("[enroll] request error: %s", e)
        raise HTTPException(status_code=500, detail="enroll request failed")


@router.post("/api/web/device-enroll/cancel")
async def cancel_device_enroll(raw_request: Request):
    """Annule une demande d'enrôlement en attente (efface enroll_request). Auth = JWT user."""
    auth = raw_request.headers.get("authorization", "")
    user_token = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else None
    if not user_token:
        raise HTTPException(status_code=401, detail="login required")
    try:
        supabase = get_supabase_client(user_token)
        user_id = get_user_id(supabase, user_token)
        _clear_enroll_request(user_id)
        return {"ok": True}
    except Exception as e:
        logger.warning("[enroll] cancel error: %s", e)
        raise HTTPException(status_code=500, detail="cancel failed")


@router.post("/api/device/enroll")
async def device_enroll(
    raw_request: Request,
    audio: UploadFile = File(...),
    name: str = Form(...),
):
    """L'ENCEINTE envoie l'audio capté (PCM16 16k ou WAV) pour créer/MAJ l'empreinte.
    Auth = DEVICE_TOKEN (appairage). ECAPA côté serveur. Efface la demande à la fin.
    422 'not_enough_speech' si pas assez de parole (le device rejoue le prompt d'échec)."""
    user_token = resolve_user_token(raw_request)
    if not user_token:
        raise HTTPException(status_code=401, detail="device auth required")
    name = (name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    raw = await audio.read()
    if len(raw) < 16000:                      # < ~0.5s → trop court
        raise HTTPException(status_code=400, detail="audio too small")
    wav = raw if raw[:4] == b"RIFF" else pcm_to_wav(raw, sample_rate=16000)
    try:
        supabase = get_supabase_client(user_token)
        user_id = get_user_id(supabase, user_token)
        service = SpeakerService.get_instance()
        embedding, ref_audio = service.enroll_from_wav_bytes(wav)   # ValueError si pas de parole
        embedding_b64 = service.embedding_to_base64(embedding)

        existing = (supabase.table("speaker_enrollments").select("id")
                    .eq("user_id", user_id).eq("speaker_name", name).execute())
        now = datetime.now(timezone.utc).isoformat()
        if existing.data:
            supabase.table("speaker_enrollments").update(
                {"embedding": embedding_b64, "updated_at": now}
            ).eq("id", existing.data[0]["id"]).execute()
        else:
            supabase.table("speaker_enrollments").insert(
                {"user_id": user_id, "speaker_name": name, "embedding": embedding_b64}
            ).execute()

        _clear_enroll_request(user_id)        # demande consommée
        dur = round(len(ref_audio) / 16000, 1)
        logger.info("[enroll] enceinte → '%s' enrôlé (%.1fs réf, %d-dim)", name, dur, len(embedding))
        return {"ok": True, "speaker_name": name, "reference_duration_s": dur}
    except ValueError as e:
        logger.info("[enroll] échec : %s", e)
        raise HTTPException(status_code=422, detail="not_enough_speech")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[enroll] device enroll error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="enroll failed")
