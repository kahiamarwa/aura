"""Enrôlement vocal DEPUIS l'enceinte (le bon micro → score fiable).

Le WEB demande l'enrôlement (nom) → on l'écrit dans device_status.enroll_request.
Le DEVICE le lit (poll /api/device/control), guide la capture avec SON micro (LED + voix),
puis POST l'audio à /api/device/enroll. ECAPA tourne CÔTÉ SERVEUR (aucune clé sur l'appareil).
But : enrôler avec le MÊME chemin audio que la vérif → cosine fiable (vs micro navigateur).
"""
import asyncio
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import Response

from app.config import get_settings
from app.routes.device_pairing import resolve_user_token
from app.routes.gemini_stt import pcm_to_wav
from app.services.speaker_service import SpeakerService
from app.services.supabase_client import get_supabase_client, get_user_id, get_service_client
from app.services.tts_service import stream_tts

logger = logging.getLogger(__name__)
router = APIRouter()

# Prompts vocaux servis À L'ENCEINTE (TTS + cache serveur → aucune clé sur l'appareil).
# Le device les récupère une fois puis les garde en cache local.
_PROMPT_TEXTS = {
    "enroll_intro": "Je vais apprendre ta voix. Quand la lumière devient verte, lis à voix "
                    "haute le texte affiché sur l'écran, naturellement.",
    "enroll_speak": "Parle maintenant.",
    "enroll_continue": "Continue, je t'écoute.",
    "enroll_almost": "Encore quelques secondes.",
    "enroll_done": "C'est bon. Je reconnais ta voix maintenant.",
    "enroll_fail": "Je n'ai pas bien entendu. On réessaiera plus tard.",
}
_PROMPT_CACHE = Path("/tmp/aura_enroll_prompts")


@router.get("/api/device/enroll-prompt/{name}")
async def enroll_prompt(name: str, raw_request: Request):
    """Sert un prompt vocal MP3 à l'enceinte (TTS ElevenLabs, mis en cache côté serveur).
    Auth = DEVICE_TOKEN. Permet la VOIX sans embarquer de clé ni distribuer des fichiers."""
    if not resolve_user_token(raw_request):
        raise HTTPException(status_code=401, detail="device auth required")
    text = _PROMPT_TEXTS.get(name)
    if not text:
        raise HTTPException(status_code=404, detail="unknown prompt")
    _PROMPT_CACHE.mkdir(parents=True, exist_ok=True)
    cached = _PROMPT_CACHE / f"{name}.mp3"
    if not cached.exists():
        settings = get_settings()
        if not settings.ELEVENLABS_API_KEY or not settings.ELEVENLABS_VOICE_ID:
            raise HTTPException(status_code=503, detail="tts not configured")
        data = b""
        async for chunk in stream_tts(text=text, voice_id=settings.ELEVENLABS_VOICE_ID,
                                      api_key=settings.ELEVENLABS_API_KEY):
            data += chunk
        cached.write_bytes(data)
    return Response(content=cached.read_bytes(), media_type="audio/mpeg")


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
        # ECAPA = CPU pur → dans un thread pour NE PAS bloquer l'event loop (sinon les polls
        # control/state du device se bloquent et le device coupe à son timeout).
        embedding, ref_audio = await asyncio.to_thread(service.enroll_from_wav_bytes, wav)
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
