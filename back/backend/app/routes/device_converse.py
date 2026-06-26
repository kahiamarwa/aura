"""Pipeline conversationnel complet pour les enceintes headless.

Le device envoie l'audio de la commande + le flag `from_conversing` + le
contexte ambiant. Le cloud fait TOUT le gating (le device n'a aucune clé) :

  STT (Mistral)
   → [si from_conversing] intent Haiku : la phrase est-elle pour Aura ?
   → speaker verification : est-ce un utilisateur enrôlé ?
   → agent LLM (avec contexte ambiant)
   → TTS (ElevenLabs)

Réponse :
  - succès  → flux MP3 (+ headers X-Transcript, X-Response, X-Status: ok)
  - gated   → JSON 200 {status: empty|not_directed|rejected, ...} (pas d'audio)

Auth : X-Device-Token (si configuré) + Authorization: Bearer <JWT user>.
"""

import asyncio
import json
import logging
import threading
from datetime import datetime, timezone
from urllib.parse import quote

import httpx

from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import StreamingResponse, JSONResponse

from app.config import get_settings
from app.routes.gemini_stt import transcribe_audio, pcm_to_wav
from app.routes.intent_classifier import classify_intent
from app.services import llm_service
from app.services.tts_service import stream_tts
from app.services.speaker_service import SpeakerService
from app.services.supabase_client import get_supabase_client, get_user_id, get_service_client
from app.services import memory_service
from app.routes.device_pairing import resolve_user_token

logger = logging.getLogger(__name__)
router = APIRouter()

VERIFY_THRESHOLD = 0.40

_COMPLETE_SYS = (
    "Tu reçois une transcription partielle d'une commande vocale en français. "
    "Dis si la personne a FINI sa phrase, ou si elle s'est arrêtée au milieu "
    "(hésitation, pause de réflexion : « euh », phrase coupée, etc.). "
    "Réponds UNIQUEMENT par un mot : COMPLET ou INCOMPLET."
)


async def _is_complete(text: str) -> bool:
    """Haiku ultra-court : la phrase est-elle terminée ? Fail-open = complet."""
    settings = get_settings()
    if len(text.split()) < 2:
        return False  # trop court → sûrement une pause
    if not settings.ANTHROPIC_API_KEY:
        return True
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": settings.ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 5,
                    "system": _COMPLETE_SYS,
                    "messages": [{"role": "user", "content": text}],
                },
            )
        if r.status_code != 200:
            return True
        reply = r.json()["content"][0]["text"].strip().upper()
        return "INCOMPLET" not in reply
    except Exception:
        return True  # ne jamais bloquer l'utilisateur


def _check_device(request: Request) -> str | None:
    """Authentifie l'enceinte et renvoie le JWT de l'utilisateur APPAIRÉ.

    Priorité : appairage (device_token → utilisateur via la table devices, le
    device ne détient aucun credential). Repli rétrocompat : Authorization Bearer.
    """
    return resolve_user_token(request)


def _verify_speaker(user_token: str | None, wav_data: bytes) -> dict:
    """Vérifie le locuteur contre les voix enrôlées. Fail-open si pas de token/enrollment."""
    if not user_token:
        return {"verified": True, "reason": "no_token"}
    try:
        supabase = get_supabase_client(user_token)
        user_id = get_user_id(supabase, user_token)
        enr = supabase.table("speaker_enrollments").select("*").eq("user_id", user_id).execute()
        if not enr.data:
            return {"verified": True, "reason": "no_enrollments"}

        service = SpeakerService.get_instance()
        audio = service._wav_bytes_to_float32(wav_data)
        speakers = [
            {"name": e["speaker_name"], "embedding": service.embedding_from_base64(e["embedding"])}
            for e in enr.data
        ]
        name, score, accepted = service.verify_multi(audio, speakers)
        return {"verified": accepted, "speaker_name": name, "score": round(float(score), 4)}
    except Exception as e:
        logger.warning("[converse] verify error (fail-open): %s", e)
        return {"verified": True, "reason": "error"}


DEVICE_CONV_TITLE = "🔊 Enceinte Aura"


