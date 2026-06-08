import logging

from fastapi import APIRouter, HTTPException, Request

from app.services.supabase_client import get_supabase_client

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/summaries")
async def list_summaries(raw_request: Request, limit: int = 20, offset: int = 0):
    """Fetch summaries from context_summaries table."""
    auth_header = raw_request.headers.get("authorization", "")
    user_token = auth_header.removeprefix("Bearer ").strip() if auth_header.startswith("Bearer ") else None

    if not user_token:
        raise HTTPException(status_code=401, detail="Authorization required")

    try:
        supabase = get_supabase_client(user_token)
        response = (
            supabase.table("context_summaries")
            .select("id, session_id, summary_text, summary_type, time_start, time_end, segment_count, created_at")
            .order("time_end", desc=True)
            .range(offset, offset + limit - 1)
            .execute()
        )
        return {"summaries": response.data}
    except Exception as e:
        logger.error("Error fetching summaries: %s: %s", type(e).__name__, e)
        raise HTTPException(status_code=500, detail=f"Failed to fetch summaries: {e}")


@router.delete("/api/summaries/{summary_id}")
async def delete_summary(summary_id: str, raw_request: Request):
    """Delete a summary."""
    auth_header = raw_request.headers.get("authorization", "")
    user_token = auth_header.removeprefix("Bearer ").strip() if auth_header.startswith("Bearer ") else None
    if not user_token:
        raise HTTPException(status_code=401, detail="Authorization required")

    try:
        supabase = get_supabase_client(user_token)
        response = (
            supabase.table("context_summaries")
            .delete()
            .eq("id", summary_id)
            .execute()
        )

        if not response.data:
            raise HTTPException(status_code=404, detail="Summary not found")

        return {"deleted": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error deleting summary: %s: %s", type(e).__name__, e)
        raise HTTPException(status_code=500, detail=f"Failed to delete summary: {e}")
