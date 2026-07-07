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
from .audio_io import MicStream, Player, play_beep, play_beep_seq
from .context import AmbientContext
from .led_controller import LedController
from .smart_turn import SmartTurn
from . import stream_client
from . import cloud
from . import enroll

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
        self._preroll = None             # audio du DÉBUT de commande (follow-up) à rejouer
        self._enroll_req = None          # demande d'enrôlement vocal poussée par le web
        self._enroll_seen_id = None      # id déjà traité (anti re-déclenchement pendant l'enrôlement)
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
        # Télémétrie wake (P6.1) : le détecteur horodate ses events avec l'état courant.
        try:
            self.wake.state_hint = new
        except Exception:
            pass
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

    def _wake_telemetry_pusher(self):
        """Flush périodique de la télémétrie wake : draine le buffer du détecteur toutes
        les 25 s et l'envoie au cloud (fire-and-forget). Non bloquant : jamais dans la
        boucle audio. Une erreur réseau n'interrompt PAS le thread (les events perdus)."""
        while not self._stop_watch:
            time.sleep(25.0)
            try:
                evs = self.wake.drain_events()
                if evs:
                    cloud.send_wake_events(evs)
            except Exception:
                pass

    def _push_enroll(self, transcript: str):
        """Pousse l'état ENROLLING + avancement au web SANS toucher la LED (le flux
        d'enrôlement pilote lui-même la LED avec ses patterns dédiés)."""
        self.state = "ENROLLING"
        self._seq += 1
        try:
            self._state_q.put_nowait(("ENROLLING", self._seq, transcript))
        except Exception:
            pass

    # ── Poll du contrôle distant (mute + demande d'enrôlement, pilotés par le web) ──
    def _mute_poller(self):
        """Interroge le cloud : mute ? demande d'enrôlement ? Met à jour event + flag.
        Erreur réseau → on ne change rien (on ne mute/enrôle pas par accident)."""
        while not self._stop_watch:
            try:
                ctrl = cloud.get_control()
                if ctrl.get("muted"):
                    self._muted.set()
                else:
                    self._muted.clear()
                req = ctrl.get("enroll_request")
                # Dédup par id : la demande reste en base tant que l'upload n'a pas fini ;
                # sans ça le poller la re-déclencherait en boucle pendant l'enrôlement.
                if req and req.get("id") != self._enroll_seen_id and not self._muted.is_set():
                    self._enroll_req = req      # consommé dans run()
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

    def _interrupt_is_owner(self, audio: np.ndarray) -> bool:
        """Anti-écho TTS (AEC off) : « Stop Aura » PENDANT LA LECTURE n'est accepté que
        si la voix ressemble à un locuteur ENRÔLÉ — la voix TTS d'Aura score ~0 face aux
        empreintes (terrain 06/07 : écho à 0.97 → coupure en pleine génération PPTX).
        Seuil BAS dédié (un vrai « stop aura » est court → score modeste ~0.3).
        Fail-open : sans empreintes / audio trop court / erreur → on accepte."""
        if not config.INTERRUPT_SPEAKER_GATE or not self.target.has_reference:
            return True
        if audio is None or len(audio) < int(0.4 * config.SAMPLE_RATE):
            return True
        try:
            _, score = self.target.is_target(audio)
            if score >= config.INTERRUPT_VERIFY_MIN:
                return True
            logger.info("[stop-guard] interrupt REJETÉ (voix score=%.2f < %.2f — écho TTS ?)",
                        score, config.INTERRUPT_VERIFY_MIN)
            return False
        except Exception:
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
        # ── Gate LOCUTEUR sur le chemin LEGACY (P9-C) : parité avec le turn_end streaming ──
        # Avant, le fallback HTTP (WS KO / STREAMING_MODE off) CONTOURNAIT le gate locuteur
        # (verify backend legacy non bloquante à 0.25) → un tiers NON enrôlé obtenait une
        # réponse complète. On vérifie EN LOCAL sur les 15 dernières s (même fenêtre bornée
        # que le streaming). verify() est fail-open (accepted=True) si pas d'empreinte / audio
        # trop court / erreur → jamais de blocage accidentel du vrai utilisateur.
        if self.target.has_reference:
            name, score, accepted = self.target.verify(pcm[-int(15 * config.SAMPLE_RATE):])
            if not accepted:
                logger.info("[gate] locuteur non reconnu EN LOCAL (%s, %.2f) → rejet (CONVERSING)",
                            name, score)
                play_beep(freq=300.0, dur=0.12)   # même tonalité « voix non reconnue » que le streaming
                return "CONVERSING", False
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

    def _error_beep(self):
        """Bip d'ÉCHEC (grave, distinct du bip aigu « je t'écoute ») : toute fin de tour
        ANORMALE (backend mort en capture, grâce force_eot expirée, panne TTS ElevenLabs,
        mpg123 mort) DOIT s'entendre — fini le silence LED orange (P9-A). Appelé UNIQUEMENT
        depuis le thread principal (jamais depuis le thread feed) — play_beep = Popen aplay
        fire-and-forget, même chemin sonore que le TTS."""
        play_beep(freq=300.0, dur=0.25)

    # ── Chemin B : flux STREAMING (Deepgram Flux décide la fin de tour) ──
    def _handle_command_streaming(self, frames, from_conversing: bool = False,
                                  silent_rearm: bool = False):
        """Stream le PCM au backend → Deepgram Flux décide la fin de tour ('turn_end'),
        puis joue la réponse streamée. Retourne (next_state, from_conversing), ou None si
        le WS échoue OU si le backend renvoie une erreur AVANT tout progrès (→ l'appelant
        retombe sur l'ancien flux fiable). from_conversing → gating intent côté backend (I5).

        silent_rearm : ré-armement INVISIBLE après un tour vide sans parole (budget
        WAKE_PATIENCE_S) — on NE rejoue PAS le bip d'accusé du wake (l'utilisateur hésitait,
        il ne doit percevoir aucune coupure). Un nouveau statut ("RETRY", from_conversing) est
        renvoyé sur ce cas (tour vide SANS parole) pour piloter le budget côté run()."""
        self._spoke = False                      # dette #9 : repart propre (garde MAX_WASTED)
        preroll, self._preroll = self._preroll, None   # début de commande à rejouer (follow-up)
        if not silent_rearm:
            play_beep()                          # FEEDBACK IMMÉDIAT au réveil (avant la connexion)
        url = stream_client.ws_url_from_http(config.CLOUD_BACKEND_URL)
        if from_conversing:
            url += "?from_conversing=1"
        client = stream_client.StreamClient(url, config.DEVICE_TOKEN)
        if not client.connect():
            return None                          # WS KO → fallback ancien flux
        logger.info("[stream] LISTENING (Flux turn-taking) — parlez…")
        self._set_state("LISTENING")
        self.ambient.set_enabled(False)
        # PRE-ROLL (follow-up cyan→vert) : le début du follow-up a été consommé pendant la
        # détection de voix dans _conversing → on le REJOUE ici pour ne rien perdre.
        if preroll is not None and len(preroll):
            for i in range(0, len(preroll), config.FRAME_SAMPLES):
                client.send_pcm(preroll[i:i + config.FRAME_SAMPLES].tobytes())
            logger.info("[stream] pré-roll rejoué (%.1fs)", len(preroll) / config.SAMPLE_RATE)
        # PAS de mic.flush() ICI : on GARDE l'audio capté pendant le bip + la connexion =
        # le DÉBUT de ta commande (prononcé juste après « Dis Aura »). Le flush le jetait
        # → début de commande coupé. Flux ignore le bip (non-parole) et transcrit la commande.
        cmd_audio = [preroll] if (preroll is not None and len(preroll)) else []  # buffer vérif locale
        # PLUS DE CAP 20s : la capture est ILLIMITÉE — c'est l'utilisateur qui clôt
        # (« Stop Aura ») ou Flux (fin de tour détectée). Le cap jetait la commande à 20s
        # pile pendant que « Stop Aura » la soumettait (course du 06/07, commande perdue).
        # Filet TRÈS long (CMD_HARD_CAP_S, 0=désactivé) : au-delà on SOUMET (jamais jeter).
        hard_cap = (time.monotonic() + config.CMD_HARD_CAP_S) if config.CMD_HARD_CAP_S > 0 else None
        grace_deadline = None                    # armée après force_eot : borne l'attente du turn_end
        progressed = False                               # I1 : reçu partial/turn_end ?
        transcript = ""
        heard_speech = False                     # P9-E : un partial NON VIDE = parole entendue
        turn_ended = False
        eot_forced = False                       # « Stop Aura » = fin de commande (1×/tour)
        while not turn_ended:
            # Mute distant (mode confidentiel) : STOPPE la capture — plus RIEN ne part
            # au cloud (avant, la capture continuait à streamer malgré le mute) (P9-C).
            if self._muted.is_set():
                logger.info("[mute] coupure de la capture (mode confidentiel)")
                client.send_cancel()
                client.close()
                return "IDLE", False
            try:
                frame = next(frames)
            except StopIteration:
                break
            client.send_pcm(frame.tobytes())
            cmd_audio.append(frame)              # bufferise pour la vérif locuteur LOCALE
            # « Stop Aura » pendant la prise de commande = FIN DE COMMANDE manuelle
            # (milieu bruyant : Flux ne coupe jamais). Une seule fois par tour.
            if not eot_forced and self.wake.process_interrupt_only(
                    frame, threshold=config.STOP_CAPTURE_THRESHOLD) == "interrupt":
                logger.info("[stream] force EOT (« Stop Aura » pendant la capture)")
                client.send_force_eot()
                eot_forced = True
                grace_deadline = time.monotonic() + config.CMD_FORCE_GRACE_S
            while True:                          # messages backend (non bloquant)
                m = client.recv(timeout=0.0)
                if m is None:
                    break
                kind, data = m
                if kind == "partial":
                    progressed = True
                    txt = data.get("text", "") or ""
                    if txt.strip():
                        heard_speech = True      # P9-E : parole réellement transcrite (pas juste du bruit)
                    self.last_transcript = txt or self.last_transcript
                elif kind == "turn_end":
                    progressed = True
                    transcript = (data.get("transcript") or "").strip()
                    self.last_transcript = transcript or self.last_transcript
                    logger.info("[stream] fin de tour (Flux) — %r", transcript[:60])
                    # VÉRIF LOCUTEUR EN LOCAL (ECAPA sur le Pi, instantané, zéro steal) →
                    # on envoie le résultat au backend qui ne fait plus l'ECAPA lui-même.
                    if transcript and cmd_audio:
                        # Fenêtre ECAPA BORNÉE (P9-C) : la capture est illimitée → cmd_audio
                        # peut atteindre 300s → embedding CPU Pi de plusieurs dizaines de s DANS
                        # la boucle (micro gelé, LED figée) + dépassement de l'attente backend
                        # (10s) → verdict droppé + fail-open. Les 15 dernières s suffisent à ECAPA.
                        pcm = np.concatenate(cmd_audio)[-int(15 * config.SAMPLE_RATE):]
                        name, score, accepted = self.target.verify(pcm)
                        client.send_speaker(accepted, name, score)
                        logger.info("[verify] LOCAL : %s score=%.2f accepted=%s", name, score, accepted)
                    turn_ended = True
                    break
                elif kind in ("final", "error", "closed"):
                    client.close()
                    if not progressed:           # I1 : erreur AVANT tout transcript → repli fiable
                        logger.warning("[stream] erreur backend précoce (%s) → fallback ancien flux", kind)
                        return None
                    # backend/WS mort APRÈS des partials (l'utilisateur PARLAIT) = commande
                    # perdue. Ne PAS retomber en CONVERSING muet : bip d'échec + IDLE (P9-A).
                    self._error_beep()
                    logger.warning("[stream] backend mort en capture (%s) après progrès → "
                                   "commande perdue: %r", kind, self.last_transcript)
                    return "IDLE", False
            now_mono = time.monotonic()
            if grace_deadline is not None and now_mono >= grace_deadline:
                # force_eot envoyé mais AUCUN turn_end après la grâce → backend muet. Au lieu
                # d'un IDLE MUET : bip d'échec + fenêtre de reprise (l'utilisateur reformule
                # sans re-wake). from_conversing préservé pour le gating intent (P9-A).
                logger.warning("[stream] pas de turn_end %.0fs après force EOT → reprise (CONVERSING)",
                               config.CMD_FORCE_GRACE_S)
                client.send_cancel()
                client.close()
                self._error_beep()
                return "CONVERSING", from_conversing
            if hard_cap is not None and not eot_forced and now_mono >= hard_cap:
                # filet anti-blocage (très long) : on SOUMET la commande, on ne la jette pas
                logger.info("[stream] filet %.0fs → soumission forcée de la commande",
                            config.CMD_HARD_CAP_S)
                client.send_force_eot()
                eot_forced = True
                grace_deadline = time.monotonic() + config.CMD_FORCE_GRACE_S
        # turn_end à transcript VIDE → inutile de lancer mpg123 (dette #1/#12)
        if not transcript:
            client.send_cancel()
            client.close()
            if not heard_speech:
                # Tour vide SANS parole = HÉSITATION (rien dit après le wake / Flux clôt sur
                # le silence). RETRY → ré-armement invisible dans run() tant qu'on est dans le
                # budget WAKE_PATIENCE_S (l'utilisateur ne doit percevoir aucune coupure).
                logger.info("[stream] tour vide SANS parole → RETRY (budget d'hésitation)")
                return "RETRY", from_conversing
            # Tour vide APRÈS parole entendue (Flux a transcrit puis le nettoyage a vidé) :
            # double bip descendant doux « rien compris » — distinct du silence — puis reprise.
            logger.info("[stream] tour vide APRÈS parole → CONVERSING (« rien compris »)")
            play_beep_seq(((500.0, 0.1), (350.0, 0.1)))
            return "CONVERSING", False
        # fin de tour → THINKING → on joue la réponse streamée
        self._set_state("THINKING")
        barge = self._play_streamed_response(client, frames)
        client.close()
        if barge == "stop":
            return "IDLE", False
        if barge == "rejected":
            play_beep(freq=300.0, dur=0.12)   # tonalité basse = « voix non reconnue »
            return "IDLE", False
        if barge == "not_directed":
            return "IDLE", False              # bruit ambiant (pas pour Aura) → silence VOULU (P9-A)
        if barge == "failed":
            return "IDLE", False              # échec (bip d'erreur déjà joué dans _play_streamed_response)
        return "CONVERSING", False

    def _play_streamed_response(self, client, frames) -> str | None:
        """Reçoit la réponse (texte + MP3) et la joue. L'audio est joué dans un THREAD
        séparé (le feed bloque sur le backpressure de mpg123) pendant que la boucle principale
        lit le micro EN CONTINU → « Stop Aura » détecté en temps réel (sans ce découplage, le
        feed bloquant gelait le micro → Stop Aura raté même dit 4×). Pendant la lecture on
        n'évalue QUE le modèle interrupt (process_interrupt_only) pour que l'écho ne réarme pas
        le cooldown (C2). mpg123 démarré au 1er chunk audio (pas pendant THINKING — dette #1/#12).
        Garde-fou : abandon si le backend reste muet trop longtemps."""
        self._spoke = False
        self.ambient.set_enabled(False)
        if getattr(self, "mic", None):
            self.mic.flush()         # audio FRAIS → « Stop Aura » jugé en temps réel
        stop = threading.Event()
        done = threading.Event()
        # flags remplis par le thread feed, LUS après le join (P9-A) : status = payload du
        # 'final' (not_directed/empty/…) ; failed = error/closed OU timeout muet avant tout
        # audio OU mpg123 mort ; tts_dead = panne ElevenLabs signalée par le backend.
        # last_activity (P9-B) : horodatage du DERNIER message backend (audio, response,
        # keepalive 'thinking'… tout compte) — initialisé AVANT le start du thread pour que
        # la garde d'inactivité soit armée dès t0 sans jamais tirer à vide au connect.
        flags = {"rejected": False, "status": None, "failed": False, "tts_dead": False,
                 "last_activity": time.monotonic()}

        def feed():
            started = False
            last_msg = time.monotonic()
            try:
                while not stop.is_set():
                    m = client.recv(timeout=0.5)
                    if m is None:
                        if time.monotonic() - last_msg > config.STREAM_RESPONSE_TIMEOUT_S:
                            logger.warning("[stream] pas de réponse backend (%.0fs) → abandon",
                                           config.STREAM_RESPONSE_TIMEOUT_S)
                            if not self._spoke:          # backend muet AVANT tout audio = échec (P9-A)
                                flags["failed"] = True
                            break
                        continue
                    last_msg = time.monotonic()
                    flags["last_activity"] = last_msg   # TOUT message = activité backend (P9-B)
                    kind, data = m
                    if kind == "response":
                        self.last_transcript = data.get("transcript", "") or self.last_transcript
                        logger.info("[stream] réponse: %s", (data.get("text") or "")[:80])
                        self._set_state("SPEAKING")
                        self._spoke = True
                    elif kind == "audio":
                        if not started:
                            self.player.start_stream()   # mpg123 démarré au 1er son (dette #1/#12)
                            started = True
                            if not self.player.is_playing:   # mpg123 absent/mort (parité legacy :601)
                                logger.error("[stream] lecture impossible (mpg123 ?) — réponse non jouée")
                                flags["failed"] = True
                                break
                        self.player.feed(data)           # peut bloquer (backpressure) — OK, thread dédié
                    elif kind == "rejected":
                        logger.info("[stream] locuteur non autorisé — aucune réponse")
                        flags["rejected"] = True
                        break
                    elif kind == "audio_end":
                        break                            # fin NORMALE (audio joué jusqu'au bout)
                    elif kind == "final":
                        flags["status"] = data.get("status")   # not_directed / empty / …
                        break
                    elif kind == "error":
                        if data.get("error") == "tts":   # panne ElevenLabs signalée par le backend
                            flags["tts_dead"] = True
                        else:
                            flags["failed"] = True
                        break
                    elif kind == "closed":
                        flags["failed"] = True
                        break
            except Exception:
                pass
            finally:
                if started and not stop.is_set():
                    self.player.end_stream()
                done.set()

        t = threading.Thread(target=feed, daemon=True)
        t.start()
        # Boucle principale : micro EN CONTINU → « Stop Aura » coupe immédiatement.
        ring = np.zeros(0, dtype=np.int16)              # fenêtre voix pour le stop-guard
        ring_max = int(1.5 * config.SAMPLE_RATE)
        last_guard_reject = -1e9                        # règle d'insistance (2e stop < 8s)
        while not done.is_set():
            # Mute distant (mode confidentiel) : coupure IMMÉDIATE de la lecture (P9-C).
            # Retour None via le join normal → run() bascule en MUTED au cycle suivant.
            if self._muted.is_set():
                logger.info("[mute] coupure de la lecture (mode confidentiel)")
                stop.set()
                self.player.stop()
                client.send_cancel()
                break
            # Garde d'INACTIVITÉ (P9-B) : SPEAK_MAX_S sans AUCUN message backend (ni audio,
            # ni response, ni keepalive 'thinking') = vraie panne (backend/sortie audio).
            # Un tour long ACTIF (génération PPTX…) rafraîchit last_activity en continu →
            # plus JAMAIS un tour légitime coupé par un cap absolu armé sur la 1re phrase.
            if flags.get("last_activity") and \
                    time.monotonic() - flags["last_activity"] > config.SPEAK_MAX_S:
                logger.warning("[stream] aucune activité backend depuis %.0fs → abandon (cancel + bip)",
                               config.SPEAK_MAX_S)
                client.send_cancel()
                stop.set()
                # stop() AVANT le bip : un mpg123 gelé tient plughw:2 (pas de dmix) —
                # aplay se prendrait « device busy » et le bip serait avalé.
                self.player.stop()
                self._error_beep()               # l'échec DOIT s'entendre (thread principal)
                break
            try:
                frame = next(frames)
            except StopIteration:
                break
            ring = np.concatenate([ring, frame])[-ring_max:]
            if self.wake.process_interrupt_only(
                    frame, threshold=config.STOP_SPEAKING_THRESHOLD) == "interrupt":   # C2 : interrupt-only
                # RÈGLE D'INSISTANCE (terrain 07/07) : en milieu très bruyant (TTS+YouTube),
                # la voix mélangée peut échouer la vérif ECAPA. Un humain RÉPÈTE ; un écho
                # ne se re-déclenche pas après reset → 2e stop < STOP_INSIST_S = on coupe.
                insisting = time.monotonic() - last_guard_reject < config.STOP_INSIST_S
                if not insisting and not self._interrupt_is_owner(ring):
                    last_guard_reject = time.monotonic()
                    continue                             # 1er rejet : peut-être écho → on attend l'insistance
                logger.info("[stream] STOP — coupure%s", " (insistance)" if insisting else "")
                stop.set()
                self.player.stop()
                client.send_cancel()                                     # dette #16 : teardown propre
                t.join(timeout=1.0)
                return "stop"
        t.join(timeout=2.0)
        if flags["rejected"]:
            return "rejected"
        if flags["status"] == "not_directed":
            return "not_directed"             # pas pour Aura → IDLE silencieux (mappé côté appelant)
        if flags["failed"] or flags["tts_dead"]:
            self._error_beep()                # échec DOIT s'entendre (thread principal, jamais feed)
            return "failed"
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
        ring = np.zeros(0, dtype=np.int16)              # fenêtre voix pour le stop-guard
        ring_max = int(1.5 * config.SAMPLE_RATE)
        last_guard_reject = -1e9                        # règle d'insistance (2e stop < 8s)
        # Garde-fou legacy (P9-B) : SPEAK_MAX promettait un anti-blocage qui n'existait PAS
        # sur ce chemin (mpg123 figé = SPEAKING infini). Cap absolu suffisant ici : la
        # réponse legacy est déjà entièrement générée (pas de tour long côté backend).
        deadline = time.monotonic() + config.SPEAK_MAX_S
        while self.player.is_playing or t.is_alive():
            if self._muted.is_set():                     # mute distant → coupure (P9-C)
                logger.info("[mute] coupure de la lecture (mode confidentiel)")
                self._teardown_speak(t, stop, res)
                return None
            if time.monotonic() > deadline:
                logger.warning("[speak] lecture > %.0fs (SPEAK_MAX_S) → abandon (sortie audio ?)",
                               config.SPEAK_MAX_S)
                self._teardown_speak(t, stop, res)
                return None
            try:
                frame = next(frames)
            except StopIteration:
                break
            ring = np.concatenate([ring, frame])[-ring_max:]
            # « Stop Aura » = je veux le SILENCE → on coupe et on s'arrête (IDLE).
            # interrupt-only : l'écho TTS ne doit pas réarmer le cooldown du wake (C2).
            if self.wake.process_interrupt_only(
                    frame, threshold=config.STOP_SPEAKING_THRESHOLD) == "interrupt":
                insisting = time.monotonic() - last_guard_reject < config.STOP_INSIST_S
                if not insisting and not self._interrupt_is_owner(ring):
                    last_guard_reject = time.monotonic()
                    continue                             # 1er rejet : peut-être écho → attente d'insistance
                logger.info("[state] STOP — coupure (silence)%s", " (insistance)" if insisting else "")
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
            if self._muted.is_set():                 # mute distant → sortie immédiate (P9-C)
                return "IDLE", False
            if time.time() >= deadline:
                return "IDLE", False
            recent = np.concatenate([recent, frame])[-recent_max:]
            # Aura est MUETTE en CONVERSING → pas d'écho TTS → seuil « Stop Aura »
            # sensible (0.5, comme la capture). Terrain 07/07 : stops 0.5-0.8 avalés à 0.85.
            ev = self.wake.process(frame, interrupt_threshold=config.STOP_CAPTURE_THRESHOLD)
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
                            # PRE-ROLL : le début du follow-up a été consommé pendant la
                            # détection → on le garde pour le rejouer au stream (sinon coupé).
                            self._preroll = recent.copy()
                            return "LISTENING", True   # follow-up : ta voix détectée
                    else:
                        streak = 0
        return "IDLE", False

    # ── Boucle principale ────────────────────────────────────────────
    def _embeddings_loader(self):
        """Recharge les empreintes en arrière-plan tant qu'elles ne sont pas chargées
        (ex : backend down/rebuild au démarrage → récupère tout seul, SANS relance du device)."""
        while not self._stop_watch and self.target.available and not self.target.has_reference:
            time.sleep(20.0)
            if self.target.try_load_references():
                logger.info("[TargetSpeaker] %d empreinte(s) chargées en arrière-plan ✓ "
                            "→ vérif locuteur active", len(self.target._refs))
                return

    def run(self):
        self.target.load_references()   # cache l'empreinte vocale (endpointing local)
        if self.target.available and not self.target.has_reference:
            # backend down/rebuild au démarrage → on recharge en fond (récupère sans relance)
            threading.Thread(target=self._embeddings_loader, daemon=True).start()
        if config.AEC_ENABLED:
            logger.info("[AEC] activé — audio via « %s » (annulation d'écho PipeWire)", config.AEC_ALSA_DEVICE)
        else:
            logger.warning("[AEC] DÉSACTIVÉ (AEC_ENABLED=0) — l'écho TTS peut masquer « Stop Aura » "
                           "pendant la lecture. Active l'AEC (setup_aec.sh + AEC_ENABLED=1) pour un arrêt fiable.")
        self.ambient.start()
        threading.Thread(target=self._state_pusher, daemon=True).start()  # états → front (ordre garanti)
        threading.Thread(target=self._wake_telemetry_pusher, daemon=True).start()  # télémétrie wake → cloud (flush 25s)
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
            listen_started = 0.0    # début de la session d'écoute courante (budget d'hésitation)
            rearm_silent = False    # la prochaine entrée LISTENING est un ré-armement invisible
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
                        rearm_silent = False   # un mute pendant la fenêtre de ré-armement ne doit
                        self._set_state("MUTED")   # pas voler le bip ACK du prochain vrai wake
                    if getattr(self, "mic", None):
                        self.mic.flush()             # jette l'audio capté (aucun traitement)
                    time.sleep(0.2)
                    continue
                if self.state == "MUTED":            # sortie de mute → reprise
                    logger.info("[mute] 🟢 micro réactivé")
                    self._set_state("IDLE")

                # ── Enrôlement vocal demandé par le web (capture guidée par SON micro) ──
                if self._enroll_req is not None:
                    req, self._enroll_req = self._enroll_req, None
                    self._enroll_seen_id = (req or {}).get("id")   # ne plus re-déclencher cette demande
                    self.player.stop()
                    try:
                        enroll.run(self, frames, req)
                    except Exception as e:
                        logger.warning("[enroll] erreur: %s", e)
                    from_conversing = False
                    rearm_silent = False   # même raison que le mute : pas de ré-armement hérité
                    self._set_state("IDLE")
                    continue

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
                    # Départ du budget d'hésitation (WAKE_PATIENCE_S) : posé à la 1re entrée
                    # d'une session d'écoute ; PRÉSERVÉ sur un ré-armement invisible (le budget
                    # court à partir du wake, pas à chaque re-tentative).
                    if not rearm_silent:
                        listen_started = time.monotonic()
                    recent = np.zeros(0, dtype=np.int16)   # le wake-gate a fait son office → purge (#11)
                    # Chemin B : flux streaming (Deepgram Flux décide la fin de tour).
                    streamed = None
                    if config.STREAMING_MODE:
                        streamed = self._handle_command_streaming(
                            frames, from_conversing, silent_rearm=rearm_silent)
                    rearm_silent = False
                    # RETRY = tour vide SANS parole (hésitation). Ne DOIT jamais fuiter vers
                    # _set_state (état invalide) ni vers wasted : on le traite ICI, avant tout.
                    if streamed is not None and streamed[0] == "RETRY":
                        from_conversing = streamed[1]
                        if time.monotonic() - listen_started < config.WAKE_PATIENCE_S:
                            # Dans le budget → ré-armement INVISIBLE : on reste en LISTENING,
                            # on re-capture SANS bip ni changement de LED (aucune coupure perçue).
                            rearm_silent = True
                            continue
                        # Budget épuisé → double bip descendant doux « rien entendu » + IDLE.
                        logger.info("[wake] budget d'hésitation épuisé (%.0fs) → « rien entendu » + IDLE",
                                    config.WAKE_PATIENCE_S)
                        play_beep_seq(((500.0, 0.1), (350.0, 0.1)))
                        from_conversing = False
                        self._set_state("IDLE")
                        continue
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
