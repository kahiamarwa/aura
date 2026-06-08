import logging

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.config import get_settings
from app.schemas import ChatRequest
from app.services.llm_service import get_response, stream_response

logger = logging.getLogger(__name__)
router = APIRouter()


async def _call_context_enrichment(
    command: str,
    context: list,
    user_token: str,
    supabase_url: str,
    user_timezone: str = "Europe/Paris",
) -> str | None:
    """Call the Supabase edge function context-enrichment."""
    immediate_context = [
        {"text": seg.text, "timestamp": seg.timestamp.isoformat(), "is_partial": seg.is_partial}
        for seg in context
    ]

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{supabase_url}/functions/v1/context-enrichment",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {user_token}",
                },
                json={
                    "command": command,
                    "immediate_context": immediate_context,
                    "user_timezone": user_timezone,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                enriched = data.get("enriched_context", "")
                logger.info(
                    "Context enrichment: %d sections, %d chars",
                    data.get("sections_count", 0),
                    len(enriched),
                )
                return enriched
            else:
                logger.warning(
                    "Context enrichment failed: %d %s",
                    resp.status_code,
                    resp.text[:200],
                )
                return None
    except Exception as e:
        logger.warning("Context enrichment error: %s", e)
        return None


@router.post("/api/chat")
async def chat(request: ChatRequest, raw_request: Request):
    """Step 1: Send command to agent -> returns text response."""
    settings = get_settings()

    if not settings.AURA_AGENT_URL or not settings.AURA_AGENT_TOKEN:
        raise HTTPException(status_code=500, detail="AURA_AGENT_URL/TOKEN not configured")

    # Extract user JWT from Authorization header
    auth_header = raw_request.headers.get("Authorization", "")
    user_token = auth_header.removeprefix("Bearer ").strip() if auth_header.startswith("Bearer ") else None
    logger.info("User token present: %s", bool(user_token))

    # Build enriched context via Supabase edge function
    enriched_context: str | None = None
    if user_token and settings.SUPABASE_URL:
        enriched_context = await _call_context_enrichment(
            command=request.command,
            context=request.context,
            user_token=user_token,
            supabase_url=settings.SUPABASE_URL,
            user_timezone=request.user_timezone,
        )

    # DEV: print full enriched context
    if enriched_context:
        logger.info("═══ ENRICHED CONTEXT ═══\n%s\n═══ END CONTEXT ═══", enriched_context)
    else:
        logger.info("═══ NO ENRICHED CONTEXT (fallback to raw segments) ═══")

    # Check if client wants streaming
    accept = raw_request.headers.get("Accept", "")
    if "text/event-stream" in accept:
        logger.info("Streaming mode requested")
        return StreamingResponse(
            stream_response(
                command=request.command,
                context=request.context,
                agent_url=settings.AURA_AGENT_URL,
                agent_token=settings.AURA_AGENT_TOKEN,
                user_token=user_token,
                enriched_context=enriched_context,
                conversation_id=request.conversation_id,
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # Non-streaming fallback
    try:
        agent_result = await get_response(
            command=request.command,
            context=request.context,
            agent_url=settings.AURA_AGENT_URL,
            agent_token=settings.AURA_AGENT_TOKEN,
            user_token=user_token,
            enriched_context=enriched_context,
            conversation_id=request.conversation_id,
        )
        response_text = agent_result["text"]
        attachments = agent_result.get("attachments")
        logger.info("Agent responded: '%s'", response_text[:100])
    except Exception as e:
        logger.error("Agent error: %s: %s", type(e).__name__, e)
        raise HTTPException(status_code=502, detail=f"Agent error: {type(e).__name__}: {e}")

    result = {"text": response_text}
    if attachments:
        result["attachments"] = attachments
    return result


@router.post("/api/tts")
async def tts(request: dict):
    """Step 2: Convert text to speech -> streams MP3 audio."""
    from fastapi.responses import StreamingResponse
    from app.services.tts_service import stream_tts

    settings = get_settings()
    text = request.get("text", "")

    if not text:
        raise HTTPException(status_code=400, detail="No text provided")

    if not settings.ELEVENLABS_API_KEY or not settings.ELEVENLABS_VOICE_ID:
        raise HTTPException(status_code=500, detail="TTS not configured")

    try:
        return StreamingResponse(
            stream_tts(
                text=text,
                voice_id=settings.ELEVENLABS_VOICE_ID,
                api_key=settings.ELEVENLABS_API_KEY,
            ),
            media_type="audio/mpeg",
        )
    except Exception as e:
        logger.error("TTS error: %s: %s", type(e).__name__, e)
        raise HTTPException(status_code=502, detail=f"TTS error: {e}")
