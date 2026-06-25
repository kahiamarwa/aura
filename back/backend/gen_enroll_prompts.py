#!/usr/bin/env python3
"""Génère les prompts vocaux d'enrôlement (MP3) via ElevenLabs — À LANCER 1× SUR LE SERVEUR.

Sauve les fichiers dans ../device/prompts/ (ou DEVICE_PROMPTS_DIR). Ces MP3 sont ensuite
EMBARQUÉS sur l'enceinte (commit) et joués LOCALEMENT pendant l'enrôlement — aucune clé à
l'exécution sur l'appareil. Si un fichier manque sur le device, il retombe sur des bips.

  cd backend && python gen_enroll_prompts.py
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from app.config import get_settings
from app.services.tts_service import stream_tts

PROMPTS = {
    "enroll_intro": "Je vais apprendre ta voix. Quand la lumière devient verte, parle normalement, "
                    "raconte ta journée ou compte jusqu'à dix.",
    "enroll_speak": "Parle maintenant.",
    "enroll_continue": "Continue, je t'écoute.",
    "enroll_almost": "Encore quelques secondes.",
    "enroll_done": "C'est bon. Je reconnais ta voix maintenant.",
    "enroll_fail": "Je n'ai pas bien entendu. On réessaiera plus tard.",
}


async def main():
    s = get_settings()
    if not s.ELEVENLABS_API_KEY or not s.ELEVENLABS_VOICE_ID:
        raise SystemExit("ELEVENLABS_API_KEY / ELEVENLABS_VOICE_ID manquant en env/.env")
    out = Path(os.getenv("DEVICE_PROMPTS_DIR",
                         Path(__file__).resolve().parent.parent / "device" / "prompts"))
    out.mkdir(parents=True, exist_ok=True)
    for name, text in PROMPTS.items():
        data = b""
        async for chunk in stream_tts(text=text, voice_id=s.ELEVENLABS_VOICE_ID,
                                      api_key=s.ELEVENLABS_API_KEY):
            data += chunk
        (out / f"{name}.mp3").write_bytes(data)
        print(f"  ✓ {out / f'{name}.mp3'} ({len(data)} octets)")
    print("Fini. Commit device/prompts/ pour embarquer les prompts sur l'enceinte.")


if __name__ == "__main__":
    asyncio.run(main())
