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
import queue
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
from .led_controller import LedController
from .smart_turn import SmartTurn
from . import stream_client
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
        self.led = LedController()       # LED d'états (no-op si LED_ENABLED=0)
        self.smart_turn = SmartTurn()    # détection de tour (chemin B ; no-op si OFF)
        self.state = "IDLE"
        self.last_transcript = ""        # dernière commande (pour l'affichage live)
        self._stop_watch = False
        self._muted = threading.Event()  # mode confidentiel (mute logiciel à distance)
        self._seq = 0                    # ordre monotone des états (anti-désordre)
        self._state_q: "queue.Queue" = queue.Queue()

    # ── États → front : push ÉVÉNEMENTIEL sérialisé ──────────────────
    def _set_state(self, new: str, transcript: str | None = None):
        """Change l'état ET l'envoie au front, EN ORDRE (seq monotone).

        Remplace l'ancien polling 0.08s : ici AUCUNE transition n'est ratée
        (même < 80ms), et l'ordre d'affichage est garanti côté front via seq.
        """
        self.state = new
        self._seq += 1
        self.led.set_state(new)          # LED physique suit l'état (comme l'orbe)
        tr = transcript if transcript is not None else (
            self.last_transcript if new in ("THINKING", "SPEAKING") else "")
        try:
            self._state_q.put_nowait((new, self._seq, tr))
        except Exception:
            pass

    def _state_pusher(self):
        """UN SEUL thread : dépile et pousse séquentiellement (ordre garanti).

        Heartbeat ~12s si rien ne change (détection online/offline côté front).
        """
        while not self._stop_watch:
            try:
                state, seq, tr = self._state_q.get(timeout=12.0)
            except queue.Empty:
                cloud.push_state(self.state, "", self._seq)   # heartbeat
                continue
            cloud.push_state(state, tr, seq)

    # ── Poll du mute distant (mode confidentiel piloté par le web) ───
    def _mute_poller(self):
        """Interroge le cloud : le web a-t-il coupé le micro ? Met à jour l'event.
        Erreur réseau → on ne change rien (on ne mute pas par accident)."""
        while not self._stop_watch:
            try:
                if cloud.get_mute_state():
                    self._muted.set()
                else:
                    self._muted.clear()
            except Exception:
                pass
            time.sleep(config.MUTE_POLL_S)

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
        use_target = self.target.has_reference and config.TARGET_ENDPOINTING
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
        hop_voiced = False      # le VAD a-t-il vu de la parole sur le hop courant ?
        if config.ENDPOINT_VAD:
            self.vad.reset()    # état Silero propre pour cette commande

        for frame in frames:
            chunks.append(frame)
            total_s += FRAME_S
            win = np.concatenate([win, frame])[-win_max:]
            hop += FRAME_S
            # VAD Silero à CHAQUE frame (modèle stateful) : robuste au bruit/ronflement,
            # là où l'énergie brute gardait l'enregistrement ouvert jusqu'au cap.
            if config.ENDPOINT_VAD and self.vad.is_speech(frame):
                hop_voiced = True

            # Cap de sécurité absolu — coupe toujours
            if total_s >= config.CMD_MAX_S:
                logger.info("[endpoint] cap max %.0fs atteint", config.CMD_MAX_S)
                break

            # Décision seulement à la cadence du hop (réduit le calcul)
            if hop < config.TARGET_HOP_S:
                continue
            hop = 0.0

            win_rms = _rms(win)           # pour logs + _user_in_window
            # Parole = VAD (robuste bruit) OU énergie franche (filet de sécurité :
            # si Silero hésite/absent ou si l'AEC baisse le niveau, rms>=seuil suffit).
            speaking = (config.ENDPOINT_VAD and hop_voiced) or (win_rms >= config.CMD_SILENCE_RMS)
            hop_voiced = False
            if not speaking:
                present = False                         # ni VAD ni énergie → silence
            elif use_target and started and len(win) >= min_win:
                # On NE bloque PAS le DÉMARRAGE sur l'identité (ECAPA court instable →
                # coupait à 2s). On démarre sur la PAROLE ; l'identité est vérifiée
                # côté cloud. Une fois démarré, on garde tant qu'il parle (KEEP=-1.0
                # par défaut → ne coupe jamais sur le score).
                _, score = self.target.is_target(win)
                present = speaking and (score >= config.TARGET_KEEP_THRESHOLD)
            else:
                present = speaking                      # démarrage + cas sans ECAPA

            if present:
                if not started:
                    logger.info("[endpoint] ta voix détectée (rms=%.0f) — j'enregistre", win_rms)
                started = True
                absent_s = 0.0
            elif started:
                absent_s += config.TARGET_HOP_S
                if absent_s >= config.TARGET_HANG_S:
                    logger.info("[endpoint] fin — %.1fs parlé (clip %.1fs, hang %.1fs)",
                                total_s - absent_s, total_s, absent_s)
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
        # Retire le silence de fin (le hang) pour ne pas diluer le STT.
        trail = int(max(0.0, absent_s - 0.3) * sr)
        if trail and len(pcm) - trail >= config.CMD_MIN_SPEECH_S * sr:
            pcm = pcm[:-trail]
        if len(pcm) < config.CMD_MIN_SPEECH_S * sr:
            logger.info("[endpoint] commande trop courte (%.1fs) → ignorée", len(pcm) / sr)
            return None
        logger.info("[endpoint] commande capturée : %.1fs envoyés au cloud", len(pcm) / sr)
        return pcm

    # ── Le mot de réveil vient-il bien de l'utilisateur enrôlé ? ─────
    def _wake_is_owner(self, audio: np.ndarray) -> bool:
        """Anti faux-déclenchement : on n'active que pour la voix enrôlée.

        Si pas d'empreinte ou gate désactivé → on accepte (fallback).
        """
        if not config.WAKE_SPEAKER_GATE or not self.target.has_reference:
            return True
        is_user, score = self.target.is_target(audio)
        if is_user is False:
            logger.info("[wake] mot de réveil mais pas ta voix (score=%.2f) → ignoré", score)
            return False
        return True

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

    # ── Appel cloud blindé : ne LÈVE JAMAIS (sinon le device crasherait) ─
    def _safe_converse(self, full, from_conversing, ctx, tentative):
        try:
            return cloud.converse(full, from_conversing, ctx, tentative=tentative)
        except httpx.HTTPStatusError as e:
            logger.error("[cloud] HTTP %s", e.response.status_code)  # PAS .text (réponse streaming)
            return {"kind": "status", "status": "error"}
        except Exception as e:
            logger.error("[cloud] injoignable: %s: %s", type(e).__name__, e)
            return {"kind": "status", "status": "error"}

    # ── THINKING : cloud (gated) → audio ou statut ───────────────────
    def _handle_command(self, pcm: np.ndarray, from_conversing: bool, frames) -> tuple[str, bool]:
        self._spoke = False
        self.last_transcript = ""            # pas encore transcrit → ne pas montrer l'ancien
        self._set_state("THINKING")          # P2 : l'orbe passe au bleu PENDANT le cloud
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
            res = self._safe_converse(full, from_conversing, ctx, tentative)

            if res.get("kind") == "status" and res.get("status") == "incomplete":
                checks += 1
                logger.info("[endpoint] pause de réflexion (« %s… ») — on continue d'écouter",
                            (res.get("transcript") or "")[:50])
                self._set_state("LISTENING")          # on réécoute la suite
                more = self._record_command(frames, continuation=True)
                self._set_state("THINKING")
                if more is None:
                    # L'utilisateur a vraiment fini → on force le traitement
                    logger.info("[endpoint] plus de parole → traitement de la commande")
                    res = self._safe_converse(full, from_conversing, ctx, False)
                    break
                full = np.concatenate([full, more])
                continue
            break

        # Erreur cloud (réseau/HTTP) : bip d'erreur LOCAL distinct + on libère.
        if res.get("kind") == "status" and res.get("status") == "error":
            logger.warning("[cloud] erreur → bip d'erreur, retour conversing")
            play_beep(freq=300.0, dur=0.25)
            return "CONVERSING", False

        if res["kind"] == "status":
            st = res.get("status")
            if st == "not_directed":
                # Pas pour Aura (tu parles aux gens) → on SORT du suivi (IDLE).
                # Sinon le bruit/la discussion relance la capture sans arrêt (vert non-stop).
                logger.info("[gate] pas pour Aura → IDLE (fin du suivi, dis « Dis Aura » pour relancer)")
                return "IDLE", False
            if st == "rejected":
                logger.info("[gate] locuteur non reconnu (%s, %.2f) → conversing",
                            res.get("speaker_name"), res.get("score") or 0)
            else:
                logger.info("[gate] %s → conversing", st)
            return "CONVERSING", False

        logger.info("[USER] %s", res.get("transcript", ""))
        logger.info("[AURA] %s", res.get("response", ""))
        self.last_transcript = res.get("transcript", "")   # pour l'affichage live
        self._spoke = True
        barge = self._speak(res, frames)
        if barge == "stop":
            return "IDLE", False        # « Stop Aura » = silence (pas de réécoute)
        if barge == "barge":
            return "LISTENING", True     # ta voix par-dessus = tu enchaînes
        return "CONVERSING", False       # fin normale → fenêtre de conversation

    # ── Chemin B : flux STREAMING (Silero VAD + Smart Turn → EOT local) ──
    def _handle_command_streaming(self, frames):
        """Stream le PCM au backend, décide la fin de tour EN LOCAL (Smart Turn),
        puis joue la réponse streamée. Retourne (next_state, from_conversing) ou
        None si le WS échoue (→ l'appelant retombe sur l'ancien flux)."""
        url = stream_client.ws_url_from_http(config.CLOUD_BACKEND_URL)
        client = stream_client.StreamClient(url, config.DEVICE_TOKEN)
        if not client.connect():
            return None                          # WS KO → fallback ancien flux
        play_beep()
        logger.info("[stream] LISTENING (streaming + Smart Turn) — parlez…")
        self._set_state("LISTENING")
        self.ambient.set_enabled(False)
        if getattr(self, "mic", None):
            self.mic.flush()
        self.vad.reset()
        turn: list[np.ndarray] = []              # audio accumulé du tour (int16)
        started = False
        silence_s = 0.0
        total_s = 0.0
        wait_s = 0.0
        for frame in frames:
            total_s += FRAME_S
            turn.append(frame)
            client.send_pcm(frame.tobytes())
            if self.vad.is_speech(frame):
                started = True
                silence_s = 0.0
            elif started:
                silence_s += FRAME_S
                if silence_s >= config.STREAM_PAUSE_S:    # pause → fin de tour ?
                    audio = np.concatenate(turn).astype(np.float32) / 32768.0
                    p = self.smart_turn.predict_endpoint(audio)
                    fini = p is None or p >= self.smart_turn.threshold
                    logger.info("[stream] Smart Turn=%.2f @ %.1fs (seuil %.2f) → %s",
                                p if p is not None else -1.0, total_s,
                                self.smart_turn.threshold, "FINI" if fini else "continue")
                    if fini:
                        break
                    silence_s = 0.0               # pause de réflexion → on continue
            else:
                wait_s += FRAME_S
                if wait_s >= config.TARGET_WAIT_START_S:
                    logger.info("[stream] aucune parole → abandon")
                    client.send_cancel()
                    client.close()
                    return "IDLE", False
            if total_s >= config.CMD_MAX_S:
                logger.info("[stream] cap %.0fs", config.CMD_MAX_S)
                break
        if not started:
            client.send_cancel()
            client.close()
            return "IDLE", False
        self._set_state("THINKING")
        client.send_eot()
        barge = self._play_streamed_response(client, frames)
        client.close()
        return ("IDLE", False) if barge == "stop" else ("CONVERSING", False)

    def _play_streamed_response(self, client, frames) -> str | None:
        """Reçoit la réponse (texte + MP3) du backend et la joue, avec « Stop Aura »."""
        self._spoke = False
        self.player.start_stream()
        done = False
        while not done:
            while True:                          # draine tout l'arrivé (non bloquant)
                m = client.recv(timeout=0.0)
                if m is None:
                    break
                kind, data = m
                if kind == "response":
                    self.last_transcript = data.get("transcript", "") or self.last_transcript
                    logger.info("[stream] réponse: %s", (data.get("text") or "")[:80])
                    self._set_state("SPEAKING")
                    self._spoke = True
                elif kind == "audio":
                    self.player.feed(data)
                elif kind in ("audio_end", "final", "error", "closed"):
                    done = True
                    break
            if done:
                break
            try:
                frame = next(frames)             # « Stop Aura » pendant la lecture
            except StopIteration:
                break
            if self.wake.process(frame) == "interrupt":
                logger.info("[stream] STOP — coupure")
                self.player.stop()
                return "stop"
        self.player.end_stream()
        return None

    # ── Teardown déterministe du SPEAKING (aucune fuite socket/zombie) ──
    def _teardown_speak(self, t, stop, res):
        stop.set()
        self.player.stop()
        try:
            res.get("close", lambda: None)()   # ferme le stream httpx → débloque feed
        except Exception:
            pass
        t.join(timeout=2.0)

    # ── SPEAKING : lecture EN STREAMING + barge-in (anti-écho) ───────
    def _speak(self, res: dict, frames) -> str | None:
        """Joue le MP3 EN STREAMING (Aura parle dès le 1er chunk).

        Retourne 'stop' (« Stop Aura » → silence), 'barge' (ta voix → on écoute),
        ou None (lecture finie normalement).
        """
        self._set_state("SPEAKING")
        self.ambient.set_enabled(False)
        # Vide le backlog micro accumulé pendant THINKING → le barge-in « Stop Aura »
        # est détecté EN TEMPS RÉEL (sur l'audio frais), pas avec du retard.
        if getattr(self, "mic", None):
            self.mic.flush()
        stop = threading.Event()
        self.player.start_stream()
        if not self.player.is_playing:        # mpg123 absent → ne pas drainer 10s pour rien
            logger.error("[player] lecture impossible (mpg123 ?) — réponse non jouée")
            try:
                res.get("close", lambda: None)()
            except Exception:
                pass
            self._spoke = False
            return None

        def feed():
            try:
                for chunk in res["chunks"]:
                    if stop.is_set():
                        break
                    self.player.feed(chunk)
            except Exception:
                pass
            finally:
                try:
                    res.get("close", lambda: None)()
                except Exception:
                    pass
                if not stop.is_set():
                    self.player.end_stream()

        t = threading.Thread(target=feed, daemon=True)
        t.start()
        logger.info("[state] SPEAKING — (« Stop Aura » pour couper)")
        # barge-in vocal : seulement si enrôlé ET activé (sinon SEUL « Stop Aura » coupe)
        gated = self.target.has_reference and config.BARGE_IN_ENABLED
        win = np.zeros(0, dtype=np.int16)
        win_max = int(1.0 * config.SAMPLE_RATE)
        hop = 0.0
        streak = 0
        while self.player.is_playing or t.is_alive():
            try:
                frame = next(frames)
            except StopIteration:
                break
            ev = self.wake.process(frame)
            # « Stop Aura » = je veux le SILENCE → on coupe et on s'arrête (IDLE).
            if ev == "interrupt":
                logger.info("[state] STOP — coupure (silence)")
                self._teardown_speak(t, stop, res)
                return "stop"
            # Barge-in par TA VOIX (tu parles par-dessus) = tu enchaînes → on t'écoute.
            # Seuil HAUT (BARGE_STREAK) pour laisser « Stop Aura » gagner la course.
            if gated:
                win = np.concatenate([win, frame])[-win_max:]
                hop += FRAME_S
                if hop >= config.TARGET_HOP_S:
                    hop = 0.0
                    if self._user_in_window(win):
                        streak += 1
                        if streak >= config.BARGE_STREAK:
                            logger.info("[state] barge-in (ta voix) — on écoute")
                            self._teardown_speak(t, stop, res)
                            return "barge"
                    else:
                        streak = 0
        return None

    # ── CONVERSING : fenêtre 12 s, follow-up sans wake word ──────────
    def _conversing(self, frames) -> tuple[str, bool]:
        """Retourne (next_state, from_conversing). Timeout → ('IDLE', False)."""
        logger.info("[state] CONVERSING — répondez (ou « Dis Aura »), %.0fs", config.CONVERSATION_WINDOW_S)
        self.ambient.set_enabled(False)
        if getattr(self, "mic", None):
            self.mic.flush()   # audio frais (pas le backlog du THINKING)
        deadline = time.time() + config.CONVERSATION_WINDOW_S
        # Follow-up sans wake word UNIQUEMENT si la VOIX de l'utilisateur est
        # détectée (locuteur cible). Sinon → seul « Dis Aura » ré-engage.
        gated = self.target.has_reference
        win = np.zeros(0, dtype=np.int16)
        win_max = int(1.0 * config.SAMPLE_RATE)
        hop = 0.0
        streak = 0
        recent = np.zeros(0, dtype=np.int16)
        recent_max = int(1.5 * config.SAMPLE_RATE)
        for frame in frames:
            if time.time() >= deadline:
                return "IDLE", False
            recent = np.concatenate([recent, frame])[-recent_max:]
            ev = self.wake.process(frame)
            if ev == "interrupt":
                return "IDLE", False             # « Stop Aura » = ARRÊTER (pas écouter)
            if ev == "activate" and self._wake_is_owner(recent):
                return "LISTENING", False        # « Dis Aura » par TOI → écoute
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
        if config.AEC_ENABLED:
            logger.info("[AEC] activé — audio via « %s » (annulation d'écho PipeWire)", config.AEC_ALSA_DEVICE)
        self.ambient.start()
        threading.Thread(target=self._state_pusher, daemon=True).start()  # états → front (ordre garanti)
        if config.MUTE_POLL_S > 0:
            threading.Thread(target=self._mute_poller, daemon=True).start()  # mute distant (mode confidentiel)
        logger.info("Aura prêt. Dites « Dis Aura ».")
        # Micro perdu (USB coupé) → LED rouge ; retour → on restaure l'état courant.
        with MicStream(on_lost=lambda: self.led.set_state("MUTED"),
                       on_back=lambda: self.led.set_state(self.state)) as mic:
            self.mic = mic
            frames = mic.frames()
            self._set_state("IDLE")
            from_conversing = False
            wasted = 0          # cycles consécutifs sans réponse → IDLE
            conv_turns = 0      # plafond de tours en CONVERSING (anti-boucle)
            recent = np.zeros(0, dtype=np.int16)        # ~1.5s pour le speaker-gate
            recent_max = int(1.5 * config.SAMPLE_RATE)
            while True:
                # ── Mode confidentiel : micro COUPÉ (rien n'est envoyé au cloud) ──
                if self._muted.is_set():
                    if self.state != "MUTED":
                        logger.info("[mute] 🔴 micro coupé (mode confidentiel) — rien n'est envoyé")
                        self.player.stop()
                        self.ambient.set_enabled(False)
                        from_conversing = False
                        self._set_state("MUTED")
                    if getattr(self, "mic", None):
                        self.mic.flush()             # jette l'audio capté (aucun traitement)
                    time.sleep(0.2)
                    continue
                if self.state == "MUTED":            # sortie de mute → reprise
                    logger.info("[mute] 🟢 micro réactivé")
                    self._set_state("IDLE")

                # Garde anti-boucle GLOBAL (couvre tous les chemins) — P8
                if wasted >= config.MAX_WASTED and self.state != "IDLE":
                    logger.info("[guard] %d cycles sans réponse → IDLE (dites « Dis Aura »)", wasted)
                    wasted, from_conversing = 0, False
                    self._set_state("IDLE")
                    continue

                if self.state == "IDLE":
                    wasted = conv_turns = 0
                    self.ambient.set_enabled(True)
                    frame = next(frames)
                    self.ambient.feed(frame)          # contexte ambiant
                    recent = np.concatenate([recent, frame])[-recent_max:]
                    # « Dis Aura » n'active QUE si c'est ta voix (anti faux-déclenchement)
                    if self.wake.process(frame) == "activate" and self._wake_is_owner(recent):
                        from_conversing = False
                        self._set_state("LISTENING")

                elif self.state == "LISTENING":
                    # Chemin B : flux streaming (Smart Turn) si activé ET dispo.
                    streamed = None
                    if config.STREAMING_MODE and self.smart_turn.available:
                        streamed = self._handle_command_streaming(frames)
                    if streamed is not None:
                        next_state, from_conversing = streamed
                        wasted = 0 if getattr(self, "_spoke", False) else wasted + 1
                    else:
                        # Ancien flux (fallback : WS KO, ou STREAMING_MODE off)
                        pcm = self._record_command(frames)
                        if pcm is None:
                            wasted += 1
                            next_state, from_conversing = ("CONVERSING", False) if from_conversing else ("IDLE", False)
                        else:
                            next_state, from_conversing = self._handle_command(pcm, from_conversing, frames)
                            wasted = 0 if getattr(self, "_spoke", False) else wasted + 1
                    self._set_state(next_state)

                elif self.state == "CONVERSING":
                    conv_turns += 1
                    if conv_turns > config.MAX_CONV_TURNS:
                        logger.info("[guard] conversation trop longue → IDLE")
                        from_conversing = False
                        self._set_state("IDLE")
                        continue
                    next_state, from_conversing = self._conversing(frames)
                    self._set_state(next_state)

                else:
                    # État inattendu : teardown défensif puis IDLE (P8)
                    logger.warning("[state] état inattendu %r → IDLE", self.state)
                    self.player.stop()
                    self.ambient.set_enabled(False)
                    from_conversing = False
                    self._set_state("IDLE")


def main():
    try:
        Orchestrator().run()
    except KeyboardInterrupt:
        print("\nArrêt.")
        sys.exit(0)


if __name__ == "__main__":
    main()
