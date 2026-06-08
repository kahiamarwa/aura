import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.config import get_settings
from app.services.supabase_client import get_supabase_client
from app.services.summarization_service import summarize_segments

logger = logging.getLogger(__name__)
router = APIRouter()


class SummarizeRequest(BaseModel):
    session_id: str
    summary_type: str = "rolling"
    time_start: datetime | None = None
    time_end: datetime | None = None


@router.post("/api/summarize")
async def summarize(request: SummarizeRequest, raw_request: Request):
    """Summarize segments for a session using Claude."""
    settings = get_settings()

    # Extract user JWT
    auth_header = raw_request.headers.get("Authorization", "")
    user_token = auth_header.removeprefix("Bearer ").strip() if auth_header.startswith("Bearer ") else None

    if not user_token:
        raise HTTPException(status_code=401, detail="Authorization required")

    if not settings.SUPABASE_URL:
        raise HTTPException(status_code=500, detail="Supabase not configured")

    # Get user ID from the session
    sb = get_supabase_client(user_token)
    user_resp = sb.auth.get_user(user_token)
    if not user_resp or not user_resp.user:
        raise HTTPException(status_code=401, detail="Invalid token")

    user_id = user_resp.user.id

    # Check that the session belongs to this user
    session_resp = (
        sb.table("listening_sessions")
        .select("id, user_id")
        .eq("id", request.session_id)
        .single()
        .execute()
    )
    if not session_resp.data or session_resp.data["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="Session not found")

    # Get Anthropic API key from env (or from settings)
    anthropic_key = getattr(settings, "ANTHROPIC_API_KEY", None)
    if not anthropic_key:
        # Try to get from environment directly
        import os
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY")

    try:
        summary = summarize_segments(
            supabase_client=sb,
            session_id=request.session_id,
            user_id=user_id,
            summary_type=request.summary_type,
            time_start=request.time_start,
            time_end=request.time_end,
            anthropic_api_key=anthropic_key,
        )
    except Exception as e:
        logger.error("Summarization error: %s: %s", type(e).__name__, e)
        raise HTTPException(status_code=502, detail=f"Summarization failed: {e}")

    if summary is None:
        return {"status": "skipped", "reason": "Not enough segments or no API key"}

    return {"status": "ok", "summary": summary}
