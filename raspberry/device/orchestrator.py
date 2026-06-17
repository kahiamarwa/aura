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
from .speaker import TargetSpeaker
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
        self.target = TargetSpeaker()
        self.player = Player()
        self.ambient = AmbientContext()
        self.state = "IDLE"

    # ── LISTENING : enregistrement avec endpointing par locuteur cible ─
    def _record_command(self, frames, continuation: bool = False) -> np.ndarray | None:
        """Enregistre la commande et s'arrête quand l'UTILISATEUR a fini.

        Robuste en milieu bruyant : si la voix de l'utilisateur est enrôlée,
        on endpointe sur SA voix (ECAPA local) en ignorant les autres voix.
        Sinon, repli sur énergie/VAD. Cap de sécurité absolu dans tous les cas.

        continuation=True : on reprend après une pause de réflexion (pas de bip,
        attente plus courte de la suite).
        """
        sr = config.SAMPLE_RATE
        use_target = self.target.has_reference
        wait_max = config.TARGET_WAIT_CONTINUE_S if continuation else config.TARGET_WAIT_START_S
        if not continuation:
            play_beep()
            mode = "locuteur cible" if use_target else "énergie/VAD"
            logger.info("[state] LISTENING (%s) — parlez…", mode)
            self.ambient.set_enabled(False)

        chunks: list[np.ndarray] = []
        win = np.zeros(0, dtype=np.int16)
        win_max = int(config.TARGET_WINDOW_S * sr)
        min_win = int(0.5 * sr)
        hop = 0.0
        total_s = 0.0
        started = False
        absent_s = 0.0
        wait_s = 0.0

        for frame in frames:
            chunks.append(frame)
            total_s += FRAME_S
            win = np.concatenate([win, frame])[-win_max:]
            hop += FRAME_S

            # Cap de sécurité absolu — coupe toujours
            if total_s >= config.CMD_MAX_S:
                logger.info("[endpoint] cap max %.0fs atteint", config.CMD_MAX_S)
                break

            # Décision seulement à la cadence du hop (réduit le calcul)
            if hop < config.TARGET_HOP_S:
                continue
            hop = 0.0

            win_rms = _rms(win)
            if win_rms < config.CMD_SILENCE_RMS * 0.5:
                present = False                         # clairement silence
            elif use_target and len(win) >= min_win:
                is_user, score = self.target.is_target(win)
                if is_user is None:                     # modèle indispo → repli
                    use_target = False
                    present = win_rms >= config.CMD_SILENCE_RMS
                else:
                    present = is_user
            else:
                present = win_rms >= config.CMD_SILENCE_RMS

            if present:
                if not started:
                    logger.info("[endpoint] ta voix détectée (rms=%.0f) — j'enregistre", win_rms)
                started = True
                absent_s = 0.0
            elif started:
                absent_s += config.TARGET_HOP_S
                if absent_s >= config.TARGET_HANG_S:
                    logger.info("[endpoint] fin (%.1fs de parole, hang %.1fs)", total_s, absent_s)
                    break                               # l'utilisateur a fini
            else:
                wait_s += config.TARGET_HOP_S
                if wait_s >= wait_max:
                    if not continuation:
                        logger.info("[endpoint] voix utilisateur jamais détectée (rms_max=%.0f) → abandon", _rms(win))
                    return None

        if not started:
            return None
        pcm = np.concatenate(chunks)
        if len(pcm) < config.CMD_MIN_SPEECH_S * sr:
            logger.info("[endpoint] commande trop courte (%.1fs) → ignorée", len(pcm) / sr)
            return None
        return pcm

    # ── Helper : la fenêtre contient-elle la voix de l'UTILISATEUR ? ──
    def _user_in_window(self, window: np.ndarray) -> bool:
        """True si la voix de l'utilisateur enrôlé est présente (locuteur cible).

        Sert au barge-in et au follow-up : seul l'utilisateur (pas YouTube ni
        la voix d'Aura elle-même) peut interrompre/relancer. Suppose une
        empreinte en cache (sinon ces déclencheurs restent désactivés).
        """
        if _rms(window) < config.CMD_SILENCE_RMS * 0.5:
            return False
        is_user, _ = self.target.is_target(window)
        return bool(is_user)

    # ── THINKING : cloud (gated) → audio ou statut ───────────────────
    def _handle_command(self, pcm: np.ndarray, from_conversing: bool, frames) -> tuple[str, bool]:
        self._spoke = False
        sr = config.SAMPLE_RATE
        full = pcm
        checks = 0
        ctx = self.ambient.get_context()
        # ── Boucle d'endpointing sémantique : tolère les pauses de réflexion ──
        while True:
            tentative = (
                config.SEMANTIC_ENDPOINTING
                and checks < config.SEMANTIC_MAX_CHECKS
                and len(full) < config.SEMANTIC_MAX_S * sr
            )
            logger.info("[state] THINKING — envoi au cloud (from_conversing=%s, tentative=%s)…",
                        from_conversing, tentative)
            try:
                res = cloud.converse(full, from_conversing, ctx, tentative=tentative)
            except httpx.HTTPStatusError as e:
                logger.error("[cloud] %s: %s", e.response.status_code, e.response.text[:200])
                return "CONVERSING", False
            except Exception as e:
                logger.error("[cloud] injoignable: %s", e)
                return "CONVERSING", False

            if res.get("kind") == "status" and res.get("status") == "incomplete":
                checks += 1
                logger.info("[endpoint] pause de réflexion (« %s… ») — on continue d'écouter",
                            (res.get("transcript") or "")[:50])
                more = self._record_command(frames, continuation=True)
                if more is None:
                    # L'utilisateur a vraiment fini → on force le traitement
                    logger.info("[endpoint] plus de parole → traitement de la commande")
                    res = cloud.converse(full, from_conversing, ctx, tentative=False)
                    break
                full = np.concatenate([full, more])
                continue
            break

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
        self._spoke = True
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
        gated = self.target.has_reference   # barge-in vocal seulement si enrôlé
        win = np.zeros(0, dtype=np.int16)
        win_max = int(1.0 * config.SAMPLE_RATE)
        hop = 0.0
        streak = 0
        while t.is_alive():
            try:
                frame = next(frames)
            except StopIteration:
                break
            ev = self.wake.process(frame)
            # ANTI-ÉCHO : 'activate' ignoré (Aura s'entend) ; 'Stop Aura' coupe toujours.
            if ev == "interrupt":
                logger.info("[state] STOP — coupure")
                self.player.stop()
                return True
            # Barge-in par la VOIX DE L'UTILISATEUR (pas YouTube ni la voix d'Aura).
            if gated:
                win = np.concatenate([win, frame])[-win_max:]
                hop += FRAME_S
                if hop >= config.TARGET_HOP_S:
                    hop = 0.0
                    if self._user_in_window(win):
                        streak += 1
                        if streak >= 2:
                            logger.info("[state] barge-in (ta voix) — coupure")
                            self.player.stop()
                            return True
                    else:
                        streak = 0
        t.join(timeout=0.5)
        return False

    # ── CONVERSING : fenêtre 12 s, follow-up sans wake word ──────────
    def _conversing(self, frames) -> tuple[str, bool]:
        """Retourne (next_state, from_conversing). Timeout → ('IDLE', False)."""
        logger.info("[state] CONVERSING — répondez (ou « Dis Aura »), %.0fs", config.CONVERSATION_WINDOW_S)
        self.ambient.set_enabled(False)
        deadline = time.time() + config.CONVERSATION_WINDOW_S
        # Follow-up sans wake word UNIQUEMENT si la VOIX de l'utilisateur est
        # détectée (locuteur cible). Sinon → seul « Dis Aura » ré-engage.
        gated = self.target.has_reference
        win = np.zeros(0, dtype=np.int16)
        win_max = int(1.0 * config.SAMPLE_RATE)
        hop = 0.0
        streak = 0
        for frame in frames:
            if time.time() >= deadline:
                return "IDLE", False
            ev = self.wake.process(frame)
            if ev in ("activate", "interrupt"):
                return "LISTENING", False        # wake word explicite (toujours)
            if gated:
                win = np.concatenate([win, frame])[-win_max:]
                hop += FRAME_S
                if hop >= config.TARGET_HOP_S:
                    hop = 0.0
                    if self._user_in_window(win):
                        streak += 1
                        if streak >= 2:
                            return "LISTENING", True   # follow-up : ta voix détectée
                    else:
                        streak = 0
        return "IDLE", False

    # ── Boucle principale ────────────────────────────────────────────
    def run(self):
        self.target.load_references()   # cache l'empreinte vocale (endpointing local)
        self.ambient.start()
        logger.info("Aura prêt. Dites « Dis Aura ».")
        with MicStream() as mic:
            frames = mic.frames()
            self.state = "IDLE"
            from_conversing = False
            wasted = 0   # garde-fou : commandes consécutives sans réponse → IDLE
            while True:
                if self.state == "IDLE":
                    wasted = 0
                    self.ambient.set_enabled(True)
                    frame = next(frames)
                    self.ambient.feed(frame)          # contexte ambiant
                    if self.wake.process(frame) == "activate":
                        self.state, from_conversing = "LISTENING", False

                elif self.state == "LISTENING":
                    pcm = self._record_command(frames)
                    if pcm is None:
                        wasted += 1
                        self.state, from_conversing = ("CONVERSING", False) if from_conversing else ("IDLE", False)
                    else:
                        self.state, from_conversing = self._handle_command(pcm, from_conversing, frames)
                        wasted = 0 if getattr(self, "_spoke", False) else wasted + 1
                    # 2 cycles sans réponse d'affilée → retour IDLE (anti-boucle)
                    if wasted >= 2 and self.state == "CONVERSING":
                        logger.info("[guard] %d commandes sans réponse → IDLE (dites « Dis Aura »)", wasted)
                        self.state, from_conversing = "IDLE", False

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
