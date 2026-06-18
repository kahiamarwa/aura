"""Pipeline conversationnel complet pour les enceintes headless.

Le device envoie l'audio de la commande + le flag `from_conversing` + le
contexte ambiant. Le cloud fait TOUT le gating (le device n'a aucune clé) :

  STT (Mistral)
   → [si from_conversing] intent Haiku : la phrase est-elle pour Aura ?
   → speaker verification : est-ce un utilisateur enrôlé ?
   → agent LLM (avec contexte ambiant)
   → TTS (ElevenLabs)

Réponse :
  - succès  → flux MP3 (+ headers X-Transcript, X-Response, X-Status: ok)
  - gated   → JSON 200 {status: empty|not_directed|rejected, ...} (pas d'audio)

Auth : X-Device-Token (si configuré) + Authorization: Bearer <JWT user>.
"""

import asyncio
import json
import logging
import threading
from datetime import datetime, timezone
from urllib.parse import quote

import httpx

from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import StreamingResponse, JSONResponse

from app.config import get_settings
from app.routes.gemini_stt import transcribe_audio, pcm_to_wav
from app.routes.intent_classifier import classify_intent
from app.services import llm_service
from app.services.tts_service import stream_tts
from app.services.speaker_service import SpeakerService
from app.services.supabase_client import get_supabase_client, get_user_id

logger = logging.getLogger(__name__)
router = APIRouter()

VERIFY_THRESHOLD = 0.40

_COMPLETE_SYS = (
    "Tu reçois une transcription partielle d'une commande vocale en français. "
    "Dis si la personne a FINI sa phrase, ou si elle s'est arrêtée au milieu "
    "(hésitation, pause de réflexion : « euh », phrase coupée, etc.). "
    "Réponds UNIQUEMENT par un mot : COMPLET ou INCOMPLET."
)


async def _is_complete(text: str) -> bool:
    """Haiku ultra-court : la phrase est-elle terminée ? Fail-open = complet."""
    settings = get_settings()
    if len(text.split()) < 2:
        return False  # trop court → sûrement une pause
    if not settings.ANTHROPIC_API_KEY:
        return True
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": settings.ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 5,
                    "system": _COMPLETE_SYS,
                    "messages": [{"role": "user", "content": text}],
                },
            )
        if r.status_code != 200:
            return True
        reply = r.json()["content"][0]["text"].strip().upper()
        return "INCOMPLET" not in reply
    except Exception:
        return True  # ne jamais bloquer l'utilisateur


def _check_device(request: Request) -> str | None:
    settings = get_settings()
    if settings.DEVICE_TOKEN:
        if request.headers.get("x-device-token", "") != settings.DEVICE_TOKEN:
            raise HTTPException(status_code=403, detail="Invalid device token")
    auth = request.headers.get("authorization", "")
    return auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else None


def _verify_speaker(user_token: str | None, wav_data: bytes) -> dict:
    """Vérifie le locuteur contre les voix enrôlées. Fail-open si pas de token/enrollment."""
    if not user_token:
        return {"verified": True, "reason": "no_token"}
    try:
        supabase = get_supabase_client(user_token)
        user_id = get_user_id(supabase, user_token)
        enr = supabase.table("speaker_enrollments").select("*").eq("user_id", user_id).execute()
        if not enr.data:
            return {"verified": True, "reason": "no_enrollments"}

        service = SpeakerService.get_instance()
        audio = service._wav_bytes_to_float32(wav_data)
        speakers = [
            {"name": e["speaker_name"], "embedding": service.embedding_from_base64(e["embedding"])}
            for e in enr.data
        ]
        name, score, accepted = service.verify_multi(audio, speakers)
        return {"verified": accepted, "speaker_name": name, "score": round(float(score), 4)}
    except Exception as e:
        logger.warning("[converse] verify error (fail-open): %s", e)
        return {"verified": True, "reason": "error"}


DEVICE_CONV_TITLE = "🔊 Enceinte Aura"


def _persist_device_conversation(user_token: str, user_text: str, assistant_text: str):
    """Persiste la conversation de l'enceinte dans Supabase (fire-and-forget).

    L'app/web peut alors l'afficher (en direct via realtime). Une seule
    conversation « Enceinte » par utilisateur, qui accumule les échanges.
    """
    if not user_token:
        return
    try:
        supabase = get_supabase_client(user_token)
        user_id = get_user_id(supabase, user_token)
        existing = (
            supabase.table("conversations").select("id")
            .eq("user_id", user_id).eq("title", DEVICE_CONV_TITLE).limit(1).execute()
        )
        if existing.data:
            conv_id = existing.data[0]["id"]
        else:
            r = supabase.table("conversations").insert(
                {"user_id": user_id, "title": DEVICE_CONV_TITLE}
            ).execute()
            conv_id = r.data[0]["id"]
        supabase.table("conversation_messages").insert([
            {"conversation_id": conv_id, "user_id": user_id, "role": "user", "content": user_text},
            {"conversation_id": conv_id, "user_id": user_id, "role": "assistant", "content": assistant_text},
        ]).execute()
        supabase.table("conversations").update(
            {"updated_at": datetime.now(timezone.utc).isoformat()}
        ).eq("id", conv_id).execute()
    except Exception as e:
        logger.warning("[converse] persist conversation error: %s", e)