def _persist_msg(user_token: str, conv_id: str, role: str, content: str, attachments=None):
    """Persiste UN message (commande ou réponse) dans conversation_messages.

    Source UNIQUE du chat affiché : la commande est persistée dès le STT (elle
    apparaît tout de suite), la réponse à la fin. Pas d'entrée 'live' qui
    apparaît/disparaît → zéro scintillement, zéro trou.
    """
    if not user_token or not conv_id or not content:
        return
    try:
        supabase = get_supabase_client(user_token)
        user_id = get_user_id(supabase, user_token)
        msg = {"conversation_id": conv_id, "user_id": user_id, "role": role, "content": content}
        if attachments and role == "assistant":
            msg["attachments"] = attachments
        supabase.table("conversation_messages").insert(msg).execute()
        supabase.table("conversations").update(
            {"updated_at": datetime.now(timezone.utc).isoformat()}
        ).eq("id", conv_id).execute()
    except Exception as e:
        logger.warning("[converse] persist msg (%s) error: %s", role, e)


def _uid_from_jwt(token: str) -> str | None:
    try:
        import base64
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("sub")
    except Exception:
        return None


def _push_status(user_token: str | None, **fields):
    """Met à jour device_status (task/response) pour l'affichage live front."""
    uid = _uid_from_jwt(user_token) if user_token else None
    if not uid:
        return
    try:
        row = {"user_id": uid, "updated_at": datetime.now(timezone.utc).isoformat()}
        row.update(fields)
        get_service_client().table("device_status").upsert(row).execute()
    except Exception as e:
        logger.debug("[converse] push status error: %s", e)


# ── Segmentation intelligente des conversations (par SUJET, pas par temps) ──
_TITLE_SYS = ("Donne un TITRE court (3 à 6 mots, sans guillemets ni ponctuation finale) "
              "résumant le sujet de ce message vocal. Réponds UNIQUEMENT le titre.")
_TOPIC_SYS = (
    "Tu décides si un NOUVEAU message vocal CONTINUE le sujet en cours, ou démarre un NOUVEAU sujet.\n"
    "Réponds 'CONTINUE' SEULEMENT si le message est LIÉ au dernier échange : même sujet, précision, "
    "suite logique, question de suivi.\n"
    "Réponds 'NOUVEAU: <titre 3-6 mots>' dès que le message change CLAIREMENT de thème — autre "
    "domaine, ou question sans rapport avec ce qui précède.\n"
    "Exemples :\n"
    "- on parlait de bases de données, puis « la coupe du monde » → NOUVEAU: Coupe du monde\n"
    "- on parlait de la météo à Lyon, puis « et demain ? » → CONTINUE\n"
    "Dans le doute, si le THÈME a changé → NOUVEAU. Une suite/précision sur le même thème → CONTINUE.\n"
    "IMPÉRATIF : réponds en UN SEUL MOT — soit exactement 'CONTINUE', soit 'NOUVEAU: <titre>'. "
    "AUCUNE justification, AUCUN markdown (pas de **), AUCUNE autre phrase."
)


def _fmt_gap(minutes: float) -> str:
    if minutes < 60:
        return f"il y a {int(minutes)} min"
    if minutes < 1440:
        return f"il y a {int(minutes / 60)}h"
    days = int(minutes / 1440)
    return "hier" if days == 1 else f"il y a {days} jours"


async def _haiku(system: str, user: str, max_tokens: int = 24) -> str:
    settings = get_settings()
    if not settings.ANTHROPIC_API_KEY:
        return ""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": settings.ANTHROPIC_API_KEY,
                         "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": "claude-haiku-4-5-20251001", "max_tokens": max_tokens,
                      "system": system, "messages": [{"role": "user", "content": user}]},
            )
        if r.status_code != 200:
            return ""
        return r.json()["content"][0]["text"].strip()
    except Exception:
        return ""


def _recent_conversation(user_token: str):
    """(conv_id, title, gap_minutes, recent_text) de la conversation la plus
    récente, ou None. recent_text = derniers échanges (pour juger la continuité)."""
    try:
        supabase = get_supabase_client(user_token)
        user_id = get_user_id(supabase, user_token)
        r = (supabase.table("conversations").select("id, title, updated_at")
             .eq("user_id", user_id).order("updated_at", desc=True).limit(1).execute())
        if not r.data:
            return None
        c = r.data[0]
        upd = datetime.fromisoformat((c["updated_at"] or "").replace("Z", "+00:00"))
        gap = (datetime.now(timezone.utc) - upd).total_seconds() / 60.0
        msgs = (supabase.table("conversation_messages").select("role, content")
                .eq("conversation_id", c["id"]).order("created_at", desc=True).limit(4).execute())
        recent = "\n".join(
            f"- {m['role']}: {(m.get('content') or '')[:150]}"
            for m in reversed(msgs.data or [])
        )
        return c["id"], (c.get("title") or ""), gap, recent
    except Exception as e:
        logger.warning("[conv] recent error: %s", e)
        return None


