"""Intent classification for directed speech detection.

Uses Claude Haiku to determine whether a transcript is directed at Aura
or is ambient conversation between people in the room.
"""

import logging

import httpx
from fastapi import APIRouter, Request

from app.config import get_settings

router = APIRouter()
logger = logging.getLogger(__name__)

HAIKU_SYSTEM_PROMPT = """Tu es un classifieur de parole pour l'assistant vocal "Aura".
Tu dois determiner si une phrase est adressee a Aura (l'assistant) ou si c'est une conversation ambiante entre des personnes dans la piece.

Reponds UNIQUEMENT par un JSON: {"directed": true/false, "confidence": 0.0-1.0}

REGLE: dans le doute, c'est POUR Aura (directed=true). Ne mets directed=false avec
une confidence haute (>0.8) QUE si c'est CLAIREMENT une conversation entre humains.

Indices que c'est pour Aura (directed=true):
- Toute demande d'information ou question: quel, comment, pourquoi, ou, quand,
  combien, "on peut avoir...", "c'est quoi...", "donne...", meteo, temperature, heure
- Imperatifs: cherche, trouve, montre, envoie, dis-moi, explique, rappelle, ajoute
- 2eme personne: tu peux, est-ce que tu, tu sais
- Suite logique de la derniere reponse d'Aura (meme sujet)
- Marqueurs d'attention: hey, ok, alors, ecoute

Indices que c'est PAS pour Aura (directed=false, confidence haute SEULEMENT si evident):
- Adresse a un prenom: "Paul, tu viens ?"
- Recit a la 3eme personne sur des gens: "il dit que", "elle pense que"
- Pure conversation sociale sans demande: "oui d'accord", "non mais attends"
NB: "on peut avoir X" / "on regarde X" = une DEMANDE = pour Aura (directed=true)."""


async def classify_intent(text: str, context: list[str]) -> dict:
    """Classify whether a transcript is directed at Aura (reusable).

    Returns: { "directed": bool, "confidence": float, "method": str }
    Fails OPEN (directed=True) on any error except empty input.
    """
    if not text.strip():
        return {"directed": False, "confidence": 1.0, "method": "empty"}

    settings = get_settings()

    user_msg = f'Phrase captee: "{text}"'
    if context:
        user_msg += f'\n\nDerniere reponse d\'Aura: "{context[-1][:200]}"'

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": settings.ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 50,
                    "system": HAIKU_SYSTEM_PROMPT,
                    "messages": [{"role": "user", "content": user_msg}],
                },
            )

        if response.status_code != 200:
            logger.warning("[IntentClassify] Haiku error %d, defaulting to directed", response.status_code)
            return {"directed": True, "confidence": 0.5, "method": "fallback"}

        result = response.json()
        reply = result["content"][0]["text"].strip()

        # Parse JSON from Haiku response (may be wrapped in ```json ... ```)
        import json
        import re
        try:
            # Extract JSON from markdown code block if present
            json_match = re.search(r'\{[^{}]*"directed"[^{}]*\}', reply)
            json_str = json_match.group(0) if json_match else reply
            parsed = json.loads(json_str)
            directed = bool(parsed.get("directed", True))
            confidence = float(parsed.get("confidence", 0.5))
        except (json.JSONDecodeError, KeyError, AttributeError):
            logger.warning("[IntentClassify] Malformed Haiku response: %s", reply[:100])
            return {"directed": True, "confidence": 0.5, "method": "fallback"}

        safe_text = text[:60].encode("ascii", "replace").decode()
        logger.info(
            "[IntentClassify] '%s' → %s (%.2f, haiku)",
            safe_text,
            "DIRECTED" if directed else "NOT_DIRECTED",
            confidence,
        )

        return {
            "directed": directed,
            "confidence": round(confidence, 3),
            "method": "haiku",
        }

    except Exception as e:
        logger.warning("[IntentClassify] Error: %s, defaulting to directed", e)
        return {"directed": True, "confidence": 0.5, "method": "fallback"}


@router.post("/api/classify-intent")
async def classify_intent_endpoint(raw_request: Request):
    """HTTP wrapper around classify_intent. Body: { text, context: [str] }."""
    body = await raw_request.json()
    return await classify_intent(body.get("text", ""), body.get("context", []))
