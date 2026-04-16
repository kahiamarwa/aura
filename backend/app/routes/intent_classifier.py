"""Intent classification for directed speech detection.

Determines whether a transcript is directed at Aura (the assistant)
or is ambient conversation between people in the room.
"""

import logging
import re

from fastapi import APIRouter, Request

router = APIRouter()
logger = logging.getLogger(__name__)

# ── Patterns: speech IS directed at Aura ──────────────────────────────

DIRECTED_PATTERNS = [
    # Imperative verbs (commands)
    r"\b(cherche|trouve|montre|affiche|envoie|dis[- ]moi|explique|calcule|ouvre|ferme|lance|arr[eê]te|mets|lis|traduis|r[eé]sume|cr[eé]e|supprime|ajoute|modifie|rappelle|programme|planifie|v[eé]rifie|compare|analyse|regarde|donne|aide|fais)\b",
    # 2nd person (tu/vous)
    r"\b(tu peux|tu sais|tu connais|tu as|tu vois|tu fais|est-ce que tu|peux-tu|sais-tu|vous pouvez|pouvez-vous)\b",
    # Direct address
    r"\b(aura|s'il te pla[iî]t|s'il vous pla[iî]t)\b",
    # Question starters
    r"^(quel|quelle|quels|quelles|comment|pourquoi|o[uù]\s|quand|combien|est-ce que|qu'est-ce)\b",
    # Continuation markers (follow-up to previous answer)
    r"^(et\s+(aussi|encore|pour|le|la|les|demain|apr[eè]s)|autre chose|encore une|une autre|parle[- ]moi)\b",
    # Attention markers at start
    r"^(hey|h[eé]|ok\s|bon\s|alors\s|dis\s|[eé]coute|tiens|merci)\b",
]

# ── Patterns: speech is NOT directed at Aura ──────────────────────────

UNDIRECTED_PATTERNS = [
    # 3rd person conversation
    r"\b(il dit|elle dit|ils disent|il pense|elle pense|ils pensent|il veut|elle veut|on va\s|il faut que je|je disais [àa])\b",
    # Side conversation fillers
    r"^(oui oui|non non|ah bon|ah oui|ah d'accord|hmm+|euh+|bah)\b",
    # Addressing someone else by name + comma
    r"^[A-Z][a-z]+\s*,",
    # Laughter / interjections
    r"^(haha|hihi|oh l[àa] l[àa]|pfff|bof)\b",
]

# Confidence thresholds
DIRECTED_THRESHOLD = 0.55
UNDIRECTED_THRESHOLD = 0.45

# French stop words for semantic overlap
STOP_WORDS = frozenset(
    "le la les de du des un une et ou en à au je tu il elle nous vous ils elles "
    "est a sont ont que qui ne pas ce se sa son ses leur leurs me te lui y "
    "on dans par pour avec tout cette cet ces plus mais où si".split()
)


def classify_rules(text: str, context: list[str]) -> tuple[bool | None, float]:
    """Rule-based classification.

    Returns (directed, confidence) or (None, confidence) if ambiguous.
    """
    text_lower = text.lower().strip()

    if len(text_lower) < 3:
        return None, 0.5

    directed_score = 0.0
    undirected_score = 0.0

    for pattern in DIRECTED_PATTERNS:
        if re.search(pattern, text_lower):
            directed_score += 0.25

    for pattern in UNDIRECTED_PATTERNS:
        if re.search(pattern, text_lower):
            undirected_score += 0.3

    # Semantic link: words in common with last Aura response
    if context:
        last_response = context[-1].lower()
        text_words = set(text_lower.split()) - STOP_WORDS
        response_words = set(last_response.split()) - STOP_WORDS
        overlap = text_words & response_words
        if len(overlap) >= 2:
            directed_score += 0.2

    total = directed_score + undirected_score
    if total == 0:
        return None, 0.5

    confidence = directed_score / max(total, 0.01)

    if confidence >= DIRECTED_THRESHOLD:
        return True, min(confidence, 1.0)
    elif confidence <= UNDIRECTED_THRESHOLD:
        return False, min(1.0 - confidence, 1.0)
    else:
        return None, confidence


@router.post("/api/classify-intent")
async def classify_intent_endpoint(raw_request: Request):
    """Classify whether a transcript is directed at the assistant.

    Body: { "text": str, "context": [str] }
    Returns: { "directed": bool, "confidence": float, "method": str }
    """
    body = await raw_request.json()
    text = body.get("text", "")
    context = body.get("context", [])

    if not text.strip():
        return {"directed": False, "confidence": 1.0, "method": "rules"}

    directed, confidence = classify_rules(text, context)

    if directed is not None:
        logger.info(
            "[IntentClassify] text='%s' → %s (%.2f, rules)",
            text[:60].encode("ascii", "replace").decode(),
            "DIRECTED" if directed else "NOT_DIRECTED",
            confidence,
        )
        return {
            "directed": directed,
            "confidence": round(confidence, 3),
            "method": "rules",
        }

    # Ambiguous → default to directed (fail-open) with low confidence
    logger.info(
        "[IntentClassify] text='%s' → AMBIGUOUS (%.2f) → defaulting DIRECTED",
        text[:60].encode("ascii", "replace").decode(),
        confidence,
    )
    return {
        "directed": True,
        "confidence": round(confidence, 3),
        "method": "rules_ambiguous",
    }
