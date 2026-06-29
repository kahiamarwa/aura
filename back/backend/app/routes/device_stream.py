"""Proxy WebSocket temps réel : device ↔ backend ↔ Deepgram Flux (chemin B).

Le device (Pi) streame du PCM 16k. Le backend relaie vers **Deepgram Flux** (STT +
détection de tour INTÉGRÉE, clé côté serveur). C'est FLUX qui décide la fin de tour
(EndOfTurn) — il tolère les hésitations/pauses (« euh… »), là où le silence fixe
fragmentait. À EndOfTurn → transcript final → (gating intent si follow-up) → LLM streamé
→ TTS ElevenLabs PHRASE PAR PHRASE (Aura parle dès la 1ère phrase). Respecte « aucune clé
sur l'appareil » : le device ne parle QU'À ce backend.

Device → backend : frames BINAIRES = PCM 16k ; {"type":"cancel"} ; query ?from_conversing=1.
Backend → device : {"type":"partial","text"} (Update au fil de l'eau),
  {"type":"turn_end","transcript"} (EndOfTurn → arrête de streamer), {"type":"response"},
  audio (frames MP3 binaires du TTS), {"type":"audio_end"}, {"type":"final"}, {"type":"error"}.
"""
import asyncio
import json
import logging
import re
import threading

import websockets
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.config import get_settings
from app.routes.device_pairing import user_token_from_device
from app.routes.device_converse import (
    _resolve_conversation, _persist_msg, _strip_sources, _push_status, _recent_ambient,
)
from app.routes.intent_classifier import classify_intent
from app.services import llm_service, memory_service
from app.services.tts_service import stream_tts

logger = logging.getLogger(__name__)
router = APIRouter()

# Frontière de phrase pour le TTS incrémental (I2) : ponctuation forte (+ guillemet/parenthèse
# fermante éventuel) suivie d'un espace, OU retour à la ligne.
_SENT_BOUNDARY = re.compile(r'[.!?:]["»)]?\s|\n')

# NB : la VÉRIF LOCUTEUR (ECAPA) est faite EN LOCAL SUR LE PI (zéro steal, instantané) et
# envoyée via {type:"speaker"}. Le backend ne fait plus d'ECAPA → CPU libéré.


def _flux_url(settings) -> str:
    return (
        "wss://api.deepgram.com/v2/listen"
        "?model=flux-general-multi&language_hint=fr"
        "&encoding=linear16&sample_rate=16000"
        f"&eot_threshold={settings.FLUX_EOT_THRESHOLD}"
        f"&eot_timeout_ms={settings.FLUX_EOT_TIMEOUT_MS}"   # I8 : configurable
    )


async def _connect_flux(api_key: str, url: str):
    hdr = {"Authorization": f"Token {api_key}"}
    try:
        return await websockets.connect(url, additional_headers=hdr)
    except TypeError:   # websockets < 13
        return await websockets.connect(url, extra_headers=hdr)