def _create_conversation(user_token: str, title: str):
    try:
        supabase = get_supabase_client(user_token)
        user_id = get_user_id(supabase, user_token)
        r = supabase.table("conversations").insert(
            {"user_id": user_id, "title": (title or "Conversation")[:80]}).execute()
        return r.data[0]["id"]
    except Exception as e:
        logger.warning("[conv] create error: %s", e)
        return None


async def _resolve_conversation(user_token: str | None, transcript: str) -> str | None:
    """Choisit la conversation cible : continue le SUJET en cours OU en crée une
    nouvelle. Critère = le SUJET (pas le temps) : une réunion peut durer des heures."""
    if not user_token:
        return None
    info = await asyncio.to_thread(_recent_conversation, user_token)
    if info:
        conv_id, title, gap, recent = info
        user_msg = (
            f"Conversation en cours « {title} » (dernière activité {_fmt_gap(gap)}) :\n"
            f"{recent}\n\nNOUVEAU message : « {transcript[:200]} »"
        )
        decision = await _haiku(_TOPIC_SYS, user_msg)
        logger.info("[conv] sujet: %r | dernier=« %s » | nouveau=%r",
                    (decision or "(vide)")[:40], title[:30], transcript[:40])
        # Parsing ROBUSTE : Haiku ajoute parfois du markdown (**CONTINUE**) ou des justifs.
        clean = (decision or "").strip().lstrip("*#>- ").upper()
        if not decision or clean.startswith("CONTINUE"):
            return conv_id   # fail-open = on continue (ne JAMAIS fragmenter à tort)
        # NOUVEAU: titre — 1ère ligne, après ':', sans markdown
        first = (decision or "").strip().splitlines()[0]
        new_title = (first.split(":", 1)[1].strip(" *#") if ":" in first else "")
        new_title = new_title or (await _haiku(_TITLE_SYS, transcript[:200])) or transcript[:40]
        logger.info("[conv] nouveau sujet → « %s »", new_title)
        return (await asyncio.to_thread(_create_conversation, user_token, new_title)) or conv_id
    # première conversation
    title = (await _haiku(_TITLE_SYS, transcript[:200])) or transcript[:40] or "Conversation"
    return await asyncio.to_thread(_create_conversation, user_token, title)


async def _run_agent(user_token, transcript, enriched, settings, conv_id=None, output_mode="voice"):
    """Consomme le flux SSE de l'agent : STREAME le texte + pousse l'outil en
    cours (animations front), renvoie (response_text, attachments).

    conv_id : continuité (l'agent charge l'historique).
    Fallback sur get_response si le stream échoue → ZÉRO régression.
    """
    response_text = ""
    attachments = None
    current_event = None
    try:
        async for line in llm_service.stream_response(
            command=transcript, context=[],
            agent_url=settings.AURA_AGENT_URL, agent_token=settings.AURA_AGENT_TOKEN,
            user_token=user_token, enriched_context=enriched, conversation_id=conv_id,
            output_mode=output_mode,
        ):
            line = line.rstrip("\n")
            if line.startswith("event: "):
                current_event = line[7:].strip()
            elif line.startswith("data: "):
                try:
                    data = json.loads(line[6:])
                except Exception:
                    continue
                if current_event == "text_delta":
                    response_text += data.get("delta", "")
                elif current_event == "tool_start":
                    logger.info("[converse] outil agent : %s", data.get("name"))
                    _push_status(user_token, task=data.get("name"))
                elif current_event == "done":
                    response_text = data.get("response") or response_text
                    attachments = data.get("attachments")
                elif current_event == "error":
                    raise RuntimeError("agent stream error")
        _push_status(user_token, task=None)
        if response_text.strip():
            return response_text, attachments
        raise ValueError("empty stream")
    except Exception as e:
        logger.warning("[converse] stream agent KO (%s) → fallback get_response", e)
        _push_status(user_token, task=None)
        result = await llm_service.get_response(
            command=transcript, context=[],
            agent_url=settings.AURA_AGENT_URL, agent_token=settings.AURA_AGENT_TOKEN,
            user_token=user_token, enriched_context=enriched, conversation_id=conv_id,
            output_mode=output_mode,
        )
        return (result.get("text") or ""), result.get("attachments")


