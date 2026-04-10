import asyncio
import base64
import json
import logging
import struct
import io
import wave

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter()

MISTRAL_TRANSCRIPTION_URL = "https://api.mistral.ai/v1/audio/transcriptions"
BATCH_INTERVAL_SECS = 15


def resample_pcm_16bit(data: bytes, from_rate: int, to_rate: int = 16000) -> bytes:
    """Resample 16-bit PCM audio from from_rate to to_rate."""
    if from_rate == to_rate:
        return data
    samples = struct.unpack(f"<{len(data)//2}h", data)
    ratio = to_rate / from_rate
    new_len = int(len(samples) * ratio)
    resampled = []
    for i in range(new_len):
        src_idx = i / ratio
        idx = int(src_idx)
        if idx >= len(samples) - 1:
            resampled.append(samples[-1])
        else:
            frac = src_idx - idx
            val = int(samples[idx] * (1 - frac) + samples[idx + 1] * frac)
            resampled.append(max(-32768, min(32767, val)))
    return struct.pack(f"<{len(resampled)}h", *resampled)


def pcm_to_wav(pcm_data: bytes, sample_rate: int = 16000, channels: int = 1, sample_width: int = 2) -> bytes:
    """Wrap raw PCM data in a WAV container."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_data)
    return buf.getvalue()


async def transcribe_audio(api_key: str, wav_data: bytes) -> str:
    """Send audio to Mistral Voxtral Mini Transcribe."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            MISTRAL_TRANSCRIPTION_URL,
            headers={"x-api-key": api_key},
            files={"file": ("audio.wav", wav_data, "audio/wav")},
            data={
                "model": "voxtral-mini-latest",
                "language": "fr",
            },
        )

        if response.status_code == 429:
            logger.warning("[Mistral-STT] Rate limited (429)")
            return ""

        response.raise_for_status()
        result = response.json()

    text = result.get("text", "").strip()
    logger.info("[Mistral-STT] Transcription: '%s'", text[:100])
    return text


@router.websocket("/api/gemini-stt")
async def passive_stt_proxy(websocket: WebSocket):
    await websocket.accept()
    settings = get_settings()

    if not settings.MISTRAL_API_KEY:
        await websocket.close(code=1008, reason="MISTRAL_API_KEY not configured")
        return

    audio_buffer = bytearray()
    sample_rate_ref = 48000
    is_running = True

    await websocket.send_json({"type": "session_started"})

    async def process_audio_batches():
        nonlocal audio_buffer, is_running

        while is_running:
            await asyncio.sleep(BATCH_INTERVAL_SECS)

            if not audio_buffer or not is_running:
                continue

            pcm_data = bytes(audio_buffer)
            audio_buffer.clear()

            # Skip if too little audio (less than 1s at 16kHz)
            min_bytes = 16000 * 2 * 1
            if len(pcm_data) < min_bytes:
                continue

            try:
                pcm_16k = resample_pcm_16bit(pcm_data, sample_rate_ref, 16000)
                wav_data = pcm_to_wav(pcm_16k)
                logger.info("[Mistral-STT] Sending %d bytes WAV", len(wav_data))

                text = await transcribe_audio(settings.MISTRAL_API_KEY, wav_data)

                if text and is_running:
                    try:
                        await websocket.send_json({
                            "type": "committed_transcript",
                            "text": text
                        })
                    except Exception:
                        break

            except httpx.HTTPStatusError as e:
                logger.error("[Mistral-STT] API error %d: %s", e.response.status_code, e.response.text[:200])
                try:
                    await websocket.send_json({
                        "type": "error",
                        "message": f"Mistral API error: {e.response.status_code}"
                    })
                except Exception:
                    break
            except Exception as e:
                logger.error("[Mistral-STT] Error: %s", e)

    async def receive_audio():
        nonlocal audio_buffer, sample_rate_ref, is_running

        try:
            while is_running:
                raw = await websocket.receive_text()
                msg = json.loads(raw)

                if msg.get("type") == "audio":
                    audio_bytes = base64.b64decode(msg["audio_base_64"])
                    sample_rate_ref = msg.get("sample_rate", 48000)
                    audio_buffer.extend(audio_bytes)
                elif msg.get("type") == "stop":
                    is_running = False
                    break
        except WebSocketDisconnect:
            is_running = False
        except Exception as e:
            logger.error("[Mistral-STT] Receive error: %s", e)
            is_running = False

    try:
        await asyncio.gather(receive_audio(), process_audio_batches(), return_exceptions=True)
    finally:
        is_running = False
        try:
            await websocket.close()
        except Exception:
            pass
