"""Endpoints SPEAKER côté CLOUD pour les devices (Raspberry Pi).

Le device calcule l'embedding vocal en local (ONNX) et l'envoie ici.
Le cloud détient les secrets (Supabase) et fait le stockage + la comparaison.

Auth :
  - X-Device-Token : identifie le device (vérifié contre settings.DEVICE_TOKEN).
  - Authorization: Bearer <JWT user> : transmis par le device, sert au RLS Supabase.

Comparaison : les embeddings ECAPA sont L2-normalisés → cosinus = produit scalaire.
"""

import base64
import io
import logging
import uuid
from datetime import datetime, timezone

import numpy as np
from fastapi import APIRouter, HTTPException, Request

from app.config import get_settings
from app.services.supabase_client import get_supabase_client, get_user_id

logger = logging.getLogger(__name__)
router = APIRouter()

SIMILARITY_THRESHOLD = 0.40


def _check_device(request: Request) -> str:
    """Valide le device token (si configuré) et retourne le JWT user."""
    settings = get_settings()
    expected = settings.DEVICE_TOKEN
    if expected:
        presented = request.headers.get("x-device-token", "")
        if presented != expected:
            raise HTTPException(status_code=403, detail="Invalid device token")

    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authorization required")
    return auth_header.removeprefix("Bearer ").strip()


def _embedding_from_base64(b64: str) -> np.ndarray:
    buf = io.BytesIO(base64.b64decode(b64))
    return np.load(buf)


@router.get("/api/device/speakers")
async def list_speakers(raw_request: Request):
    """Liste les voix enrôlées de l'utilisateur."""
    user_token = _check_device(raw_request)
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
        logger.error("[device] Error listing speakers: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/device/speakers/enroll")
async def enroll_speaker(raw_request: Request):
    """Enrôle une voix à partir d'un embedding calculé par le device.

    Body JSON: { speaker_name, embedding_b64, reference_audio_b64?, ... }
    """
    user_token = _check_device(raw_request)
    try:
        body = await raw_request.json()
        speaker_name = body.get("speaker_name")
        embedding_b64 = body.get("embedding_b64")
        if not speaker_name or not embedding_b64:
            raise HTTPException(status_code=400, detail="speaker_name and embedding_b64 required")

        supabase = get_supabase_client(user_token)
        user_id = get_user_id(supabase, user_token)

        # Audio de référence (optionnel) → storage
        storage_path = None
        ref_b64 = body.get("reference_audio_b64")
        if ref_b64:
            try:
                ref_bytes = base64.b64decode(ref_b64)
                storage_path = f"{user_id}/{speaker_name}_{uuid.uuid4().hex[:8]}.wav"
                supabase.storage.from_("speaker-audio").upload(
                    path=storage_path,
                    file=ref_bytes,
                    file_options={"content-type": "audio/wav"},
                )
            except Exception as e:
                logger.warning("[device] reference audio upload failed: %s", e)
                storage_path = None

        existing = (
            supabase.table("speaker_enrollments")
            .select("id")
            .eq("user_id", user_id)
            .eq("speaker_name", speaker_name)
            .execute()
        )

        now = datetime.now(timezone.utc).isoformat()
        if existing.data:
            supabase.table("speaker_enrollments").update({
                "embedding": embedding_b64,
                "reference_audio_path": storage_path,
                "updated_at": now,
            }).eq("id", existing.data[0]["id"]).execute()
            enrollment_id = existing.data[0]["id"]
        else:
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
            "embedding_dims": body.get("embedding_dims"),
            "reference_duration_s": body.get("reference_duration_s"),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("[device] Error enrolling speaker: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/device/speakers/{enrollment_id}")
async def delete_speaker(enrollment_id: str, raw_request: Request):
    """Supprime une voix enrôlée."""
    user_token = _check_device(raw_request)
    try:
        supabase = get_supabase_client(user_token)
        user_id = get_user_id(supabase, user_token)

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
        if enrollment.get("reference_audio_path"):
            try:
                supabase.storage.from_("speaker-audio").remove([enrollment["reference_audio_path"]])
            except Exception:
                pass

        supabase.table("speaker_enrollments").delete().eq("id", enrollment_id).execute()
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[device] Error deleting speaker: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/device/speakers/verify")
async def verify_speaker(raw_request: Request):
    """Compare l'embedding de la commande (calculé par le device) aux voix enrôlées.

    Body JSON: { "embedding_b64": "<np.save base64 d'un vecteur 192-dim L2-normé>" }
    """
    user_token = _check_device(raw_request)
    try:
        body = await raw_request.json()
        embedding_b64 = body.get("embedding_b64")
        if not embedding_b64:
            raise HTTPException(status_code=400, detail="embedding_b64 required")

        supabase = get_supabase_client(user_token)
        user_id = get_user_id(supabase, user_token)

        enrollments = (
            supabase.table("speaker_enrollments")
            .select("*")
            .eq("user_id", user_id)
            .execute()
        )

        if not enrollments.data:
            return {
                "verified": True,
                "speaker_name": None,
                "score": 1.0,
                "threshold": SIMILARITY_THRESHOLD,
                "reason": "no_enrollments",
            }

        query = _embedding_from_base64(embedding_b64).astype(np.float32)
        norm = float(np.linalg.norm(query))
        if norm > 0:
            query = query / norm

        best_name = None
        best_score = -1.0
        for enr in enrollments.data:
            ref = _embedding_from_base64(enr["embedding"]).astype(np.float32)
            score = float(np.dot(query, ref))  # cosinus (vecteurs L2-normés)
            if score > best_score:
                best_score = score
                best_name = enr["speaker_name"]

        accepted = best_score >= SIMILARITY_THRESHOLD
        logger.info(
            "[device] verify user=%s best=%s score=%.4f accepted=%s",
            user_id, best_name, best_score, accepted,
        )
        return {
            "verified": accepted,
            "speaker_name": best_name,
            "score": round(best_score, 4),
            "threshold": SIMILARITY_THRESHOLD,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("[device] Error verifying speaker: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
