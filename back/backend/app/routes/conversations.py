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


@router.post("/api/conversations")
async def create_conversation(raw_request: Request):
    """Create a new conversation."""
    user_token = _extract_token(raw_request)
    body = await raw_request.json()

    try:
        supabase = get_supabase_client(user_token)
        user_id = get_user_id(supabase, user_token)
        logger.info("Creating conversation for user_id=%s", user_id)

        conv_data = {
            "user_id": user_id,
            "title": body.get("title", "Nouvelle conversation"),
        }
        response = supabase.table("conversations").insert(conv_data).execute()
        logger.info("Insert response: data=%s", response.data)

        if not response.data:
            raise HTTPException(status_code=500, detail="Failed to create conversation")

        conversation = response.data[0]

        # Insert initial messages if provided
        messages = body.get("messages", [])
        inserted_messages = []
        for msg in messages:
            msg_data = {
                    "conversation_id": conversation["id"],
                    "user_id": user_id,
                    "role": msg["role"],
                    "content": msg["content"],
                }
            if msg.get("attachments"):
                msg_data["attachments"] = msg["attachments"]
            msg_resp = (
                supabase.table("conversation_messages")
                .insert(msg_data)
                .execute()
            )
            if msg_resp.data:
                inserted_messages.append(msg_resp.data[0])

        return {"conversation": conversation, "messages": inserted_messages}
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        logger.error("Error creating conversation: %s: %s\n%s", type(e).__name__, e, traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Failed to create conversation: {type(e).__name__}: {e}")


@router.get("/api/conversations")
async def list_conversations(raw_request: Request, limit: int = 20, offset: int = 0):
    """List conversations for the authenticated user."""
    user_token = _extract_token(raw_request)

    try:
        supabase = get_supabase_client(user_token)
        response = (
            supabase.table("conversations")
            .select("id, title, created_at, updated_at")
            .order("updated_at", desc=True)
            .range(offset, offset + limit - 1)
            .execute()
        )
        return {"conversations": response.data}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error listing conversations: %s: %s", type(e).__name__, e)
        raise HTTPException(status_code=500, detail=f"Failed to list conversations: {e}")


@router.get("/api/conversations/{conversation_id}")
async def get_conversation(conversation_id: str, raw_request: Request):
    """Get a conversation with all its messages."""
    user_token = _extract_token(raw_request)

    try:
        supabase = get_supabase_client(user_token)

        conv_resp = (
            supabase.table("conversations")
            .select("id, title, created_at, updated_at")
            .eq("id", conversation_id)
            .execute()
        )
        if not conv_resp.data:
            raise HTTPException(status_code=404, detail="Conversation not found")

        msg_resp = (
            supabase.table("conversation_messages")
            .select("id, role, content, attachments, created_at")
            .eq("conversation_id", conversation_id)
            .order("created_at", desc=False)
            .execute()
        )

        return {
            "conversation": conv_resp.data[0],
            "messages": msg_resp.data,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error fetching conversation: %s: %s", type(e).__name__, e)
        raise HTTPException(status_code=500, detail=f"Failed to fetch conversation: {e}")


@router.post("/api/conversations/{conversation_id}/messages")
async def add_message(conversation_id: str, raw_request: Request):
    """Add a message to an existing conversation."""
    user_token = _extract_token(raw_request)
    body = await raw_request.json()

    role = body.get("role")
    content = body.get("content")
    if not role or not content:
        raise HTTPException(status_code=400, detail="role and content are required")

    try:
        supabase = get_supabase_client(user_token)
        user_id = get_user_id(supabase, user_token)

        msg_data = {
                "conversation_id": conversation_id,
                "user_id": user_id,
                "role": role,
                "content": content,
            }
        attachments = body.get("attachments")
        if attachments:
            msg_data["attachments"] = attachments
        msg_resp = (
            supabase.table("conversation_messages")
            .insert(msg_data)
            .execute()
        )

        # Update conversation's updated_at
        supabase.table("conversations").update(
            {"updated_at": datetime.now(timezone.utc).isoformat()}
        ).eq("id", conversation_id).execute()

        return {"message": msg_resp.data[0] if msg_resp.data else None}
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        logger.error("Error adding message: %s: %s\n%s", type(e).__name__, e, traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Failed to add message: {type(e).__name__}: {e}")


@router.delete("/api/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str, raw_request: Request):
    """Delete a conversation (messages cascade via ON DELETE CASCADE)."""
    user_token = _extract_token(raw_request)

    try:
        supabase = get_supabase_client(user_token)
        response = (
            supabase.table("conversations")
            .delete()
            .eq("id", conversation_id)
            .execute()
        )

        if not response.data:
            raise HTTPException(status_code=404, detail="Conversation not found")

        return {"deleted": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error deleting conversation: %s: %s", type(e).__name__, e)
        raise HTTPException(status_code=500, detail=f"Failed to delete conversation: {e}")
