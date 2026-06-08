"""Context orchestration service.

Uses Claude to parse temporal references from French commands, queries Supabase
for relevant context (summaries + raw segments), and assembles a structured
context string for the LLM agent.
"""

import json
import logging
import re
from datetime import datetime, timedelta, timezone

import anthropic
from supabase import Client

from app.schemas import TranscriptionSegment

logger = logging.getLogger(__name__)

# ─── French stop words (for keyword extraction) ───
STOP_WORDS = frozenset(
    "le la les un une des de du à au aux en et ou où que qui quoi dont "
    "ce cette ces mon ma mes ton ta tes son sa ses notre nos votre vos leur leurs "
    "je tu il elle on nous vous ils elles me te se lui y "
    "ne pas plus rien jamais "
    "est sont a ont fait faire être avoir "
    "pour par avec dans sur sous entre vers chez sans contre "
    "tout tous toute toutes même aussi très bien peu trop "
    "ça c ca mais donc car si alors quand comment pourquoi "
    "moi toi soi ici là quel quelle quels quelles "
    "ai as avons avez eu été suis es sommes êtes était étais "
    "dit dis fais vas va vais allez allons veux veut "
    "résume résumer résumé envoie envoyer mail email ".split()
)

# ─── Time range extraction via LLM ───

TIME_EXTRACTION_SYSTEM = """Tu es un parseur de dates/heures ultra-précis. Tu reçois une commande en français et la date/heure actuelle.

Tu dois extraire la fenêtre temporelle exacte mentionnée dans la commande.

Réponds UNIQUEMENT en JSON valide, sans markdown, sans explication :
{
  "date_start": "YYYY-MM-DDTHH:MM:SS",
  "date_end": "YYYY-MM-DDTHH:MM:SS",
  "has_time_reference": true/false,
  "keywords": ["mot1", "mot2"]
}

Règles pour les heures :
- "entre 16h et 17h30" → 16:00:00 à 17:30:00
- "vers 10h" → 09:45:00 à 10:15:00 (±15 min)
- "à 14h" → 13:50:00 à 14:10:00 (±10 min)
- "entre 10h et 12h" → 10:00:00 à 12:00:00

Règles pour les périodes de la journée (quand AUCUNE heure précise n'est mentionnée) :
- "le matin" ou "ce matin" → 06:00:00 à 12:00:00
- "l'après-midi" ou "l'aprem" ou "cet aprem" → 12:00:00 à 18:00:00
- "le soir" ou "en soirée" → 18:00:00 à 23:00:00
- "en fin de journée" ou "fin de journée" ou "fin d'aprem" → 16:00:00 à 19:00:00
- "en début de journée" ou "tôt" → 06:00:00 à 10:00:00
- "en début d'aprem" ou "début d'après-midi" → 12:00:00 à 14:00:00
- Si aucune période ni heure → journée entière 00:00:00 à 23:59:59

Règles pour les jours :
- "hier" → jour précédent
- "avant-hier" → 2 jours avant
- "mardi dernier" ou "mardi de la semaine dernière" → le mardi de la semaine précédente
- "lundi" (sans "dernier") → le lundi le plus récent passé
- "la semaine dernière" → lundi 00:00 à dimanche 23:59 de la semaine précédente
- "le 5 mars" ou "le 5" → date exacte (mois courant si non précisé)
- "il y a 3 jours" → 3 jours avant maintenant
- "il y a 20 minutes" → maintenant - 20min à maintenant
- "aujourd'hui" ou "ce jour" → aujourd'hui

Combinaisons jour + heure :
- "mardi dernier entre 10h et 12h" → mardi précédent 10:00 à 12:00
- "hier matin" → hier 06:00 à 12:00
- "lundi l'aprem" → lundi précédent 12:00 à 18:00
- "la semaine dernière le matin" → lun-dim semaine passée, mais seulement 06:00-12:00 chaque jour → utilise lundi 06:00 à dimanche 12:00

- Les dates/heures doivent être dans le fuseau horaire de l'utilisateur (fourni dans le prompt)
- Si aucune référence temporelle, has_time_reference = false et utilise les 3 dernières heures
- keywords = mots-clés importants pour la recherche sémantique (sujets, noms de personnes, projets — PAS les mots temporels ni les stop words), max 5"""


