"""Proxy WebSocket temps réel : device ↔ backend ↔ Deepgram Flux (chemin B).

Le device (Pi) streame du PCM 16k. Le backend relaie vers **Deepgram Flux** (STT +
détection de tour INTÉGRÉE, clé côté serveur). C'est FLUX qui décide la fin de tour
(EndOfTurn) — il tolère les hésitations/pauses (« euh… »), là où le silence fixe
fragmentait. À EndOfTurn → transcript final → intent → LLM → TTS streamé (ElevenLabs).
Respecte « aucune clé sur l'appareil » : le device ne parle QU'À ce backend.

Device → backend : frames BINAIRES = PCM 16k ; {"type":"cancel"}.
Backend → device : {"type":"partial","text"} (Update, au fil de l'eau),
  {"type":"turn_end","transcript"} (EndOfTurn → arrête de streamer), {"type":"response"},
  audio (frames MP3 binaires du TTS), {"type":"audio_end"}.
"""
import asyncio
import json
import logging
import threading

import websockets
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.config import get_settings
from app.routes.device_pairing import user_token_from_device
from app.routes.device_converse import (
    _resolve_conversation, _persist_msg, _run_agent, _strip_sources,
)
from app.services import memory_service
from app.services.tts_service import stream_tts

logger = logging.getLogger(__name__)
router = APIRouter()


def _flux_url(settings) -> str:
    return (
        "wss://api.deepgram.com/v2/listen"
        "?model=flux-general-multi&language_hint=fr"
        "&encoding=linear16&sample_rate=16000"
        f"&eot_threshold={settings.FLUX_EOT_THRESHOLD}"
        "&eot_timeout_ms=5000"
    )


async def _connect_flux(api_key: str, url: str):
    hdr = {"Authorization": f"Token {api_key}"}
    try:
        return await websockets.connect(url, additional_headers=hdr)
    except TypeError:   # websockets < 13
        return await websockets.connect(url, extra_headers=hdr)


async def _run_eot_pipeline(ws, user_token, transcript, settings):
    """EOT → LLM → TTS, streamé sur le WS device. Réutilise la logique converse.
    Envoie {"type":"response"} puis le MP3 en binaire puis {"type":"audio_end"}."""
    conv_id = await _resolve_conversation(user_token, transcript)
    threading.Thread(target=_persist_msg, args=(user_token, conv_id, "user", transcript), daemon=True).start()
    memories = await asyncio.to_thread(memory_service.retrieve, user_token, transcript)
    enriched = memories or None
    response_text, attachments = await _run_agent(user_token, transcript, enriched, settings, conv_id)
    response_text = (response_text or "").strip()
    if not response_text:
        await ws.send_json({"type": "final", "transcript": transcript, "response": ""})
        return
    threading.Thread(target=_persist_msg,
                     args=(user_token, conv_id, "assistant", response_text, attachments),
                     daemon=True).start()
    await ws.send_json({"type": "response", "transcript": transcript,
                        "text": response_text, "conversation_id": conv_id})
    voice_text = _strip_sources(response_text)
    try:
        async for mp3 in stream_tts(text=voice_text, voice_id=settings.ELEVENLABS_VOICE_ID,
                                    api_key=settings.ELEVENLABS_API_KEY):
            await ws.send_bytes(mp3)
    except Exception as e:
        logger.warning("[stream] TTS error: %s", e)
    await ws.send_json({"type": "audio_end"})


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

    try:
        flux = await _connect_flux(settings.DEEPGRAM_API_KEY, _flux_url(settings))
    except Exception as e:
        logger.error("[stream] Flux connect KO: %s", e)
        await ws.send_json({"type": "error", "error": "stt_connect"})
        await ws.close()
        return

    done = asyncio.Event()

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
                    if transcript:
                        await _run_eot_pipeline(ws, user_token, transcript, settings)
                    else:
                        await ws.send_json({"type": "final", "transcript": ""})
                    break                          # 1 tour par connexion
        except Exception:
            pass
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
                        if json.loads(txt).get("type") == "cancel":
                            break
                    except Exception:
                        pass
        except Exception:
            pass
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
