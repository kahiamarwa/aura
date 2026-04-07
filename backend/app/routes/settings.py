import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

from app.services.supabase_client import get_supabase_client, get_user_id

logger = logging.getLogger(__name__)
router = APIRouter()


def _extract_token(request: Request) -> str:
    auth_header = request.headers.get("authorization", "")
    user_token = auth_header.removeprefix("Bearer ").strip() if auth_header.startswith("Bearer ") else None
    if not user_token:
        raise HTTPException(status_code=401, detail="Authorization required")
    return user_token


SETTINGS_FIELDS = [
    "langue", "theme", "timezone", "notifications",
    "wake_word", "voix", "vitesse", "barge_in",
    "continuite", "son_confirmation",
    "passive_active", "resumes_auto", "passive_timeout",
    "retention", "langue_transcription",
    "logo_path",
]


@router.get("/api/settings")
async def get_settings(raw_request: Request):
    """Get user settings. Creates default row if none exists."""
    user_token = _extract_token(raw_request)

    try:
        supabase = get_supabase_client(user_token)
        user_id = get_user_id(supabase, user_token)

        response = (
            supabase.table("user_settings")
            .select("*")
            .eq("user_id", user_id)
            .execute()
        )

        if response.data:
            return {"settings": response.data[0]}

        # No settings yet — insert defaults
        insert_resp = (
            supabase.table("user_settings")
            .insert({"user_id": user_id})
            .execute()
        )
        return {"settings": insert_resp.data[0] if insert_resp.data else {}}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error fetching settings: %s: %s", type(e).__name__, e)
        raise HTTPException(status_code=500, detail=f"Failed to fetch settings: {e}")


@router.put("/api/settings")
async def update_settings(raw_request: Request):
    """Update user settings. Only provided fields are updated."""
    user_token = _extract_token(raw_request)
    body = await raw_request.json()

    try:
        supabase = get_supabase_client(user_token)
        user_id = get_user_id(supabase, user_token)

        update_data = {}
        for field in SETTINGS_FIELDS:
            if field in body:
                update_data[field] = body[field]

        if not update_data:
            raise HTTPException(status_code=400, detail="No fields to update")

        update_data["updated_at"] = datetime.now(timezone.utc).isoformat()

        # Upsert: update if exists, insert if not
        response = (
            supabase.table("user_settings")
            .update(update_data)
            .eq("user_id", user_id)
            .execute()
        )

        if not response.data:
            # No row to update — insert with provided values
            insert_data = {"user_id": user_id, **update_data}
            response = (
                supabase.table("user_settings")
                .insert(insert_data)
                .execute()
            )

        return {"settings": response.data[0] if response.data else {}}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error updating settings: %s: %s", type(e).__name__, e)
        raise HTTPException(status_code=500, detail=f"Failed to update settings: {e}")