async def extract_time_range_llm(
    command: str,
    user_timezone: str,
    anthropic_api_key: str,
) -> tuple[datetime, datetime, list[str]]:
    """Use Claude to extract time range and keywords from a French command.

    Returns (date_start, date_end, keywords) in UTC.
    """
    now_local = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

    user_prompt = (
        f"Date/heure actuelle (UTC): {now_local}\n"
        f"Fuseau horaire de l'utilisateur: {user_timezone}\n"
        f"Commande: {command}"
    )

    try:
        client = anthropic.Anthropic(api_key=anthropic_api_key)
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            system=TIME_EXTRACTION_SYSTEM,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw = message.content[0].text.strip()
        # Remove potential markdown fences
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)

        parsed = json.loads(raw)
        logger.info("LLM time extraction: %s", parsed)

        date_start = datetime.fromisoformat(parsed["date_start"])
        date_end = datetime.fromisoformat(parsed["date_end"])
        keywords = parsed.get("keywords", [])

        # Convert from user timezone to UTC for Supabase queries
        # The LLM returns times in user's local timezone, we need UTC
        from zoneinfo import ZoneInfo
        try:
            tz = ZoneInfo(user_timezone)
            # Attach timezone info and convert to UTC
            date_start = date_start.replace(tzinfo=tz).astimezone(timezone.utc)
            date_end = date_end.replace(tzinfo=tz).astimezone(timezone.utc)
        except (KeyError, Exception) as tz_err:
            logger.warning("Invalid timezone '%s', assuming UTC: %s", user_timezone, tz_err)
            date_start = date_start.replace(tzinfo=timezone.utc)
            date_end = date_end.replace(tzinfo=timezone.utc)

        logger.info("Time range (UTC): %s → %s, keywords: %s", date_start, date_end, keywords)
        return date_start, date_end, keywords

    except Exception as e:
        logger.warning("LLM time extraction failed, falling back to 3h window: %s", e)
        now = datetime.now(timezone.utc)
        return now - timedelta(hours=3), now, []


def extract_keywords(command: str) -> list[str]:
    """Extract meaningful keywords from a command (excluding stop words). Fallback for when LLM extraction fails."""
    words = re.findall(r"[a-zàâäéèêëïîôùûüÿçœæ]+", command.lower())
    keywords = [w for w in words if w not in STOP_WORDS and len(w) > 2]
    return keywords[:5]


def _format_timestamp(iso_str: str, user_tz: str | None = None) -> str:
    """Format ISO timestamp to readable HH:MM in user's timezone."""
    try:
        dt = datetime.fromisoformat(iso_str)
        if user_tz:
            from zoneinfo import ZoneInfo
            try:
                dt = dt.astimezone(ZoneInfo(user_tz))
            except Exception:
                pass
        return dt.strftime("%H:%M")
    except (ValueError, TypeError):
        return "??:??"


def _format_date_timestamp(iso_str: str, user_tz: str | None = None) -> str:
    """Format ISO timestamp to DD/MM HH:MM in user's timezone."""
    try:
        dt = datetime.fromisoformat(iso_str)
        if user_tz:
            from zoneinfo import ZoneInfo
            try:
                dt = dt.astimezone(ZoneInfo(user_tz))
            except Exception:
                pass
        return dt.strftime("%d/%m %H:%M")
    except (ValueError, TypeError):
        return "??/?? ??:??"


