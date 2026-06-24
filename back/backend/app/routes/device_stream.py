"""Proxy WebSocket temps réel : device ↔ backend ↔ ElevenLabs Scribe v2 realtime.

Chemin B (turn-taking façon Alexa). Le device (Pi) :
  - streame du PCM 16k au fil de l'eau,
  - décide LOCALEMENT la fin de tour (Smart Turn v3, sur le Pi),
  - signale l'EOT au backend.
Le backend relaie l'audio vers ElevenLabs (clé CÔTÉ SERVEUR) et renvoie les
transcripts partiels. À l'EOT, on fige le transcript → pipeline existant (intent →
LLM → TTS, étape 1b). Respecte « aucune clé sur l'appareil » : le device ne parle
QU'À ce backend (corrige aussi la fuite directe vers ElevenLabs côté web).

Protocole device → backend :
  - frames BINAIRES         = PCM 16-bit 16 kHz mono (audio au fil de l'eau)
  - {"type":"eot"}          = fin de tour (Smart Turn local a décidé)
  - {"type":"cancel"}       = abandon (« Stop Aura » / silence)
Protocole backend → device :
  - {"type":"partial","text":…}    transcript mutable (affichage live)
  - {"type":"committed","text":…}  transcript figé
  - {"type":"final","transcript":…}  après EOT (LLM/TTS = étape 1b)

NOTE : le format EXACT des messages ElevenLabs Scribe realtime est à valider sur
l'API live (input_audio_chunk / partial_transcript / committed_transcript).
"""
import asyncio
import base64
import json
import logging

import websockets
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.config import get_settings
from app.routes.device_pairing import user_token_from_device

logger = logging.getLogger(__name__)
router = APIRouter()

_ELEVEN_WS = ("wss://api.elevenlabs.io/v1/speech-to-text/realtime"
              "?model_id=scribe_v2_realtime&commit_strategy=manual")
# Chunk vide qui force le commit (= fin de tour) côté ElevenLabs.
_SILENCE_B64 = base64.b64encode(b"\x00" * 320).decode()


async def _connect_eleven(api_key: str):
    """WS client vers ElevenLabs (header xi-api-key). Compatible websockets 12/13+."""
    hdr = {"xi-api-key": api_key}
    try:
        return await websockets.connect(_ELEVEN_WS, additional_headers=hdr)
    except TypeError:  # websockets < 13 : extra_headers
        return await websockets.connect(_ELEVEN_WS, extra_headers=hdr)


@router.websocket("/api/device-stream")
async def device_stream(ws: WebSocket):
    await ws.accept()
    settings = get_settings()

    # ── Auth : le device présente son DEVICE_TOKEN (header ou query) ──
    device_token = (ws.headers.get("x-device-token")
                    or ws.query_params.get("device_token") or "")
    user_token = user_token_from_device(device_token)
    if not user_token:
        await ws.send_json({"type": "error", "error": "auth"})
        await ws.close(code=4401)
        return
    if not settings.ELEVENLABS_API_KEY:
        await ws.send_json({"type": "error", "error": "stt_unconfigured"})
        await ws.close()
        return

    # ── Connexion ElevenLabs Scribe realtime (clé CÔTÉ SERVEUR) ──
    try:
        eleven = await _connect_eleven(settings.ELEVENLABS_API_KEY)
    except Exception as e:
        logger.error("[stream] ElevenLabs connect KO: %s", e)
        await ws.send_json({"type": "error", "error": "stt_connect"})
        await ws.close()
        return

    committed = ""        # dernier transcript figé
    eot_pending = False   # un EOT device attend le committed_transcript d'ElevenLabs

    async def eleven_to_device():
        nonlocal committed, eot_pending
        try:
            async for raw in eleven:
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                mt = msg.get("message_type")
                if mt == "partial_transcript":
                    await ws.send_json({"type": "partial", "text": msg.get("text", "")})
                elif mt in ("committed_transcript", "committed_transcript_with_timestamps"):
                    committed = msg.get("text", "") or committed
                    if eot_pending:
                        eot_pending = False
                        transcript = committed.strip()
                        logger.info("[stream] EOT → transcript=%r", transcript[:80])
                        # TODO étape 1b : intent → LLM → TTS et streamer le MP3 en retour.
                        await ws.send_json({"type": "final", "transcript": transcript})
                    else:
                        await ws.send_json({"type": "committed", "text": committed})
                elif mt in ("error", "auth_error", "input_error", "quota_exceeded", "rate_limited"):
                    logger.warning("[stream] ElevenLabs %s: %s", mt, msg.get("error"))
        except Exception:
            pass

    async def device_to_eleven():
        nonlocal committed, eot_pending
        while True:
            data = await ws.receive()
            if data.get("type") == "websocket.disconnect":
                break
            chunk = data.get("bytes")
            if chunk:
                # PCM 16k brut → base64 → ElevenLabs (format officiel)
                await eleven.send(json.dumps({
                    "message_type": "input_audio_chunk",
                    "audio_base_64": base64.b64encode(chunk).decode(),
                    "commit": False,
                    "sample_rate": 16000,
                }))
                continue
            txt = data.get("text")
            if not txt:
                continue
            try:
                cmd = json.loads(txt)
            except Exception:
                continue
            if cmd.get("type") == "eot":
                # fin de tour (Smart Turn local) → on COMMITE → committed_transcript suit
                eot_pending = True
                await eleven.send(json.dumps({
                    "message_type": "input_audio_chunk",
                    "audio_base_64": _SILENCE_B64,
                    "commit": True,
                    "sample_rate": 16000,
                }))
            elif cmd.get("type") == "cancel":
                committed = ""
                eot_pending = False

    try:
        await asyncio.gather(eleven_to_device(), device_to_eleven())
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning("[stream] erreur: %s: %s", type(e).__name__, e)
    finally:
        try:
            await eleven.close()
        except Exception:
            pass
