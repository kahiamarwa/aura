import logging

from fastapi import APIRouter, HTTPException

from app.config import get_settings
from app.services.stt_service import create_stt_token

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/stt-token")
async def get_stt_token():
    settings = get_settings()
    if not settings.ELEVENLABS_API_KEY:
        raise HTTPException(status_code=500, detail="ELEVENLABS_API_KEY not configured")
    try:
        token = await create_stt_token(settings.ELEVENLABS_API_KEY)
        return {"token": token}
    except Exception as e:
        logger.error(f"STT token error: {type(e).__name__}: {e}")
        raise HTTPException(status_code=502, detail=f"Failed to generate token: {e}")