async def build_enriched_context(
    command: str,
    immediate_context: list[TranscriptionSegment],
    supabase_client: Client,
    anthropic_api_key: str = "",
    user_timezone: str = "UTC",
) -> str:
    """Build an enriched context string from 3 tiers:
    1. Immediate context (from frontend in-memory buffer)
    2. Historical context (summaries + segments from Supabase)
    3. Keyword search results (if command has specific topics)
    """
    sections: list[str] = []

    # ── Tier 1: Immediate context (last few minutes, from frontend) ──
    if immediate_context:
        lines = []
        for seg in immediate_context:
            ts = _format_timestamp(seg.timestamp.isoformat(), user_timezone)
            lines.append(f"[{ts}] {seg.text}")
        tier1 = "\n".join(lines[-30:])  # Last 30 segments max
        sections.append(f"--- CONTEXTE IMMÉDIAT (dernières minutes) ---\n{tier1}")

    # ── Extract time range via LLM ──
    if anthropic_api_key:
        date_start, date_end, keywords = await extract_time_range_llm(
            command, user_timezone, anthropic_api_key
        )
    else:
        # Fallback: last 3 hours, regex keywords
        logger.warning("No Anthropic API key, falling back to 3h window")
        now = datetime.now(timezone.utc)
        date_start, date_end = now - timedelta(hours=3), now
        keywords = extract_keywords(command)

    logger.info("Context window (UTC): %s to %s", date_start.isoformat(), date_end.isoformat())

    # ── Tier 2: Historical context from Supabase ──
    try:
        # Get summaries in the time window
        summaries_resp = (
            supabase_client.table("context_summaries")
            .select("summary_text, time_start, time_end, summary_type")
            .gte("time_end", date_start.isoformat())
            .lte("time_start", date_end.isoformat())
            .order("time_end", desc=True)
            .limit(5)
            .execute()
        )
        summaries = summaries_resp.data or []

        # Get raw segments not covered by summaries
        latest_summary_end = summaries[0]["time_end"] if summaries else date_start.isoformat()
        segments_resp = (
            supabase_client.table("live_segments")
            .select("text, spoken_at")
            .gte("spoken_at", latest_summary_end)
            .lte("spoken_at", date_end.isoformat())
            .order("spoken_at")
            .limit(100)
            .execute()
        )
        segments = segments_resp.data or []

        if summaries or segments:
            lines = []
            for s in summaries:
                ts_start = _format_date_timestamp(s["time_start"], user_timezone)
                ts_end = _format_date_timestamp(s["time_end"], user_timezone)
                lines.append(f"[Résumé {ts_start} → {ts_end}] {s['summary_text']}")

            for seg in segments:
                ts = _format_timestamp(seg["spoken_at"], user_timezone)
                lines.append(f"[{ts}] {seg['text']}")

            tier2 = "\n".join(lines)
            # Display time range in user's local timezone
            from zoneinfo import ZoneInfo
            try:
                tz = ZoneInfo(user_timezone)
                local_start = date_start.astimezone(tz)
                local_end = date_end.astimezone(tz)
            except Exception:
                local_start, local_end = date_start, date_end
            time_label = f"{local_start.strftime('%d/%m %H:%M')} → {local_end.strftime('%d/%m %H:%M')}"
            sections.append(f"--- CONTEXTE HISTORIQUE ({time_label}) ---\n{tier2}")
    except Exception as e:
        logger.warning("Failed to fetch historical context: %s", e)

    # ── Tier 3: Keyword search ──
    if not keywords:
        keywords = extract_keywords(command)
    if keywords:
        try:
            query = " & ".join(keywords)
            search_resp = (
                supabase_client.table("live_segments")
                .select("text, spoken_at")
                .text_search("fts", query, config="french")
                .gte("spoken_at", (date_start - timedelta(days=30)).isoformat())
                .order("spoken_at", desc=True)
                .limit(15)
                .execute()
            )
            results = search_resp.data or []

            if results:
                lines = []
                for r in results:
                    ts = _format_date_timestamp(r["spoken_at"], user_timezone)
                    lines.append(f"[{ts}] {r['text']}")
                tier3 = "\n".join(lines)
                sections.append(
                    f"--- RÉSULTATS RECHERCHE (mots-clés: {', '.join(keywords)}) ---\n{tier3}"
                )
        except Exception as e:
            logger.warning("Keyword search failed: %s", e)

    if not sections:
        return " ".join(seg.text for seg in immediate_context) if immediate_context else ""

    return "\n\n".join(sections)
