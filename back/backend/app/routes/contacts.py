import logging

from fastapi import APIRouter, HTTPException, Request

from app.services.supabase_client import get_supabase_client, get_user_id

logger = logging.getLogger(__name__)
router = APIRouter()


def _extract_token(request: Request) -> str:
    """Extract and validate user JWT from Authorization header."""
    auth_header = request.headers.get("authorization", "")
    user_token = auth_header.removeprefix("Bearer ").strip() if auth_header.startswith("Bearer ") else None
    if not user_token:
        raise HTTPException(status_code=401, detail="Authorization required")
    return user_token


@router.get("/api/contacts")
async def list_contacts(raw_request: Request, search: str | None = None):
    """List all contacts for the authenticated user."""
    user_token = _extract_token(raw_request)

    try:
        supabase = get_supabase_client(user_token)
        query = (
            supabase.table("contacts")
            .select("id, user_id, name, email, phone, company, notes, created_at")
            .order("created_at", desc=True)
        )

        if search:
            query = query.or_(
                f"name.ilike.%{search}%,"
                f"email.ilike.%{search}%,"
                f"company.ilike.%{search}%"
            )

        response = query.execute()
        return {"contacts": response.data}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error fetching contacts: %s: %s", type(e).__name__, e)
        raise HTTPException(status_code=500, detail=f"Failed to fetch contacts: {e}")


@router.post("/api/contacts")
async def create_contact(raw_request: Request):
    """Create a new contact."""
    user_token = _extract_token(raw_request)
    body = await raw_request.json()

    try:
        supabase = get_supabase_client(user_token)
        user_id = get_user_id(supabase, user_token)

        # Support both "name" and "first_name"+"last_name" from frontend
        name = body.get("name", "")
        if not name:
            first = body.get("first_name", "")
            last = body.get("last_name", "")
            name = f"{first} {last}".strip()

        contact_data = {
            "user_id": user_id,
            "name": name,
            "email": body.get("email"),
            "phone": body.get("phone"),
            "company": body.get("company"),
            "notes": body.get("notes"),
        }

        response = supabase.table("contacts").insert(contact_data).execute()
        return {"contact": response.data[0] if response.data else None}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error creating contact: %s: %s", type(e).__name__, e)
        raise HTTPException(status_code=500, detail=f"Failed to create contact: {e}")


@router.put("/api/contacts/{contact_id}")
async def update_contact(contact_id: str, raw_request: Request):
    """Update an existing contact."""
    user_token = _extract_token(raw_request)
    body = await raw_request.json()

    try:
        supabase = get_supabase_client(user_token)

        update_data = {}
        for field in ("name", "email", "phone", "company", "notes"):
            if field in body:
                update_data[field] = body[field]

        # Support first_name+last_name from frontend
        if "first_name" in body or "last_name" in body:
            first = body.get("first_name", "")
            last = body.get("last_name", "")
            update_data["name"] = f"{first} {last}".strip()

        if not update_data:
            raise HTTPException(status_code=400, detail="No fields to update")

        response = (
            supabase.table("contacts")
            .update(update_data)
            .eq("id", contact_id)
            .execute()
        )

        if not response.data:
            raise HTTPException(status_code=404, detail="Contact not found")

        return {"contact": response.data[0]}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error updating contact: %s: %s", type(e).__name__, e)
        raise HTTPException(status_code=500, detail=f"Failed to update contact: {e}")


@router.delete("/api/contacts/{contact_id}")
async def delete_contact(contact_id: str, raw_request: Request):
    """Delete a contact."""
    user_token = _extract_token(raw_request)

    try:
        supabase = get_supabase_client(user_token)
        response = (
            supabase.table("contacts")
            .delete()
            .eq("id", contact_id)
            .execute()
        )

        if not response.data:
            raise HTTPException(status_code=404, detail="Contact not found")

        return {"deleted": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error deleting contact: %s: %s", type(e).__name__, e)
        raise HTTPException(status_code=500, detail=f"Failed to delete contact: {e}")
