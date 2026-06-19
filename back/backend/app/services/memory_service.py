"""Mémoire RAG : embeddings (Mistral) + persistance + recherche sémantique.

L'ambiant et les résumés sont embeddés dans memory_chunks. À chaque commande,
on récupère les souvenirs PERTINENTS par similarité (pas par mot-clé) et on les
injecte dans le contexte de l'agent → il « se rappelle » de n'importe quoi.
"""

import logging

import httpx

from app.config import get_settings
from app.services.supabase_client import get_supabase_client, get_user_id

logger = logging.getLogger(__name__)
MISTRAL_EMBED_URL = "https://api.mistral.ai/v1/embeddings"


def embed(text: str) -> str | None:
    """Texte → vecteur (format '[...]' prêt pour pgvector). None si échec."""
    settings = get_settings()
    if not settings.MISTRAL_API_KEY or not text or not text.strip():
        return None
    try:
        r = httpx.post(
            MISTRAL_EMBED_URL,
            headers={"Authorization": f"Bearer {settings.MISTRAL_API_KEY}",
                     "Content-Type": "application/json"},
            json={"model": "mistral-embed", "input": [text[:8000]]},
            timeout=15.0,
        )
        r.raise_for_status()
        vec = r.json()["data"][0]["embedding"]
        return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"
    except Exception as e:
        logger.warning("[memory] embed error: %s", e)
        return None


def persist_chunk(user_token: str, text: str, source: str = "ambient"):
    """Embedde et stocke un fragment dans memory_chunks (fire-and-forget)."""
    if not user_token or not text or not text.strip():
        return
    emb = embed(text)
    if not emb:
        return
    try:
        supabase = get_supabase_client(user_token)
        user_id = get_user_id(supabase, user_token)
        supabase.table("memory_chunks").insert({
            "user_id": user_id, "content": text, "embedding": emb, "source": source,
        }).execute()
    except Exception as e:
        logger.warning("[memory] persist chunk error: %s", e)


def retrieve(user_token: str, query: str, k: int = 6, min_sim: float = 0.55) -> str:
    """Souvenirs PERTINENTS pour la requête (similarité cosinus). '' si rien."""
    if not user_token or not query or not query.strip():
        return ""
    emb = embed(query)
    if not emb:
        return ""
    try:
        supabase = get_supabase_client(user_token)
        user_id = get_user_id(supabase, user_token)
        r = supabase.rpc("match_memory_chunks", {
            "query_embedding": emb, "match_user_id": user_id, "match_count": k,
        }).execute()
        rows = [c for c in (r.data or []) if (c.get("similarity") or 0) >= min_sim]
        if not rows:
            return ""
        lines = [f"[{(c.get('created_at') or '')[:16]}] {c.get('content', '')}" for c in rows]
        logger.info("[memory] %d souvenir(s) pertinent(s) injecté(s)", len(lines))
        return "Souvenirs pertinents (mémoire de l'utilisateur) :\n" + "\n".join(lines)
    except Exception as e:
        logger.warning("[memory] retrieve error: %s", e)
        return ""
