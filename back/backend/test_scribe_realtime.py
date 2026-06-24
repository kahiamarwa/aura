#!/usr/bin/env python3
"""Valide le format de l'API ElevenLabs Scribe v2 realtime (avant de bâtir le device dessus).

Connecte le WS, envoie un audio (WAV 16k mono, ou un bip généré), et IMPRIME tous les
messages reçus → on voit le vrai format (input_audio_chunk ? partial_transcript ?).

À lancer SUR LE SERVEUR (où ELEVENLABS_API_KEY est en env) :
    pip install websockets numpy
    ELEVENLABS_API_KEY=... python backend/test_scribe_realtime.py [fichier.wav]
"""
import asyncio
import base64
import json
import os
import sys
import wave

import numpy as np
import websockets

WS = ("wss://api.elevenlabs.io/v1/speech-to-text/realtime"
      "?model_id=scribe_v2_realtime&commit_strategy=manual")


def _load_pcm(path: str | None) -> bytes:
    if path:
        with wave.open(path, "rb") as w:
            assert w.getframerate() == 16000 and w.getnchannels() == 1, "attendu WAV 16k mono"
            return w.readframes(w.getnframes())
    # pas de fichier → 2s de bip 440Hz (valide juste la connexion + les messages de session)
    t = np.linspace(0, 2, 32000, endpoint=False)
    return (np.sin(2 * np.pi * 440 * t) * 8000).astype(np.int16).tobytes()


async def main():
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise SystemExit("ELEVENLABS_API_KEY manquant en env")
    pcm = _load_pcm(sys.argv[1] if len(sys.argv) > 1 else None)
    print(f"Audio : {len(pcm)} octets PCM16 16k\n--- connexion ---")

    hdr = {"xi-api-key": key}
    try:
        ws = await websockets.connect(WS, additional_headers=hdr)
    except TypeError:
        ws = await websockets.connect(WS, extra_headers=hdr)

    async def reader():
        async for raw in ws:
            try:
                msg = json.loads(raw)
                print("← REÇU:", json.dumps(msg, ensure_ascii=False)[:300])
            except Exception:
                print("← REÇU (brut):", raw[:200])

    rt = asyncio.create_task(reader())
    # envoie l'audio en chunks de 80ms (2560 octets) — format officiel
    print("--- envoi audio (chunks input_audio_chunk) ---")
    for i in range(0, len(pcm), 2560):
        await ws.send(json.dumps({
            "message_type": "input_audio_chunk",
            "audio_base_64": base64.b64encode(pcm[i:i + 2560]).decode(),
            "commit": False,
            "sample_rate": 16000,
        }))
        await asyncio.sleep(0.05)
    # commit final (= fin de tour) → force le committed_transcript
    await ws.send(json.dumps({
        "message_type": "input_audio_chunk",
        "audio_base_64": base64.b64encode(b"\x00" * 320).decode(),
        "commit": True,
        "sample_rate": 16000,
    }))
    print("--- audio envoyé + commit, attente des transcripts (5s) ---")
    await asyncio.sleep(5)
    rt.cancel()
    await ws.close()
    print("--- fini ---")


if __name__ == "__main__":
    asyncio.run(main())
