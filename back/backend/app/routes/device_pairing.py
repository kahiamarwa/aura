"""Appairage des enceintes (auth définitive) + résolution device → utilisateur.

L'enceinte n'envoie que X-Device-Token. Le backend retrouve l'utilisateur appairé
(table devices) et mint un JWT frais à partir du refresh token stocké CÔTÉ SERVEUR.
Le device ne détient donc AUCUN credential utilisateur (zéro secret sur l'enceinte).
"""

import time
import logging

import jwt as pyjwt
from fastapi import APIRouter, Request, HTTPException

from app.config import get_settings
from app.services.supabase_client import get_supabase_client, get_user_id, get_service_client

logger = logging.getLogger(__name__)
router = APIRouter()

# Cache JWT forgé par device : device_token → (jwt, exp)
_jwt_cache: dict[str, tuple[str, float]] = {}
_JWT_TTL = 3600


def _mint_user_jwt(user_id: str) -> str | None:
    """Forge un JWT utilisateur court (signé avec le secret JWT du projet).

    SESSION DÉDIÉE par enceinte : indépendante du web, aucun refresh token
    partagé → zéro conflit, la sécurité Supabase reste activée.
    """
    settings = get_settings()
    secret = settings.SUPABASE_JWT_SECRET
    if not secret:
        logger.error("[pair] SUPABASE_JWT_SECRET manquant — impossible de forger le JWT")
        return None
    now = int(time.time())
    payload = {
        "sub": user_id,
        "aud": "authenticated",
        "role": "authenticated",
        "iat": now,
        "exp": now + _JWT_TTL,
    }
    try:
        return pyjwt.encode(payload, secret, algorithm="HS256")
    except Exception as e:
        logger.warning("[pair] mint JWT error: %s", e)
        return None


def resolve_user_token(request: Request) -> str | None:
    """JWT utilisateur pour ce device (forgé à la demande), avec cache.

    device_token → user_id (table devices, service role) → JWT forgé.
    Repli : header Authorization (rétrocompat dev). None si rien.
    """
    device_token = request.headers.get("x-device-token", "").strip()
    if device_token:
        cached = _jwt_cache.get(device_token)
        if cached and time.time() < cached[1]:
            return cached[0]
        try:
            svc = get_service_client()
            r = (svc.table("devices").select("user_id")
                 .eq("device_token", device_token).limit(1).execute())
            if r.data and r.data[0].get("user_id"):
                token = _mint_user_jwt(r.data[0]["user_id"])
                if token:
                    _jwt_cache[device_token] = (token, time.time() + _JWT_TTL - 120)
                    return token
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

    Body: { device_token, label }. Aucun secret côté device : le backend
    forgera un JWT à la demande à partir de ce mapping device→utilisateur.
    """
    user_token = _user_from_auth(raw_request)
    body = await raw_request.json()
    device_token = (body.get("device_token") or "").strip()
    label = (body.get("label") or "Enceinte Aura").strip()
    if not device_token:
        raise HTTPException(status_code=400, detail="device_token required")
    try:
        supabase = get_supabase_client(user_token)
        user_id = get_user_id(supabase, user_token)
        supabase.table("devices").upsert({
            "device_token": device_token,
            "user_id": user_id,
            "label": label,
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