async def _run_eot_pipeline(ws, user_token, transcript, settings, from_conversing=False, verify=None):
    """EndOfTurn → (gating intent) → LLM streamé → TTS PHRASE PAR PHRASE → MP3 au device.

    Appelé UNIQUEMENT si le locuteur est autorisé — la VÉRIF est faite EN LOCAL SUR LE PI
    (ECAPA, zéro steal) et reçue via {type:"speaker"} ; le backend ne fait plus d'ECAPA.
    Le LLM (producteur) et le TTS (consommateur) tournent en parallèle via une file de phrases
    (I2). Conversation ∥ mémoire en parallèle (I3). Gating intent sur les follow-ups (I5).
    Badge locuteur = résultat reçu du device (I6)."""
    # KEEPALIVE : tant que le tour n'est pas fini, on ping le device toutes les ~10s
    # (« je réfléchis »). Il ne timeout JAMAIS sur une tâche LONGUE (rapport, recherche),
    # tout en coupant si le backend meurt vraiment. TOUS les envois device passent par _send
    # (verrou) pour ne pas entremêler le ping et le flux MP3.
    ws_lock = asyncio.Lock()
    keepalive_stop = asyncio.Event()

    async def _send(payload=None, raw=None):
        async with ws_lock:
            if raw is not None:
                await ws.send_bytes(raw)
            else:
                await ws.send_json(payload)

    async def _keepalive():
        while not keepalive_stop.is_set():
            try:
                await asyncio.wait_for(keepalive_stop.wait(), timeout=10.0)
            except asyncio.TimeoutError:
                try:
                    await _send({"type": "thinking"})
                except Exception:
                    return

    ka_task = asyncio.create_task(_keepalive())

    # Contexte ambiant : 1 requête, réutilisée pour le gating ET l'agent (parité converse, I5).
    ambient = await asyncio.to_thread(_recent_ambient, user_token)

    # I5 : sur un follow-up, ne réponds pas à une phrase « pas pour Aura ».
    if from_conversing:
        try:
            intent = await classify_intent(transcript, ambient)
        except Exception:
            intent = {"directed": True}
        if not intent.get("directed", True) and intent.get("confidence", 0.0) >= settings.INTENT_CONFIDENCE:
            logger.info("[stream] not directed (conf=%.2f) → skip", intent.get("confidence", 0.0))
            keepalive_stop.set()
            await _send({"type": "final", "transcript": transcript, "status": "not_directed"})
            return

    # Badge locuteur = résultat reçu du DEVICE (vérif ECAPA faite sur le Pi). I6 : plus de
    # badge périmé collé.
    verify = verify or {}
    if verify.get("name"):
        _push_status(user_token, speaker=verify.get("name"),
                     verified=bool(verify.get("verified", True)), speaker_score=verify.get("score"))
    else:
        _push_status(user_token, speaker=None, verified=None, speaker_score=None)

    # I3 : conversation (Haiku) ∥ mémoire (RAG) en parallèle.
    conv_task = asyncio.create_task(_resolve_conversation(user_token, transcript))
    mem_task = asyncio.create_task(asyncio.to_thread(memory_service.retrieve, user_token, transcript))
    conv_id = await conv_task
    memories = await mem_task
    threading.Thread(target=_persist_msg, args=(user_token, conv_id, "user", transcript), daemon=True).start()
    parts = list(ambient) if ambient else []
    if memories:
        parts.append(memories)
    enriched = "\n".join(parts) if parts else None

    # ── LLM streamé (producteur) + TTS phrase par phrase (consommateur) ──
    sentence_q: asyncio.Queue = asyncio.Queue()
    state = {"full_text": "", "attachments": None, "voice_spoken": 0}
    spoke_response = False
    nbytes = 0

    def _pending_sentences(final: bool):
        """Phrases COMPLÈTES (et le reste si final) du texte VOIX accumulé, depuis le
        dernier index parlé. _strip_sources retire la section « Sources » (non lue à voix)."""
        voice = _strip_sources(state["full_text"])
        out, sp = [], state["voice_spoken"]
        while True:
            m = _SENT_BOUNDARY.search(voice, sp)
            if not m:
                break
            out.append(voice[sp:m.end()])
            sp = m.end()
        if final:
            tail = voice[sp:]
            if tail.strip():
                out.append(tail)
            sp = len(voice)
        state["voice_spoken"] = sp
        return out

    async def _speak(segment: str):
        nonlocal spoke_response, nbytes
        seg = segment.strip()
        if not seg:
            return
        if not spoke_response:
            await _send({"type": "response", "transcript": transcript,
                         "text": seg, "conversation_id": conv_id})
            spoke_response = True
        try:
            async for mp3 in stream_tts(text=seg, voice_id=settings.ELEVENLABS_VOICE_ID,
                                        api_key=settings.ELEVENLABS_API_KEY):
                nbytes += len(mp3)
                await _send(raw=mp3)
        except Exception as e:
            logger.warning("[stream] TTS error: %s", e)

    async def producer():
        try:
            current_event = None
            async for line in llm_service.stream_response(
                    command=transcript, context=[],
                    agent_url=settings.AURA_AGENT_URL, agent_token=settings.AURA_AGENT_TOKEN,
                    user_token=user_token, enriched_context=enriched, conversation_id=conv_id,
                    output_mode="voice"):
                line = line.rstrip("\n")
                if line.startswith("event: "):
                    current_event = line[7:].strip()
                elif line.startswith("data: "):
                    try:
                        data = json.loads(line[6:])
                    except Exception:
                        continue
                    if current_event == "text_delta":
                        state["full_text"] += data.get("delta", "")
                        for s in _pending_sentences(final=False):
                            await sentence_q.put(s)
                    elif current_event == "tool_start":
                        _push_status(user_token, task=data.get("name"))
                    elif current_event == "done":
                        state["full_text"] = data.get("response") or state["full_text"]
                        state["attachments"] = data.get("attachments")
                    elif current_event == "error":
                        raise RuntimeError("agent stream error")
            _push_status(user_token, task=None)
        except Exception as e:
            logger.warning("[stream] stream agent KO (%s) → fallback get_response", e, exc_info=True)
            _push_status(user_token, task=None)
            try:
                result = await llm_service.get_response(
                    command=transcript, context=[],
                    agent_url=settings.AURA_AGENT_URL, agent_token=settings.AURA_AGENT_TOKEN,
                    user_token=user_token, enriched_context=enriched, conversation_id=conv_id,
                    output_mode="voice")
                state["full_text"] = result.get("text") or state["full_text"]
                state["attachments"] = result.get("attachments")
            except Exception as e2:
                logger.error("[stream] agent KO total: %s", e2, exc_info=True)
        finally:
            for s in _pending_sentences(final=True):   # dernière phrase + tail
                await sentence_q.put(s)
            await sentence_q.put(None)                  # sentinelle de fin

    async def consumer():
        while True:
            seg = await sentence_q.get()
            if seg is None:
                break
            await _speak(seg)

    await asyncio.gather(producer(), consumer())

    keepalive_stop.set()                       # le tour parle/se termine → stop le ping
    full_text = (state["full_text"] or "").strip()
    if not spoke_response:
        if not full_text:
            await _send({"type": "final", "transcript": transcript, "status": "empty"})
            return
        # texte présent mais jamais « parlé » (ex : que des sources) → on l'envoie quand même
        await _send({"type": "response", "transcript": transcript,
                     "text": full_text, "conversation_id": conv_id})

    threading.Thread(target=_persist_msg,
                     args=(user_token, conv_id, "assistant", full_text, state["attachments"]),
                     daemon=True).start()
    await _send({"type": "audio_end"})
    logger.info("[stream] tour terminé (%d octets MP3, %d car.)", nbytes, len(full_text))


