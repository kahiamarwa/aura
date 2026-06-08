import logging

from fastapi import APIRouter, HTTPException, Request

from app.services.supabase_client import get_supabase_client

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/activity")
async def list_activity(raw_request: Request, limit: int = 50, offset: int = 0):
    """Fetch actions performed by Aura from activity_logs."""
    auth_header = raw_request.headers.get("authorization", "")
    user_token = auth_header.removeprefix("Bearer ").strip() if auth_header.startswith("Bearer ") else None

    if not user_token:
        raise HTTPException(status_code=401, detail="Authorization required")

    try:
        supabase = get_supabase_client(user_token)

        logs_resp = (
            supabase.table("activity_logs")
            .select("id, action_type, tool_name, description, metadata, status, created_at")
            .order("created_at", desc=True)
            .range(offset, offset + limit - 1)
            .execute()
        )

        activities = []
        for log in logs_resp.data or []:
            action = log.get("action_type", "")
            tool = log.get("tool_name", "")
            title = tool if tool else action
            activities.append({
                "id": log.get("id"),
                "type": action,
                "title": title,
                "description": log.get("description", ""),
                "created_at": log.get("created_at"),
            })

        return {"activity": activities, "total": len(activities)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error fetching activity: %s: %s", type(e).__name__, e)
        raise HTTPException(status_code=500, detail=f"Failed to fetch activity: {e}")