def _strip_sources(text: str) -> str:
    """Retire la section « Sources » (liens) — pour la VOIX (TTS) uniquement.
    Le chat garde la version complète avec les liens cliquables."""
    import re
    return re.split(r"\n\s*\**\s*Sources\s*:", text, maxsplit=1, flags=re.IGNORECASE)[0].rstrip()


@router.post("/api/device/converse")
async def converse(
    raw_request: Request,
    audio: UploadFile = File(...),
    from_conversing: str = Form("false"),
    context: str = Form("[]"),
    tentative: str = Form("false"),
):
    settings = get_settings()
    user_token = _check_device(raw_request)
    from_conv = from_conversing.lower() in ("1", "true", "yes")
    is_tentative = tentative.lower() in ("1", "true", "yes")
    try:
        ambient_context = json.loads(context) if context else []
        if not isinstance(ambient_context, list):
            ambient_context = []
    except Exception:
        ambient_context = []

    if not settings.MISTRAL_API_KEY:
        raise HTTPException(status_code=500, detail="MISTRAL_API_KEY not configured")
    if not settings.AURA_AGENT_URL or not settings.AURA_AGENT_TOKEN:
        raise HTTPException(status_code=500, detail="AURA agent not configured")
    if not settings.ELEVENLABS_API_KEY or not settings.ELEVENLABS_VOICE_ID:
        raise HTTPException(status_code=500, detail="TTS not configured")

    raw = await audio.read()
    if len(raw) < 1000:
        raise HTTPException(status_code=400, detail="Audio too small")
    wav_data = raw if raw[:4] == b"RIFF" else pcm_to_wav(raw, sample_rate=16000)

    # ── 1. STT ──────────────────────────────────────────────────────
    _dur = max(0, (len(wav_data) - 44)) / 2 / 16000  # ~durée (PCM16 mono 16k)
    transcript = (await transcribe_audio(settings.MISTRAL_API_KEY, wav_data) or "").strip()
    logger.info("[converse] audio=%.1fs transcript=%r from_conv=%s", _dur, transcript[:80], from_conv)
    if not transcript:
        return JSONResponse({"status": "empty"})

    # ── 1bis. Complétude (endpointing sémantique) ───────────────────
    # Si le device est en mode "tentative" (il a détecté une pause mais n'est
    # pas sûr que la phrase soit finie), on vérifie : pause de réflexion ou fin ?
    if is_tentative and not await _is_complete(transcript):
        logger.info("[converse] phrase incomplète → continue d'écouter: %r", transcript[:60])
        return JSONResponse({"status": "incomplete", "transcript": transcript})

    # ── 2. Intent (seulement depuis conversing) ─────────────────────
    # On ne REJETTE que si Haiku est CONFIANT que ce n'est pas pour Aura.
    # Dans le doute → on répond (mieux vaut répondre que d'ignorer une vraie demande).
    if from_conv:
        intent = await classify_intent(transcript, ambient_context)
        if not intent.get("directed", True) and intent.get("confidence", 0.0) >= settings.INTENT_CONFIDENCE:
            logger.info("[converse] not directed at Aura (conf=%.2f ≥ %.2f) → skip",
                        intent.get("confidence", 0.0), settings.INTENT_CONFIDENCE)
            return JSONResponse({"status": "not_directed", "transcript": transcript})

    # Efface le badge locuteur du tour PRÉCÉDENT : sinon l'ancien nom/score reste
    # affiché tant que la vérif du locuteur ACTUEL n'a pas fini (~1s) → décalage.
    _push_status(user_token, speaker=None, verified=None, speaker_score=None)

    # ── 3+4. Speaker verify ∥ LLM EN PARALLÈLE (latence) ────────────
    # La vérif locuteur (réseau + ONNX) tourne EN MÊME TEMPS que le LLM. Sur le
    # chemin nominal (accepté), son coût disparaît dans l'ombre du LLM.
    conv_id = await _resolve_conversation(user_token, transcript)  # conversation par sujet
    # Persiste la COMMANDE TOUT DE SUITE → elle s'affiche instantanément (source unique)
    threading.Thread(target=_persist_msg, args=(user_token, conv_id, "user", transcript), daemon=True).start()
    # RAG : récupère les souvenirs PERTINENTS (par sens) ∥ la vérif locuteur
    verify_task = asyncio.create_task(asyncio.to_thread(_verify_speaker, user_token, wav_data))
    mem_task = asyncio.create_task(asyncio.to_thread(memory_service.retrieve, user_token, transcript))
    memories = await mem_task
    parts = list(ambient_context) if ambient_context else []
    if memories:
        parts.append(memories)
    enriched = "\n".join(parts) if parts else None
    agent_task = asyncio.create_task(_run_agent(user_token, transcript, enriched, settings, conv_id))

    verify = await verify_task
    rejected = not verify.get("verified", True) and verify.get("reason") != "no_enrollments"
    if rejected and settings.VERIFY_SPEAKER_ENFORCE:
        logger.info("[converse] speaker rejected (enforce): %s", verify)
        agent_task.cancel()
        try:
            await agent_task
        except BaseException:
            pass
        _push_status(user_token, task=None, response=None,
                     speaker=verify.get("speaker_name"), verified=False,
                     speaker_score=verify.get("score"))
        return JSONResponse({
            "status": "rejected",
            "transcript": transcript,
            "speaker_name": verify.get("speaker_name"),
            "score": verify.get("score"),
        })
    if rejected:
        logger.info("[converse] locuteur non reconnu (%s, %.2f) mais on répond (verif non bloquante)",
                    verify.get("speaker_name"), verify.get("score") or 0.0)

    # Badge de vérification locuteur → front (parité avec l'ancien web : ✓/✗ nom + score)
    if verify.get("speaker_name"):
        _push_status(user_token, speaker=verify.get("speaker_name"),
                     verified=bool(verify.get("verified")),
                     speaker_score=verify.get("score"))

    response_text, attachments = await agent_task
    response_text = (response_text or "").strip()
    logger.info("[converse] response=%r attachments=%s", response_text[:80], bool(attachments))
    _push_status(user_token, task=None)   # fin de l'agent → efface l'animation d'outil
    if not response_text:
        return JSONResponse({"status": "empty_response", "transcript": transcript})

    # ── Persiste la RÉPONSE (source unique) → s'affiche et RESTE ────
    threading.Thread(
        target=_persist_msg,
        args=(user_token, conv_id, "assistant", response_text, attachments),
        daemon=True,
    ).start()

    # ── 5. TTS → MP3 ────────────────────────────────────────────────
    headers = {
        "X-Status": "ok",
        "X-Transcript": quote(transcript),
        "X-Response": quote(response_text),
        "X-Speaker": quote(verify.get("speaker_name") or ""),
        "Access-Control-Expose-Headers": "X-Status, X-Transcript, X-Response, X-Speaker",
    }
    # La voix ne lit PAS la section « Sources » (liens) — affichée dans le chat seulement.
    voice_text = _strip_sources(response_text)
    return StreamingResponse(
        stream_tts(text=voice_text, voice_id=settings.ELEVENLABS_VOICE_ID, api_key=settings.ELEVENLABS_API_KEY),
        media_type="audio/mpeg",
        headers=headers,
    )


