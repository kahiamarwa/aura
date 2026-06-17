"""Orchestrateur de l'enceinte (device headless).

Boucle :
  IDLE      → écoute le wake word "Dis Aura"
  RECORDING → enregistre la commande (VAD énergie) jusqu'au silence
              → envoie l'audio au cloud (STT+LLM+TTS) → MP3
  SPEAKING  → joue la réponse ; "Stop Aura" coupe et revient à IDLE

Aucun navigateur, aucun front, aucune clé tierce sur le device.
Le wake word est le seul traitement always-on local ; tout le reste est au cloud.
"""

import sys
import time
import logging
import threading

import httpx
import numpy as np

from . import config
from .wakeword import WakeWord
from .audio_io import MicStream, Player
from . import cloud

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("orchestrator")


def _rms(frame: np.ndarray) -> float:
    return float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))


class Orchestrator:
    def __init__(self):
        self.wake = WakeWord()
        self.player = Player()
        self.state = "IDLE"

    # ── Capture d'une commande après le wake word ────────────────────
    def _record_command(self, mic_frames) -> np.ndarray | None:
        logger.info("[state] LISTENING — parlez…")
        chunks: list[np.ndarray] = []
        speech_started = False
        silence_s = 0.0
        total_s = 0.0
        frame_s = config.FRAME_SAMPLES / config.SAMPLE_RATE

        for frame in mic_frames:
            chunks.append(frame)
            total_s += frame_s
            if _rms(frame) >= config.CMD_SILENCE_RMS:
                speech_started = True
                silence_s = 0.0
            elif speech_started:
                silence_s += frame_s

            if speech_started and silence_s >= config.CMD_SILENCE_HANG_S:
                break
            if total_s >= config.CMD_MAX_S:
                break

        if not speech_started:
            logger.info("[state] aucune parole détectée → IDLE")
            return None
        pcm = np.concatenate(chunks)
        if len(pcm) < config.CMD_MIN_SPEECH_S * config.SAMPLE_RATE:
            return None
        return pcm

    # ── Réponse : cloud + lecture, interruptible par "Stop Aura" ─────
    def _respond(self, command_pcm: np.ndarray, mic_frames):
        logger.info("[state] THINKING — envoi au cloud…")
        try:
            mp3, transcript, response_text = cloud.converse(command_pcm)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 422:
                logger.info("[cloud] transcription vide → IDLE")
            else:
                logger.error("[cloud] erreur %s: %s", e.response.status_code, e.response.text[:200])
            return
        except Exception as e:
            logger.error("[cloud] injoignable: %s", e)
            return

        logger.info("[USER] %s", transcript)
        logger.info("[AURA] %s", response_text)

        # Joue le MP3 dans un thread ; la boucle continue d'écouter le wake word
        self.state = "SPEAKING"
        t = threading.Thread(target=self.player.play_mp3, args=(mp3,), daemon=True)
        t.start()
        logger.info("[state] SPEAKING — (dites « Stop Aura » pour couper)")

        while t.is_alive():
            try:
                frame = next(mic_frames)
            except StopIteration:
                break
            if self.wake.process(frame) == "interrupt":
                logger.info("[state] STOP — coupure de la réponse")
                self.player.stop()
                break
        t.join(timeout=0.5)

    # ── Boucle principale ────────────────────────────────────────────
    def run(self):
        logger.info("Aura device prêt. Dites « Dis Aura ».")
        with MicStream() as mic:
            frames = mic.frames()
            self.state = "IDLE"
            for frame in frames:
                event = self.wake.process(frame)
                if event == "activate":
                    command = self._record_command(frames)
                    if command is not None:
                        self._respond(command, frames)
                    self.state = "IDLE"
                    logger.info("[state] IDLE — Dites « Dis Aura »")


def main():
    try:
        Orchestrator().run()
    except KeyboardInterrupt:
        print("\nArrêt.")
        sys.exit(0)


if __name__ == "__main__":
    main()