@router.post("/api/device/converse")
async def converse(
    raw_request: Request,
    audio: UploadFile = File(...),
    from_conversing: str = Form("false"),
    context: str = Form("[]"),
    tentative: str = Form("false"),
):
    settings = get_settings()
    user_token = _check_device(raw_request)
    from_conv = from_conversing.lower() in ("1", "true", "yes")
    is_tentative = tentative.lower() in ("1", "true", "yes")
    try:
        ambient_context = json.loads(context) if context else []
        if not isinstance(ambient_context, list):
            ambient_context = []
    except Exception:
        ambient_context = []

    if not settings.MISTRAL_API_KEY:
        raise HTTPException(status_code=500, detail="MISTRAL_API_KEY not configured")
    if not settings.AURA_AGENT_URL or not settings.AURA_AGENT_TOKEN:
        raise HTTPException(status_code=500, detail="AURA agent not configured")
    if not settings.ELEVENLABS_API_KEY or not settings.ELEVENLABS_VOICE_ID:
        raise HTTPException(status_code=500, detail="TTS not configured")

    raw = await audio.read()
    if len(raw) < 1000:
        raise HTTPException(status_code=400, detail="Audio too small")
    wav_data = raw if raw[:4] == b"RIFF" else pcm_to_wav(raw, sample_rate=16000)

    # ── 1. STT ──────────────────────────────────────────────────────
    _dur = max(0, (len(wav_data) - 44)) / 2 / 16000  # ~durée (PCM16 mono 16k)
    transcript = (await transcribe_audio(settings.MISTRAL_API_KEY, wav_data) or "").strip()
    logger.info("[converse] audio=%.1fs transcript=%r from_conv=%s", _dur, transcript[:80], from_conv)
    if not transcript:
        return JSONResponse({"status": "empty"})

    # ── 1bis. Complétude (endpointing sémantique) ───────────────────
    # Si le device est en mode "tentative" (il a détecté une pause mais n'est
    # pas sûr que la phrase soit finie), on vérifie : pause de réflexion ou fin ?
    if is_tentative and not await _is_complete(transcript):
        logger.info("[converse] phrase incomplète → continue d'écouter: %r", transcript[:60])
        return JSONResponse({"status": "incomplete", "transcript": transcript})

    # ── 2. Intent (seulement depuis conversing) ─────────────────────
    # On ne REJETTE que si Haiku est CONFIANT que ce n'est pas pour Aura.
    # Dans le doute → on répond (mieux vaut répondre que d'ignorer une vraie demande).
    if from_conv:
        intent = await classify_intent(transcript, ambient_context)
        if not intent.get("directed", True) and intent.get("confidence", 0.0) >= 0.75:
            logger.info("[converse] not directed at Aura (conf=%.2f) → skip", intent.get("confidence", 0.0))
            return JSONResponse({"status": "not_directed", "transcript": transcript})

    # ── 3+4. Speaker verify ∥ LLM EN PARALLÈLE (latence) ────────────
    # La vérif locuteur (réseau + ONNX) tourne EN MÊME TEMPS que le LLM. Sur le
    # chemin nominal (accepté), son coût disparaît dans l'ombre du LLM.
    enriched = "\n".join(ambient_context) if ambient_context else None
    verify_task = asyncio.create_task(asyncio.to_thread(_verify_speaker, user_token, wav_data))
    llm_task = asyncio.create_task(llm_service.get_response(
        command=transcript,
        context=[],
        agent_url=settings.AURA_AGENT_URL,
        agent_token=settings.AURA_AGENT_TOKEN,
        user_token=user_token,
        enriched_context=enriched,
    ))

    verify = await verify_task
    if not verify.get("verified", True) and verify.get("reason") != "no_enrollments":
        logger.info("[converse] speaker rejected: %s", verify)
        llm_task.cancel()
        try:
            await llm_task
        except BaseException:
            pass
        return JSONResponse({
            "status": "rejected",
            "transcript": transcript,
            "speaker_name": verify.get("speaker_name"),
            "score": verify.get("score"),
        })

    result = await llm_task
    response_text = (result.get("text") or "").strip()
    logger.info("[converse] response=%r", response_text[:80])
    if not response_text:
        return JSONResponse({"status": "empty_response", "transcript": transcript})

    # ── Persistance (fire-and-forget) : l'app/web pourra l'afficher ──
    threading.Thread(
        target=_persist_device_conversation,
        args=(user_token, transcript, response_text),
        daemon=True,
    ).start()

    # ── 5. TTS → MP3 ────────────────────────────────────────────────
    headers = {
        "X-Status": "ok",
        "X-Transcript": quote(transcript),
        "X-Response": quote(response_text),
        "X-Speaker": quote(verify.get("speaker_name") or ""),
        "Access-Control-Expose-Headers": "X-Status, X-Transcript, X-Response, X-Speaker",
    }
    return StreamingResponse(
        stream_tts(text=response_text, voice_id=settings.ELEVENLABS_VOICE_ID, api_key=settings.ELEVENLABS_API_KEY),
        media_type="audio/mpeg",
        headers=headers,
    )