@router.post("/api/web/chat")
async def web_chat(raw_request: Request):
    """Chat web/mobile (sans enceinte) : écrire un message dans une conversation.

    Même cerveau que l'enceinte : segmentation par sujet, RAG (mémoire), agent
    avec continuité (conversation_id), persistance → realtime. Auth = JWT user.
    Body: { message, conversation_id? }
    """
    auth = raw_request.headers.get("authorization", "")
    user_token = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else None
    if not user_token:
        raise HTTPException(status_code=401, detail="login required")
    body = await raw_request.json()
    message = (body.get("message") or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="message required")
    settings = get_settings()

    # conversation cible : fournie (vue web) ou résolue intelligemment (sujet)
    conv_id = body.get("conversation_id") or await _resolve_conversation(user_token, message)
    # commande utilisateur persistée tout de suite (s'affiche en realtime)
    threading.Thread(target=_persist_msg, args=(user_token, conv_id, "user", message), daemon=True).start()

    memories = await asyncio.to_thread(memory_service.retrieve, user_token, message)
    enriched = memories or None
    response_text, attachments = await _run_agent(
        user_token, message, enriched, settings, conv_id, output_mode="chat")
    response_text = (response_text or "").strip()
    if response_text:
        threading.Thread(
            target=_persist_msg,
            args=(user_token, conv_id, "assistant", response_text, attachments),
            daemon=True,
        ).start()
    return {"response": response_text, "attachments": attachments, "conversation_id": conv_id}


