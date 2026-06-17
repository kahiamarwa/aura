"""Orchestrateur de l'enceinte Aura (device headless) — parité web.

Machine à états (comme useAuraSession côté web) :

  IDLE      → contexte ambiant actif ; attend « Dis Aura »
  LISTENING → enregistre la commande (VAD Silero) ; bip d'entrée
  THINKING  → cloud : STT → [intent si conversing] → speaker verif → LLM → TTS
  SPEAKING  → joue la réponse ; « Stop Aura » ou parole forte = barge-in
              ANTI-ÉCHO : le wake word « activate » est ignoré pendant la lecture
  CONVERSING→ fenêtre 12 s : on peut reparler SANS wake word ; sinon → IDLE

Le device ne fait localement que : wake word, VAD, capture/lecture audio,
machine à états. STT / intent / speaker verif / LLM / TTS + clés = cloud.
"""

import sys
import time
import logging
import threading

import httpx
import numpy as np

from . import config
from .wakeword import WakeWord
from .vad import VAD
from .audio_io import MicStream, Player, play_beep
from .context import AmbientContext
from . import cloud

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("orchestrator")

FRAME_S = config.FRAME_SAMPLES / config.SAMPLE_RATE


def _rms(frame: np.ndarray) -> float:
    return float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))


class Orchestrator:
    def __init__(self):
        self.wake = WakeWord()
        self.vad = VAD()
        self.player = Player()
        self.ambient = AmbientContext()
        self.state = "IDLE"

    # ── LISTENING : enregistre la commande via VAD ───────────────────
    def _record_command(self, frames) -> np.ndarray | None:
        play_beep()
        logger.info("[state] LISTENING — parlez…")
        self.ambient.set_enabled(False)
        self.vad.reset()
        chunks, speech, silence_s, total_s = [], False, 0.0, 0.0
        for frame in frames:
            chunks.append(frame)
            total_s += FRAME_S
            if self.vad.is_speech(frame):
                speech, silence_s = True, 0.0
            elif speech:
                silence_s += FRAME_S
            if speech and silence_s >= config.CMD_SILENCE_HANG_S:
                break
            if total_s >= config.CMD_MAX_S:
                break
        if not speech:
            logger.info("[state] aucune parole → retour")
            return None
        pcm = np.concatenate(chunks)
        if len(pcm) < config.CMD_MIN_SPEECH_S * config.SAMPLE_RATE:
            return None
        return pcm

    # ── THINKING : cloud (gated) → audio ou statut ───────────────────
    def _handle_command(self, pcm: np.ndarray, from_conversing: bool, frames) -> tuple[str, bool]:
        logger.info("[state] THINKING — envoi au cloud (from_conversing=%s)…", from_conversing)
        try:
            res = cloud.converse(pcm, from_conversing, self.ambient.get_context())
        except httpx.HTTPStatusError as e:
            logger.error("[cloud] %s: %s", e.response.status_code, e.response.text[:200])
            return "CONVERSING", False
        except Exception as e:
            logger.error("[cloud] injoignable: %s", e)
            return "CONVERSING", False

        if res["kind"] == "status":
            st = res.get("status")
            if st == "not_directed":
                logger.info("[gate] pas pour Aura → conversing")
            elif st == "rejected":
                logger.info("[gate] locuteur non reconnu (%s, %.2f) → conversing",
                            res.get("speaker_name"), res.get("score") or 0)
            else:
                logger.info("[gate] %s → conversing", st)
            return "CONVERSING", False

        logger.info("[USER] %s", res.get("transcript", ""))
        logger.info("[AURA] %s", res.get("response", ""))
        barge = self._speak(res["mp3"], frames)
        # Barge-in pendant la réponse = l'utilisateur enchaîne → réécoute (intent gating)
        return ("LISTENING", True) if barge else ("CONVERSING", False)

    # ── SPEAKING : lecture + barge-in (anti-écho) ────────────────────
    def _speak(self, mp3: bytes, frames) -> bool:
        """Joue le MP3. Retourne True si interrompu (barge-in), False si fini."""
        self.state = "SPEAKING"
        self.ambient.set_enabled(False)
        t = threading.Thread(target=self.player.play_mp3, args=(mp3,), daemon=True)
        t.start()
        logger.info("[state] SPEAKING — (« Stop Aura » pour couper)")
        loud = 0
        while t.is_alive():
            try:
                frame = next(frames)
            except StopIteration:
                break
            ev = self.wake.process(frame)
            # ANTI-ÉCHO : on ignore 'activate' (Aura s'entend elle-même) ;
            # seul 'Stop Aura' (interrupt) ou une parole forte coupe.
            if ev == "interrupt":
                logger.info("[state] STOP — coupure")
                self.player.stop()
                return True
            if _rms(frame) >= config.SPEAKING_RMS:
                loud += 1
                if loud >= config.FOLLOWUP_SPEECH_FRAMES:
                    logger.info("[state] barge-in (parole) — coupure")
                    self.player.stop()
                    return True
            else:
                loud = 0
        t.join(timeout=0.5)
        return False

    # ── CONVERSING : fenêtre 12 s, follow-up sans wake word ──────────
    def _conversing(self, frames) -> tuple[str, bool]:
        """Retourne (next_state, from_conversing). Timeout → ('IDLE', False)."""
        logger.info("[state] CONVERSING — répondez (ou « Dis Aura »), %.0fs", config.CONVERSATION_WINDOW_S)
        self.ambient.set_enabled(False)
        deadline = time.time() + config.CONVERSATION_WINDOW_S
        speech = 0
        for frame in frames:
            if time.time() >= deadline:
                return "IDLE", False
            ev = self.wake.process(frame)
            if ev == "activate":
                return "LISTENING", False        # wake word explicite
            if ev == "interrupt":
                return "LISTENING", False
            if _rms(frame) >= config.CONVERSING_RMS:
                speech += 1
                if speech >= config.FOLLOWUP_SPEECH_FRAMES:
                    return "LISTENING", True      # follow-up sans wake word
            else:
                speech = 0
        return "IDLE", False

    # ── Boucle principale ────────────────────────────────────────────
    def run(self):
        self.ambient.start()
        logger.info("Aura prêt. Dites « Dis Aura ».")
        with MicStream() as mic:
            frames = mic.frames()
            self.state = "IDLE"
            from_conversing = False
            while True:
                if self.state == "IDLE":
                    self.ambient.set_enabled(True)
                    frame = next(frames)
                    self.ambient.feed(frame)          # contexte ambiant
                    if self.wake.process(frame) == "activate":
                        self.state, from_conversing = "LISTENING", False

                elif self.state == "LISTENING":
                    pcm = self._record_command(frames)
                    if pcm is None:
                        self.state, from_conversing = ("CONVERSING", from_conversing) if from_conversing else ("IDLE", False)
                    else:
                        self.state, from_conversing = self._handle_command(pcm, from_conversing, frames)

                elif self.state == "CONVERSING":
                    self.state, from_conversing = self._conversing(frames)
                    logger.info("[state] → %s", self.state)

                else:
                    self.state = "IDLE"


def main():
    try:
        Orchestrator().run()
    except KeyboardInterrupt:
        print("\nArrêt.")
        sys.exit(0)


if __name__ == "__main__":
    main()