@router.websocket("/api/device-stream")
async def device_stream(ws: WebSocket):
    await ws.accept()
    settings = get_settings()

    device_token = (ws.headers.get("x-device-token")
                    or ws.query_params.get("device_token") or "")
    user_token = user_token_from_device(device_token)
    if not user_token:
        await ws.send_json({"type": "error", "error": "auth"})
        await ws.close(code=4401)
        return
    if not settings.DEEPGRAM_API_KEY:
        await ws.send_json({"type": "error", "error": "stt_unconfigured"})
        await ws.close()
        return
    from_conversing = str(ws.query_params.get("from_conversing", "")).lower() in ("1", "true", "yes")

    try:
        flux = await _connect_flux(settings.DEEPGRAM_API_KEY, _flux_url(settings))
    except Exception as e:
        logger.error("[stream] Flux connect KO: %s", e)
        await ws.send_json({"type": "error", "error": "stt_connect"})
        await ws.close()
        return

    done = asyncio.Event()
    speaker_future: asyncio.Future = asyncio.Future()   # verdict vérif locuteur (fait sur le Pi)

    async def flux_to_device():
        try:
            async for raw in flux:
                if isinstance(raw, (bytes, bytearray)):
                    continue                       # l'audio entrant Flux est ignoré
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                if msg.get("type") != "TurnInfo":
                    continue
                ev = msg.get("event")
                if ev == "Update":
                    await ws.send_json({"type": "partial", "text": msg.get("transcript", "")})
                elif ev == "EndOfTurn":
                    transcript = (msg.get("transcript") or "").strip()
                    logger.info("[stream] EndOfTurn conf=%.2f → %r",
                                msg.get("end_of_turn_confidence", 0.0), transcript[:80])
                    await ws.send_json({"type": "turn_end", "transcript": transcript})
                    if not transcript:
                        await ws.send_json({"type": "final", "transcript": ""})
                        break
                    # VÉRIF LOCUTEUR faite SUR LE PI (ECAPA local, zéro steal) → on attend
                    # son verdict via {type:"speaker"} (~1-2s). Le backend ne fait plus d'ECAPA.
                    if settings.STREAM_VERIFY_ENFORCE:
                        try:
                            verify = await asyncio.wait_for(speaker_future, timeout=10.0)
                        except asyncio.TimeoutError:
                            verify = {"verified": True, "name": None, "score": 0.0}  # fail-open
                    else:
                        verify = {"verified": True, "name": None, "score": 0.0}
                    if not verify.get("verified", True):
                        logger.info("[stream] locuteur NON autorisé (%s, score=%.2f) → aucune réponse",
                                    verify.get("name"), verify.get("score") or 0.0)
                        _push_status(user_token, speaker=verify.get("name"),
                                     verified=False, speaker_score=verify.get("score"))
                        await ws.send_json({"type": "rejected", "transcript": transcript,
                                            "speaker": verify.get("name"), "score": verify.get("score")})
                        break
                    try:
                        await _run_eot_pipeline(ws, user_token, transcript, settings,
                                                from_conversing, verify)
                    except Exception as e:   # I1/I7 : pas de 30s de silence
                        logger.error("[stream] pipeline KO: %s", e, exc_info=True)
                        try:
                            await ws.send_json({"type": "error", "error": "pipeline"})
                        except Exception:
                            pass
                    break                          # 1 tour par connexion
        except Exception as e:                     # I7 : ne plus avaler en silence
            logger.warning("[stream] flux_to_device: %s", e, exc_info=True)
        finally:
            done.set()

    async def device_to_flux():
        try:
            while not done.is_set():
                data = await ws.receive()
                if data.get("type") == "websocket.disconnect":
                    break
                chunk = data.get("bytes")
                if chunk:
                    await flux.send(chunk)         # PCM 16k binaire BRUT (pas de base64)
                    continue
                txt = data.get("text")
                if txt:
                    try:
                        msg = json.loads(txt)
                    except Exception:
                        continue
                    if msg.get("type") == "cancel":
                        break
                    # Verdict de la vérif locuteur faite sur le Pi → débloque le gate.
                    if msg.get("type") == "speaker" and not speaker_future.done():
                        speaker_future.set_result({
                            "verified": msg.get("verified", True),
                            "name": msg.get("name") or None,
                            "score": msg.get("score", 0.0),
                        })
        except Exception as e:                     # I7 : déconnexion = normal → debug
            logger.debug("[stream] device_to_flux: %s", e)
        finally:
            done.set()

    t1 = asyncio.create_task(flux_to_device())
    t2 = asyncio.create_task(device_to_flux())
    try:
        await done.wait()
    except (WebSocketDisconnect, Exception) as e:
        logger.debug("[stream] %s", e)
    finally:
        for t in (t1, t2):
            t.cancel()
        try:
            await flux.close()
        except Exception:
            pass