@router.post("/api/web/device-mute")
async def web_device_mute(raw_request: Request):
    """Mute logiciel à distance (mode confidentiel) depuis le web/mobile.
    Écrit device_status.muted ; le device le lit (poll) et arrête micro+ambiant.
    Auth = JWT user. Body: { muted: bool }. NB: mute logiciel, pas coupure matérielle.
    """
    auth = raw_request.headers.get("authorization", "")
    user_token = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else None
    if not user_token:
        raise HTTPException(status_code=401, detail="login required")
    body = await raw_request.json()
    muted = bool(body.get("muted"))
    try:
        supabase = get_supabase_client(user_token)
        user_id = get_user_id(supabase, user_token)
        supabase.table("device_status").upsert({
            "user_id": user_id,
            "muted": muted,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
        logger.info("[web] mute=%s", muted)
        return {"ok": True, "muted": muted}
    except Exception as e:
        logger.warning("[web] device-mute error: %s", e)
        raise HTTPException(status_code=500, detail="mute failed")


@router.get("/api/device/control")
async def device_control(raw_request: Request):
    """Le device lit son contrôle distant (mute + demande d'enrôlement). Auth = DEVICE_TOKEN.
    Polled ~2s. enroll_request = {id, name, requested_at} ou null."""
    user_token = _check_device(raw_request)
    if not user_token:
        return {"muted": False, "enroll_request": None}
    try:
        supabase = get_supabase_client(user_token)
        user_id = get_user_id(supabase, user_token)
        r = (supabase.table("device_status").select("muted, enroll_request")
             .eq("user_id", user_id).limit(1).execute())
        row = r.data[0] if r.data else {}
        return {"muted": bool(row.get("muted")), "enroll_request": row.get("enroll_request")}
    except Exception as e:
        logger.warning("[device] control read error: %s", e)
        return {"muted": False, "enroll_request": None}


@router.post("/api/device/state")
async def device_state(
    raw_request: Request,
    state: str = Form(...),
    transcript: str = Form(""),
    seq: str = Form("0"),
):
    """Reçoit l'état courant de l'enceinte (IDLE/LISTENING/THINKING/SPEAKING…)
    et l'écrit dans Supabase pour l'affichage EN DIRECT côté app/web.

    seq monotone : on ignore un état arrivé EN RETARD (anti-désordre réseau).
    """
    user_token = _check_device(raw_request)
    if not user_token:
        return {"ok": False, "reason": "no_token"}
    seq_i = int(seq) if seq.isdigit() else 0
    try:
        supabase = get_supabase_client(user_token)
        user_id = get_user_id(supabase, user_token)
        # L'ordre est déjà garanti côté device (push sérialisé) → upsert direct,
        # AUCUN SELECT préalable (latence minimale). seq stocké pour info/ordre.
        supabase.table("device_status").upsert({
            "user_id": user_id,
            "state": state,
            "transcript": transcript or None,
            "seq": seq_i,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
        return {"ok": True}
    except Exception as e:
        logger.warning("[device] state upsert error: %s", e)
        return {"ok": False, "reason": "error"}


def _recent_ambient(user_token: str | None, limit: int = 6) -> list[str]:
    """Derniers segments ambiants (device_status.ambient) → contexte pour l'agent et le
    gating intent du chemin streaming (parité avec converse, qui les reçoit du device)."""
    if not user_token:
        return []
    try:
        supabase = get_supabase_client(user_token)
        user_id = get_user_id(supabase, user_token)
        r = (supabase.table("device_status").select("ambient")
             .eq("user_id", user_id).limit(1).execute())
        ambient = (r.data[0].get("ambient") or []) if r.data else []
        texts = [a.get("text", "") for a in ambient[-limit:] if a.get("text")]
        # ÉTIQUETTE clairement comme BRUIT DE FOND : sans ça l'agent prend la conversation
        # entendue autour comme si c'était la commande → il dérive sur des sujets hors-sujet.
        return [f"[Ambiant entendu autour — NE PAS répondre à ceci, juste contexte] {t}"
                for t in texts]
    except Exception as e:
        logger.debug("[device] recent ambient error: %s", e)
        return []


def _persist_ambient(user_token: str, text: str):
    """Ajoute un segment ambiant à device_status.ambient (borné aux 12 derniers)."""
    if not user_token or not text:
        return
    try:
        supabase = get_supabase_client(user_token)
        user_id = get_user_id(supabase, user_token)
        row = (
            supabase.table("device_status").select("ambient")
            .eq("user_id", user_id).limit(1).execute()
        )
        ambient = (row.data[0].get("ambient") or []) if row.data else []
        ambient = (ambient + [{"text": text, "ts": datetime.now(timezone.utc).isoformat()}])[-12:]
        supabase.table("device_status").upsert({
            "user_id": user_id,
            "ambient": ambient,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception as e:
        logger.warning("[device] ambient persist error: %s", e)


def _persist_ambient_memory(user_token: str, text: str):
    """Accumule l'ambiant dans une TRANSCRIPTION JOURNALIÈRE (1 ligne/jour/user).

    C'est la table que l'agent interroge (get_recent_context / search_memory) →
    Aura peut alors résumer « mes réunions d'hier » : elle retrouve la ligne du
    jour par date et la résume. Sans ça, l'ambiant restait éphémère.
    """
    if not user_token or not text:
        return
    try:
        supabase = get_supabase_client(user_token)
        user_id = get_user_id(supabase, user_token)
        today = datetime.now(timezone.utc).date().isoformat()
        marker = f"ambient-{today}"   # 1 transcription par jour, repérée par ce nom
        existing = (
            supabase.table("transcriptions").select("id, transcription_text")
            .eq("user_id", user_id).eq("audio_filename", marker).limit(1).execute()
        )
        now = datetime.now(timezone.utc).isoformat()
        if existing.data:
            prev = existing.data[0].get("transcription_text") or ""
            new_text = (prev + "\n" + text)[-60000:]   # cap (garde la fin)
            supabase.table("transcriptions").update(
                {"transcription_text": new_text, "updated_at": now}
            ).eq("id", existing.data[0]["id"]).execute()
        else:
            supabase.table("transcriptions").insert({
                "user_id": user_id,
                "audio_filename": marker,
                "language": "fr",
                "transcription_text": text,
                "summary": {"title": f"Contexte ambiant du {today}"},
            }).execute()
    except Exception as e:
        logger.warning("[device] ambient memory persist error: %s", e)


@router.post("/api/device/transcribe")
async def transcribe(raw_request: Request, audio: UploadFile = File(...)):
    """Transcription simple pour le contexte ambiant (batch passif du device)."""
    user_token = _check_device(raw_request)
    settings = get_settings()
    if not settings.MISTRAL_API_KEY:
        raise HTTPException(status_code=500, detail="MISTRAL_API_KEY not configured")
    raw = await audio.read()
    if len(raw) < 1000:
        return {"text": ""}
    wav_data = raw if raw[:4] == b"RIFF" else pcm_to_wav(raw, sample_rate=16000)
    text = (await transcribe_audio(settings.MISTRAL_API_KEY, wav_data) or "").strip()
    if text and user_token:
        # 1) affichage live (12 derniers)  2) mémoire datée (transcription du jour)
        # 3) mémoire VECTORIELLE (RAG : recherche sémantique par l'agent)
        threading.Thread(target=_persist_ambient, args=(user_token, text), daemon=True).start()
        threading.Thread(target=_persist_ambient_memory, args=(user_token, text), daemon=True).start()
        threading.Thread(target=memory_service.persist_chunk, args=(user_token, text, "ambient"), daemon=True).start()
    return {"text": text}


@router.get("/api/device/speakers/embeddings")
async def device_speaker_embeddings(raw_request: Request):
    """Renvoie les empreintes vocales enrôlées de l'utilisateur (base64).

    Le device les met en cache pour faire l'endpointing par locuteur cible
    EN LOCAL (décider en temps réel si c'est bien l'utilisateur qui parle),
    sans round-trip réseau pendant la prise de commande.
    """
    user_token = _check_device(raw_request)
    if not user_token:
        return {"embeddings": []}
    try:
        supabase = get_supabase_client(user_token)
        user_id = get_user_id(supabase, user_token)
        enr = (
            supabase.table("speaker_enrollments")
            .select("speaker_name, embedding")
            .eq("user_id", user_id)
            .execute()
        )
        return {
            "embeddings": [
                {"name": e["speaker_name"], "embedding_b64": e["embedding"]}
                for e in (enr.data or [])
            ]
        }
    except Exception as e:
        logger.warning("[device] embeddings fetch error: %s", e)
        return {"embeddings": []}
