"""Summarization service using Claude to compress transcription segments."""

import logging
from datetime import datetime

import anthropic
from supabase import Client

logger = logging.getLogger(__name__)

SUMMARIZE_SYSTEM_PROMPT = """Tu es un assistant spécialisé dans la synthèse de conversations et réunions en français.
Tu reçois une série de segments de transcription horodatés.
Produis un résumé structuré et concis en français qui capture :
- Les sujets principaux abordés
- Les décisions prises
- Les actions à faire (si mentionnées)
- Les personnes mentionnées

Format le résumé en markdown avec des sections claires. Sois concis mais ne perds pas d'information importante.
Le résumé doit faire environ 20% de la longueur originale."""


def summarize_segments(
    supabase_client: Client,
    session_id: str,
    user_id: str,
    summary_type: str = "rolling",
    time_start: datetime | None = None,
    time_end: datetime | None = None,
    anthropic_api_key: str | None = None,
) -> str | None:
    """Read segments from Supabase, summarize with Claude, store the summary."""

    # Build query
    query = (
        supabase_client.table("live_segments")
        .select("text, spoken_at")
        .eq("session_id", session_id)
        .order("spoken_at")
    )
    if time_start:
        query = query.gte("spoken_at", time_start.isoformat())
    if time_end:
        query = query.lte("spoken_at", time_end.isoformat())

    resp = query.limit(500).execute()
    segments = resp.data or []

    if len(segments) < 5:
        logger.info("Too few segments (%d) to summarize", len(segments))
        return None

    # Build text block for Claude
    text_block = "\n".join(
        f"[{seg['spoken_at']}] {seg['text']}" for seg in segments
    )
    total_words = sum(len(seg["text"].split()) for seg in segments)
    logger.info(
        "Summarizing %d segments (%d words) for session %s",
        len(segments), total_words, session_id,
    )

    # Call Claude
    if not anthropic_api_key:
        logger.warning("No Anthropic API key, skipping summarization")
        return None

    client = anthropic.Anthropic(api_key=anthropic_api_key)
    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        system=SUMMARIZE_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": f"Voici les segments de transcription à résumer :\n\n{text_block}",
            }
        ],
    )
    summary_text = message.content[0].text
    logger.info("Summary generated: %d chars", len(summary_text))

    # Determine time range from actual segments
    actual_start = segments[0]["spoken_at"]
    actual_end = segments[-1]["spoken_at"]

    # Store in context_summaries
    supabase_client.table("context_summaries").insert({
        "user_id": user_id,
        "session_id": session_id,
        "time_start": actual_start,
        "time_end": actual_end,
        "segment_count": len(segments),
        "summary_text": summary_text,
        "summary_type": summary_type,
    }).execute()

    # Update session status if session_final
    if summary_type == "session_final":
        supabase_client.table("listening_sessions").update({
            "status": "summarized",
            "summary": {"text": summary_text, "segment_count": len(segments)},
        }).eq("id", session_id).execute()

    logger.info("Summary stored for session %s", session_id)
    return summary_text
