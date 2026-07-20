"""Détection de mot de réveil locale (openWakeWord, ONNX).

Deux modèles :
  - ACTIVATE ("Dis Aura")  -> démarre l'écoute d'une commande
  - INTERRUPT ("Stop Aura") -> coupe la réponse en cours

Seuils par modèle + cooldown pour éviter les déclenchements multiples.
Aucun secret : les modèles ONNX sont publics et embarqués.
"""

import os
import time
import logging
import threading
from datetime import datetime, timezone

import numpy as np
from openwakeword.model import Model

from . import config

logger = logging.getLogger(__name__)


class WakeWord:
    def __init__(self):
        # Ensemble : tous les modèles activate existants (ACTIVATE_MODELS) — le
        # frontend openWakeWord est partagé, chaque tête ONNX de plus est ~gratuite.
        models = []
        for m in config.ACTIVATE_MODELS:
            path = config.OPENWAKE_DIR / m
            if path.exists():
                models.append(str(path))
            else:
                logger.warning("[WakeWord] modèle activate absent, ignoré : %s", path)
        if not models:
            raise FileNotFoundError(
                f"aucun modèle activate trouvé dans {config.OPENWAKE_DIR} "
                f"(ACTIVATE_MODELS={config.ACTIVATE_MODELS})")
        interrupt_path = config.OPENWAKE_DIR / config.INTERRUPT_MODEL
        if interrupt_path.exists():
            models.append(str(interrupt_path))

        self.model = Model(wakeword_models=models, inference_framework="onnx")
        self.names = list(self.model.models.keys())
        self.thresholds = config.WAKE_THRESHOLDS
        self.cooldown = config.WAKE_COOLDOWN_S
        # Cooldown PAR MODÈLE (et non global) : un faux « activate » sur l'écho TTS
        # ne doit PAS désarmer « Stop Aura » (C2). Chaque modèle a son propre dernier-tir.
        self._last = {n: 0.0 for n in self.names}
        self._interrupt_key = next((n for n in self.names if "stop" in n.lower()), None)
        # ── Télémétrie wake (Phase 6.1) : buffer mémoire drainé par l'orchestrateur ──
        # state_hint : l'orchestrateur y écrit l'état courant (IDLE/LISTENING/…) pour
        # que chaque événement soit horodaté avec le contexte. _log_min : plancher des
        # near-miss journalisés (calibration terrain sans avoir à activer WAKE_DEBUG).
        self.state_hint = "IDLE"
        # Dernier déclenchement (modèle+score) — lu par l'orchestrateur pour nommer
        # les fichiers de récolte terrain (WAKE_SAVE_AUDIO).
        self.last_trigger: dict | None = None
        self._events: list = []
        self._events_lock = threading.Lock()
        self._log_min = float(os.getenv("WAKE_LOG_MIN", "0.10"))
        logger.info("[WakeWord] models=%s thresholds=%s", self.names, self.thresholds)

    def _record(self, event: str, model: str, score: float, frame_int16, now: float,
                threshold: float | None = None):
        """Événement wake → buffer mémoire (JAMAIS d'I/O ici — boucle audio temps réel).

        threshold : seuil EFFECTIF du contexte (override capture/lecture/conversing compris).
        Sans lui, la télémétrie journalisait toujours le défaut du modèle (0.85 pour stop)
        même quand un override 0.5/0.70 l'avait avalé → triggers à score<threshold incohérents
        et near-miss mal cadrés → calibration corrompue (audit 07/07). Fallback : défaut modèle."""
        try:
            rms = float(np.sqrt(np.mean((frame_int16.astype(np.float32) / 32768.0) ** 2)))
        except Exception:
            rms = None
        thr = threshold if threshold is not None else self.thresholds.get(model, 0.5)
        with self._events_lock:
            if len(self._events) >= 500:
                self._events.pop(0)
            self._events.append({
                "ts": datetime.now(timezone.utc).isoformat(), "event": event, "model": model,
                "score": round(float(score), 4),
                "threshold": float(thr),
                "rms": (round(rms, 5) if rms is not None else None), "state": self.state_hint,
            })

    def drain_events(self) -> list:
        """Vide le buffer et le renvoie (thread-safe). Swap sous lock : jamais bloquant."""
        with self._events_lock:
            out, self._events = self._events, []
            return out

    def _emit(self, name: str, score: float, now: float) -> str:
        self._last[name] = now
        self.last_trigger = {"model": name, "score": float(score)}
        # Vide le buffer glissant d'openWakeWord : sinon le résidu audio du mot de
        # réveil re-déclenche au process() suivant (re-trigger observé au test terrain).
        try:
            self.model.reset()
        except Exception:
            pass
        if self._interrupt_key and name == self._interrupt_key:
            logger.info("[WakeWord] INTERRUPT (%s=%.2f)", name, score)
            return "interrupt"
        logger.info("[WakeWord] ACTIVATE (%s=%.2f)", name, score)
        return "activate"

    def process(self, frame_int16: np.ndarray, interrupt_threshold: float | None = None) -> str | None:
        """Retourne 'activate', 'interrupt' ou None pour une frame de 1280 samples.
        Cooldown indépendant par modèle (un déclenchement d'un modèle ne bloque pas l'autre).

        `interrupt_threshold` : override contextuel du seuil « Stop Aura » — en CONVERSING,
        Aura est MUETTE (aucun écho TTS) donc le 0.85 anti-écho est inutilement strict
        (terrain 07/07 : stops à 0.5-0.8 avalés en mode suivi avec bruit ambiant)."""
        now = time.time()
        preds = self.model.predict(frame_int16)
        # Calibration : logge le pic réel (même sous le seuil) → savoir si le seuil
        # est trop haut (pic ~0.45) ou le modèle nul (pic ~0.05). À couper en prod.
        if config.WAKE_DEBUG:
            name = max(preds, key=preds.get)
            if preds[name] > 0.1:
                rms = float(np.sqrt(np.mean((frame_int16.astype(np.float32) / 32768.0) ** 2)))
                logger.info("[wake] pic=%.3f (%s) seuil=%.2f rms=%.4f", preds[name], name,
                            self.thresholds.get(name, 0.5), rms)
        for name, score in preds.items():
            thr = self.thresholds.get(name, 0.5)
            if interrupt_threshold is not None and name == self._interrupt_key:
                thr = interrupt_threshold
            if score >= thr and now - self._last.get(name, 0.0) >= self.cooldown:
                ev = self._emit(name, score, now)
                self._record("trigger_interrupt" if ev == "interrupt" else "trigger_activate",
                             name, score, frame_int16, now, threshold=thr)
                return ev
        # Télémétrie : un seul near-miss par frame (le meilleur modèle sous le seuil,
        # mais > _log_min) → calibration terrain des seuils sans WAKE_DEBUG. On journalise
        # le seuil EFFECTIF du meilleur modèle (override interrupt inclus), pas le défaut.
        if preds:
            best = max(preds, key=preds.get)
            if preds[best] > self._log_min:
                eff = self.thresholds.get(best, 0.5)
                if interrupt_threshold is not None and best == self._interrupt_key:
                    eff = interrupt_threshold
                self._record("near_miss", best, preds[best], frame_int16, now, threshold=eff)
        return None

    def process_interrupt_only(self, frame_int16: np.ndarray, threshold: float | None = None) -> str | None:
        """N'évalue QUE « Stop Aura » (avec SON cooldown). À utiliser PENDANT la lecture
        (SPEAKING) : ainsi l'écho TTS ne peut pas déclencher un faux « activate » qui
        réarmerait le cooldown et masquerait un vrai « Stop Aura » (C2). 'interrupt' ou None.

        `threshold` : override CONTEXTUEL. Pendant la CAPTURE (Aura muette → pas d'écho),
        le seuil 0.85 anti-écho est inutilement strict — les vrais « Stop Aura » scorent
        0.65-0.99 (terrain 06/07 : 0.66 rejeté → commande perdue). La capture passe ~0.5."""
        if not self._interrupt_key:
            return None
        now = time.time()
        score = self.model.predict(frame_int16).get(self._interrupt_key, 0.0)
        eff = threshold if threshold is not None else self.thresholds.get(self._interrupt_key, 0.5)
        if config.WAKE_DEBUG and score > 0.1:
            logger.info("[wake] (interrupt-only) pic=%.3f seuil=%.2f", score, eff)
        if score >= eff and \
                now - self._last.get(self._interrupt_key, 0.0) >= self.cooldown:
            self._last[self._interrupt_key] = now
            logger.info("[WakeWord] INTERRUPT (%s=%.2f)", self._interrupt_key, score)
            # Symétrie avec _emit : vide le buffer glissant pour que « stop aura » ne soit
            # pas rejoué au process() suivant (re-trigger observé au test terrain).
            try:
                self.model.reset()
            except Exception:
                pass
            self._record("trigger_interrupt", self._interrupt_key, score, frame_int16, now,
                         threshold=eff)
            return "interrupt"
        # Télémétrie : near-miss du modèle interrupt (sous le seuil mais > _log_min).
        # Seuil EFFECTIF du contexte (eff : override capture/lecture), pas le défaut.
        if score > self._log_min:
            self._record("near_miss", self._interrupt_key, score, frame_int16, now, threshold=eff)
        return None