@router.post("/api/device/state")
async def device_state(
    raw_request: Request,
    state: str = Form(...),
    transcript: str = Form(""),
    seq: str = Form("0"),
):
    """Reçoit l'état courant de l'enceinte (IDLE/LISTENING/THINKING/SPEAKING…)
    et l'écrit dans Supabase pour l'affichage EN DIRECT côté app/web.

    seq monotone : on ignore un état arrivé EN RETARD (anti-désordre réseau).
    """
    user_token = _check_device(raw_request)
    if not user_token:
        return {"ok": False, "reason": "no_token"}
    seq_i = int(seq) if seq.isdigit() else 0
    try:
        supabase = get_supabase_client(user_token)
        user_id = get_user_id(supabase, user_token)
        # L'ordre est déjà garanti côté device (push sérialisé) → upsert direct,
        # AUCUN SELECT préalable (latence minimale). seq stocké pour info/ordre.
        supabase.table("device_status").upsert({
            "user_id": user_id,
            "state": state,
            "transcript": transcript or None,
            "seq": seq_i,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
        return {"ok": True}
    except Exception as e:
        logger.warning("[device] state upsert error: %s", e)
        return {"ok": False, "reason": "error"}


def _persist_ambient(user_token: str, text: str):
    """Ajoute un segment ambiant à device_status.ambient (borné aux 12 derniers)."""
    if not user_token or not text:
        return
    try:
        supabase = get_supabase_client(user_token)
        user_id = get_user_id(supabase, user_token)
        row = (
            supabase.table("device_status").select("ambient")
            .eq("user_id", user_id).limit(1).execute()
        )
        ambient = (row.data[0].get("ambient") or []) if row.data else []
        ambient = (ambient + [{"text": text, "ts": datetime.now(timezone.utc).isoformat()}])[-12:]
        supabase.table("device_status").upsert({
            "user_id": user_id,
            "ambient": ambient,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception as e:
        logger.warning("[device] ambient persist error: %s", e)


@router.post("/api/device/transcribe")
async def transcribe(raw_request: Request, audio: UploadFile = File(...)):
    """Transcription simple pour le contexte ambiant (batch passif du device)."""
    user_token = _check_device(raw_request)
    settings = get_settings()
    if not settings.MISTRAL_API_KEY:
        raise HTTPException(status_code=500, detail="MISTRAL_API_KEY not configured")
    raw = await audio.read()
    if len(raw) < 1000:
        return {"text": ""}
    wav_data = raw if raw[:4] == b"RIFF" else pcm_to_wav(raw, sample_rate=16000)
    text = (await transcribe_audio(settings.MISTRAL_API_KEY, wav_data) or "").strip()
    if text and user_token:
        threading.Thread(target=_persist_ambient, args=(user_token, text), daemon=True).start()
    return {"text": text}


@router.get("/api/device/speakers/embeddings")
async def device_speaker_embeddings(raw_request: Request):
    """Renvoie les empreintes vocales enrôlées de l'utilisateur (base64).

    Le device les met en cache pour faire l'endpointing par locuteur cible
    EN LOCAL (décider en temps réel si c'est bien l'utilisateur qui parle),
    sans round-trip réseau pendant la prise de commande.
    """
    user_token = _check_device(raw_request)
    if not user_token:
        return {"embeddings": []}
    try:
        supabase = get_supabase_client(user_token)
        user_id = get_user_id(supabase, user_token)
        enr = (
            supabase.table("speaker_enrollments")
            .select("speaker_name, embedding")
            .eq("user_id", user_id)
            .execute()
        )
        return {
            "embeddings": [
                {"name": e["speaker_name"], "embedding_b64": e["embedding"]}
                for e in (enr.data or [])
            ]
        }
    except Exception as e:
        logger.warning("[device] embeddings fetch error: %s", e)
        return {"embeddings": []}
