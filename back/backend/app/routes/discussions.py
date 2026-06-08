import logging

from fastapi import APIRouter, HTTPException, Request

from app.services.supabase_client import get_supabase_client

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/discussions")
async def list_discussions(
    raw_request: Request,
    limit: int = 20,
    offset: int = 0,
    search: str | None = None,
):
    """Fetch conversation/session list from listening_sessions."""
    auth_header = raw_request.headers.get("authorization", "")
    user_token = auth_header.removeprefix("Bearer ").strip() if auth_header.startswith("Bearer ") else None

    if not user_token:
        raise HTTPException(status_code=401, detail="Authorization required")

    try:
        supabase = get_supabase_client(user_token)
        query = (
            supabase.table("listening_sessions")
            .select("id, started_at, ended_at, status, summary, segment_count")
            .order("started_at", desc=True)
            .range(offset, offset + limit - 1)
        )

        if search:
            query = query.ilike("summary", f"%{search}%")

        response = query.execute()
        return {"discussions": response.data}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error fetching discussions: %s: %s", type(e).__name__, e)
        raise HTTPException(status_code=500, detail=f"Failed to fetch discussions: {e}")


@router.get("/api/discussions/{discussion_id}")
async def get_discussion(discussion_id: str, raw_request: Request):
    """Fetch a single discussion with its segments."""
    auth_header = raw_request.headers.get("authorization", "")
    user_token = auth_header.removeprefix("Bearer ").strip() if auth_header.startswith("Bearer ") else None
    if not user_token:
        raise HTTPException(status_code=401, detail="Authorization required")

    try:
        supabase = get_supabase_client(user_token)

        # Get session
        session_resp = (
            supabase.table("listening_sessions")
            .select("id, started_at, ended_at, status, summary, segment_count")
            .eq("id", discussion_id)
            .execute()
        )
        if not session_resp.data:
            raise HTTPException(status_code=404, detail="Discussion not found")

        # Get segments
        segments_resp = (
            supabase.table("live_segments")
            .select("id, text, spoken_at")
            .eq("session_id", discussion_id)
            .order("spoken_at", desc=False)
            .execute()
        )

        return {
            "discussion": session_resp.data[0],
            "segments": segments_resp.data,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error fetching discussion detail: %s: %s", type(e).__name__, e)
        raise HTTPException(status_code=500, detail=f"Failed to fetch discussion: {e}")


@router.delete("/api/discussions/{discussion_id}")
async def delete_discussion(discussion_id: str, raw_request: Request):
    """Delete a discussion and all related segments and summaries."""
    auth_header = raw_request.headers.get("authorization", "")
    user_token = auth_header.removeprefix("Bearer ").strip() if auth_header.startswith("Bearer ") else None
    if not user_token:
        raise HTTPException(status_code=401, detail="Authorization required")

    try:
        supabase = get_supabase_client(user_token)

        # Delete related summaries
        supabase.table("context_summaries").delete().eq("session_id", discussion_id).execute()

        # Delete related segments
        supabase.table("live_segments").delete().eq("session_id", discussion_id).execute()

        # Delete the session itself
        response = (
            supabase.table("listening_sessions")
            .delete()
            .eq("id", discussion_id)
            .execute()
        )

        if not response.data:
            raise HTTPException(status_code=404, detail="Discussion not found")

        return {"deleted": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error deleting discussion: %s: %s", type(e).__name__, e)
        raise HTTPException(status_code=500, detail=f"Failed to delete discussion: {e}")
