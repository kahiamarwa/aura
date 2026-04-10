"""Speaker enrollment & verification endpoints."""

import base64
import logging
import uuid
from datetime import datetime, timezone

import numpy as np

from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form

from app.services.supabase_client import get_supabase_client, get_user_id
from app.services.speaker_service import SpeakerService

logger = logging.getLogger(__name__)
router = APIRouter()


def _extract_token(request: Request) -> str:
    auth_header = request.headers.get("authorization", "")
    user_token = auth_header.removeprefix("Bearer ").strip() if auth_header.startswith("Bearer ") else None
    if not user_token:
        raise HTTPException(status_code=401, detail="Authorization required")
    return user_token


@router.get("/api/speakers")
async def list_speakers(raw_request: Request):
    """List all enrolled speakers for the authenticated user."""
    user_token = _extract_token(raw_request)
    try:
        supabase = get_supabase_client(user_token)
        user_id = get_user_id(supabase, user_token)
        response = (
            supabase.table("speaker_enrollments")
            .select("id, speaker_name, created_at, updated_at")
            .eq("user_id", user_id)
            .order("created_at")
            .execute()
        )
        return {"speakers": response.data or []}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error listing speakers: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/speakers/enroll")
async def enroll_speaker(
    raw_request: Request,
    audio: UploadFile = File(...),
    speaker_name: str = Form(...),
):
    """Enroll a speaker from an audio file (WAV, 16kHz mono).

    The audio should be at least 10 seconds of speech.
    Multiple samples concatenated into one file is ideal.
    """
    user_token = _extract_token(raw_request)
    try:
        supabase = get_supabase_client(user_token)
        user_id = get_user_id(supabase, user_token)

        # Read audio file
        wav_bytes = await audio.read()
        if len(wav_bytes) < 1000:
            raise HTTPException(status_code=400, detail="Audio file too small")

        # Get speaker service and create enrollment
        service = SpeakerService.get_instance()
        embedding, ref_audio = service.enroll_from_wav_bytes(wav_bytes)

        # Encode embedding as base64 for DB storage
        embedding_b64 = service.embedding_to_base64(embedding)

        # Upload reference audio to Supabase Storage
        ref_wav_bytes = service.audio_to_wav_bytes(ref_audio)
        storage_path = f"{user_id}/{speaker_name}_{uuid.uuid4().hex[:8]}.wav"

        supabase.storage.from_("speaker-audio").upload(
            path=storage_path,
            file=ref_wav_bytes,
            file_options={"content-type": "audio/wav"},
        )

        # Upsert enrollment in database
        # Check if speaker already exists
        existing = (
            supabase.table("speaker_enrollments")
            .select("id")
            .eq("user_id", user_id)
            .eq("speaker_name", speaker_name)
            .execute()
        )

        now = datetime.now(timezone.utc).isoformat()
        if existing.data:
            # Update existing enrollment
            supabase.table("speaker_enrollments").update({
                "embedding": embedding_b64,
                "reference_audio_path": storage_path,
                "updated_at": now,
            }).eq("id", existing.data[0]["id"]).execute()
            enrollment_id = existing.data[0]["id"]
        else:
            # Insert new
            resp = supabase.table("speaker_enrollments").insert({
                "user_id": user_id,
                "speaker_name": speaker_name,
                "embedding": embedding_b64,
                "reference_audio_path": storage_path,
            }).execute()
            enrollment_id = resp.data[0]["id"] if resp.data else None

        return {
            "success": True,
            "enrollment_id": enrollment_id,
            "speaker_name": speaker_name,
            "embedding_dims": len(embedding),
            "reference_duration_s": round(len(ref_audio) / 16000, 1),
        }

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Error enrolling speaker: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/speakers/{enrollment_id}")
async def delete_speaker(enrollment_id: str, raw_request: Request):
    """Delete an enrolled speaker."""
    user_token = _extract_token(raw_request)
    try:
        supabase = get_supabase_client(user_token)
        user_id = get_user_id(supabase, user_token)

        # Get enrollment to find storage path
        resp = (
            supabase.table("speaker_enrollments")
            .select("*")
            .eq("id", enrollment_id)
            .eq("user_id", user_id)
            .execute()
        )
        if not resp.data:
            raise HTTPException(status_code=404, detail="Speaker not found")

        enrollment = resp.data[0]

        # Delete storage file if exists
        if enrollment.get("reference_audio_path"):
            try:
                supabase.storage.from_("speaker-audio").remove(
                    [enrollment["reference_audio_path"]]
                )
            except Exception:
                pass  # Storage cleanup is best-effort

        # Delete DB row
        supabase.table("speaker_enrollments").delete().eq("id", enrollment_id).execute()

        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error deleting speaker: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/speakers/verify")
async def verify_speaker(raw_request: Request):
    """Verify a speaker from audio data.

    Expects JSON body:
    {
        "audio_base64": "<base64 encoded PCM int16 audio at 16kHz>",
        "sample_rate": 16000
    }

    Returns:
    {
        "verified": bool,
        "speaker_name": str | null,
        "score": float,
        "threshold": float
    }
    """
    user_token = _extract_token(raw_request)
    try:
        body = await raw_request.json()
        audio_b64 = body.get("audio_base64")
        if not audio_b64:
            raise HTTPException(status_code=400, detail="audio_base64 required")

        supabase = get_supabase_client(user_token)
        user_id = get_user_id(supabase, user_token)

        # Load all enrolled speakers for this user
        enrollments = (
            supabase.table("speaker_enrollments")
            .select("*")
            .eq("user_id", user_id)
            .execute()
        )

        if not enrollments.data:
            # No speakers enrolled — skip verification, allow through
            return {
                "verified": True,
                "speaker_name": None,
                "score": 1.0,
                "threshold": SpeakerService.get_instance() and 0.40,
                "reason": "no_enrollments",
            }

        service = SpeakerService.get_instance()

        # Decode audio
        pcm_bytes = base64.b64decode(audio_b64)
        audio = service.pcm_int16_to_float32(pcm_bytes)

        logger.info(
            "[SpeakerVerify] input audio: len=%d, rms=%.4f, min=%.4f, max=%.4f",
            len(audio), float(np.sqrt(np.mean(audio**2))),
            float(np.min(audio)), float(np.max(audio)),
        )

        # Resample if needed
        sample_rate = body.get("sample_rate", 16000)
        if sample_rate != 16000:
            # Linear interpolation resample
            duration = len(audio) / sample_rate
            target_len = int(duration * 16000)
            indices = np.linspace(0, len(audio) - 1, target_len)
            audio = np.interp(indices, np.arange(len(audio)), audio).astype(np.float32)
            logger.info("[SpeakerVerify] resampled from %d to 16000 Hz, new len=%d", sample_rate, len(audio))

        # Build speakers list with embeddings (cosine similarity only — fast)
        speakers = []
        for enr in enrollments.data:
            emb = service.embedding_from_base64(enr["embedding"])
            speakers.append({
                "name": enr["speaker_name"],
                "embedding": emb,
                "reference_audio": None,
            })

        # Multi-speaker verification
        best_name, best_score, accepted = service.verify_multi(audio, speakers)

        logger.info(
            "[SpeakerVerify] user=%s best=%s score=%.4f accepted=%s",
            user_id, best_name, best_score, accepted,
        )

        return {
            "verified": accepted,
            "speaker_name": best_name,
            "score": round(best_score, 4),
            "threshold": 0.40,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error verifying speaker: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
