"""Appairage des enceintes (auth définitive) + résolution device → utilisateur.

L'enceinte n'envoie que X-Device-Token. Le backend retrouve l'utilisateur appairé
(table devices) et mint un JWT frais à partir du refresh token stocké CÔTÉ SERVEUR.
Le device ne détient donc AUCUN credential utilisateur (zéro secret sur l'enceinte).
"""

import time
import json
import base64
import logging

import httpx
from fastapi import APIRouter, Request, HTTPException

from app.config import get_settings
from app.services.supabase_client import get_supabase_client, get_user_id, get_service_client

logger = logging.getLogger(__name__)
router = APIRouter()

# Cache JWT par device : device_token → (access_token, exp)
_jwt_cache: dict[str, tuple[str, float]] = {}


def _jwt_exp(token: str) -> float:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload)).get("exp", 0))
    except Exception:
        return 0.0


def _mint_user_jwt(refresh_token: str):
    """refresh token → (access_token, refresh_token rotaté). (None, None) si échec."""
    settings = get_settings()
    if not refresh_token or not settings.SUPABASE_URL or not settings.SUPABASE_ANON_KEY:
        return None, None
    try:
        r = httpx.post(
            settings.SUPABASE_URL.rstrip("/") + "/auth/v1/token",
            params={"grant_type": "refresh_token"},
            headers={"apikey": settings.SUPABASE_ANON_KEY, "Content-Type": "application/json"},
            json={"refresh_token": refresh_token},
            timeout=10.0,
        )
        if r.status_code != 200:
            logger.warning("[pair] mint JWT échec %d", r.status_code)
            return None, None
        d = r.json()
        return d.get("access_token"), d.get("refresh_token")
    except Exception as e:
        logger.warning("[pair] mint JWT error: %s", e)
        return None, None


def resolve_user_token(request: Request) -> str | None:
    """JWT utilisateur pour ce device (via l'appairage), avec cache.

    Repli : header Authorization (rétrocompat dev). None si rien.
    """
    device_token = request.headers.get("x-device-token", "").strip()
    if device_token:
        cached = _jwt_cache.get(device_token)
        if cached and time.time() < cached[1]:
            return cached[0]
        try:
            svc = get_service_client()
            r = (svc.table("devices").select("refresh_token")
                 .eq("device_token", device_token).limit(1).execute())
            if r.data and r.data[0].get("refresh_token"):
                access, new_refresh = _mint_user_jwt(r.data[0]["refresh_token"])
                if access:
                    _jwt_cache[device_token] = (access, _jwt_exp(access) - 120.0)
                    if new_refresh and new_refresh != r.data[0]["refresh_token"]:
                        (svc.table("devices").update({"refresh_token": new_refresh})
                         .eq("device_token", device_token).execute())
                    return access
        except Exception as e:
            logger.warning("[pair] resolve error: %s", e)
    # Repli rétrocompat : Authorization: Bearer <JWT>
    auth = request.headers.get("authorization", "")
    return auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else None


def _user_from_auth(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    token = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else None
    if not token:
        raise HTTPException(status_code=401, detail="login required")
    return token


@router.post("/api/device/pair")
async def pair_device(raw_request: Request):
    """Lie une enceinte au compte connecté.

    Body: { device_token, label, refresh_token }
    refresh_token = celui de la session web de l'utilisateur (géré ensuite serveur).
    """
    user_token = _user_from_auth(raw_request)
    body = await raw_request.json()
    device_token = (body.get("device_token") or "").strip()
    label = (body.get("label") or "Enceinte Aura").strip()
    refresh_token = (body.get("refresh_token") or "").strip()
    if not device_token:
        raise HTTPException(status_code=400, detail="device_token required")
    try:
        supabase = get_supabase_client(user_token)
        user_id = get_user_id(supabase, user_token)
        supabase.table("devices").upsert({
            "device_token": device_token,
            "user_id": user_id,
            "label": label,
            "refresh_token": refresh_token or None,
        }).execute()
        _jwt_cache.pop(device_token, None)   # invalide le cache
        logger.info("[pair] enceinte « %s » liée à l'utilisateur", label)
        return {"ok": True, "label": label}
    except Exception as e:
        logger.error("[pair] error: %s", e)
        raise HTTPException(status_code=500, detail="pairing failed")


@router.get("/api/device/list")
async def list_devices(raw_request: Request):
    """Liste les enceintes de l'utilisateur (pour l'UI)."""
    user_token = _user_from_auth(raw_request)
    try:
        supabase = get_supabase_client(user_token)
        user_id = get_user_id(supabase, user_token)
        r = (supabase.table("devices")
             .select("device_token, label, created_at, last_seen_at")
             .eq("user_id", user_id).order("created_at", desc=True).execute())
        devices = [{
            "token_preview": (d["device_token"][:8] + "…") if d.get("device_token") else "",
            "label": d.get("label"),
            "created_at": d.get("created_at"),
            "last_seen_at": d.get("last_seen_at"),
        } for d in (r.data or [])]
        return {"devices": devices}
    except Exception as e:
        logger.warning("[pair] list error: %s", e)
        return {"devices": []}


@router.delete("/api/device/pair/{device_token}")
async def unpair_device(device_token: str, raw_request: Request):
    """Déconnecte une enceinte (révocation)."""
    user_token = _user_from_auth(raw_request)
    try:
        supabase = get_supabase_client(user_token)
        get_user_id(supabase, user_token)
        supabase.table("devices").delete().eq("device_token", device_token).execute()
        _jwt_cache.pop(device_token, None)
        return {"ok": True}
    except Exception as e:
        logger.error("[pair] unpair error: %s", e)
        raise HTTPException(status_code=500, detail="unpair failed")
