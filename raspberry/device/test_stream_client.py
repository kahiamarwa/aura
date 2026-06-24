"""Test standalone du chemin B de bout en bout (étape 3, avant l'intégration orchestrateur).

Streame un WAV (16k mono) au backend via le WS, envoie EOT, et affiche le transcript +
la réponse LLM + compte le MP3 TTS reçu. Valide device ↔ backend ↔ ElevenLabs ↔ LLM ↔ TTS.

Prérequis : pip install websocket-client ; backend déployé (route /api/device-stream) ;
DEVICE_TOKEN dans ~/.aura/env. Lancer depuis ~/aura/raspberry :
    python -m device.test_stream_client mon_audio_16k_mono.wav
"""
import sys
import time
import wave

from . import config
from .stream_client import StreamClient, ws_url_from_http


def main():
    if len(sys.argv) < 2:
        raise SystemExit("usage: python -m device.test_stream_client audio_16k_mono.wav")
    with wave.open(sys.argv[1], "rb") as w:
        if w.getframerate() != 16000 or w.getnchannels() != 1:
            raise SystemExit("WAV 16k mono attendu")
        pcm = w.readframes(w.getnframes())

    url = ws_url_from_http(config.CLOUD_BACKEND_URL)
    print(f"WS : {url}\nAudio : {len(pcm)} octets PCM16 16k")
    c = StreamClient(url, config.DEVICE_TOKEN)
    if not c.connect():
        raise SystemExit("connexion WS KO (backend déployé ? websocket-client installé ?)")

    print("--- streaming PCM ---")
    for i in range(0, len(pcm), 2560):     # chunks de 80ms
        c.send_pcm(pcm[i:i + 2560])
        time.sleep(0.05)
    print("→ EOT (fin de tour)")
    c.send_eot()

    audio_bytes = 0
    while True:
        m = c.recv(timeout=30)
        if m is None:
            print("timeout (30s sans message)")
            break
        kind, data = m
        if kind == "partial":
            print("  partial :", data.get("text", ""))
        elif kind == "committed":
            print("  committed :", data.get("text", ""))
        elif kind == "response":
            print("  ✅ RÉPONSE :", data.get("text", "")[:200])
        elif kind == "audio":
            audio_bytes += len(data)
        elif kind == "audio_end":
            print(f"  ✅ MP3 TTS reçu : {audio_bytes} octets — FIN OK")
            break
        elif kind == "final":
            print("  final (réponse vide) :", data)
            break
        elif kind in ("error", "closed"):
            print(f"  ⚠️ {kind} :", data)
            break
    c.close()


if __name__ == "__main__":
    main()
