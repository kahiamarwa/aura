"""Factory for creating authenticated Supabase clients."""

import json
import base64

from supabase import create_client, Client

from app.config import get_settings


def get_supabase_client(user_token: str | None = None) -> Client:
    """Create a Supabase client, optionally authenticated with a user JWT.

    If user_token is provided, all queries will go through RLS as that user.
    Otherwise, uses the anon key (limited access).
    """
    settings = get_settings()
    client = create_client(settings.SUPABASE_URL, settings.SUPABASE_ANON_KEY)

    if user_token:
        # Set the JWT on the auth session so RLS sees auth.uid()
        try:
            client.auth.set_session(access_token=user_token, refresh_token="")
        except Exception:
            pass
        # Ensure the Authorization header is set for postgrest RLS
        client.postgrest.auth(user_token)

    return client


def get_user_id(supabase: Client, user_token: str) -> str:
    """Extract user_id from supabase auth, with JWT decode fallback."""
    try:
        user_resp = supabase.auth.get_user()
        if user_resp and user_resp.user:
            return user_resp.user.id
    except Exception:
        pass
    # Fallback: decode JWT payload directly
    payload = user_token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    data = json.loads(base64.urlsafe_b64decode(payload))
    return data["sub"]
