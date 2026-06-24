"""Détection de mot de réveil locale (openWakeWord, ONNX).

Deux modèles :
  - ACTIVATE ("Dis Aura")  -> démarre l'écoute d'une commande
  - INTERRUPT ("Stop Aura") -> coupe la réponse en cours

Seuils par modèle + cooldown pour éviter les déclenchements multiples.
Aucun secret : les modèles ONNX sont publics et embarqués.
"""

import time
import logging

import numpy as np
from openwakeword.model import Model

from . import config

logger = logging.getLogger(__name__)


class WakeWord:
    def __init__(self):
        models = [str(config.OPENWAKE_DIR / config.ACTIVATE_MODEL)]
        interrupt_path = config.OPENWAKE_DIR / config.INTERRUPT_MODEL
        if interrupt_path.exists():
            models.append(str(interrupt_path))

        self.model = Model(wakeword_models=models, inference_framework="onnx")
        self.names = list(self.model.models.keys())
        self.thresholds = config.WAKE_THRESHOLDS
        self.cooldown = config.WAKE_COOLDOWN_S
        self._last = 0.0
        self._interrupt_key = next((n for n in self.names if "stop" in n.lower()), None)
        logger.info("[WakeWord] models=%s thresholds=%s", self.names, self.thresholds)

    def process(self, frame_int16: np.ndarray) -> str | None:
        """Retourne 'activate', 'interrupt' ou None pour une frame de 1280 samples."""
        now = time.time()
        preds = self.model.predict(frame_int16)
        # Calibration : logge le pic réel (même sous le seuil) → savoir si le seuil
        # est trop haut (pic ~0.45) ou le modèle nul (pic ~0.05). À couper en prod.
        if config.WAKE_DEBUG:
            name = max(preds, key=preds.get)
            if preds[name] > 0.1:
                logger.info("[wake] pic=%.3f (%s) seuil=%.2f", preds[name], name,
                            self.thresholds.get(name, 0.5))
        if now - self._last < self.cooldown:
            return None
        for name, score in preds.items():
            if score >= self.thresholds.get(name, 0.5):
                self._last = now
                if self._interrupt_key and name == self._interrupt_key:
                    logger.info("[WakeWord] INTERRUPT (%s=%.2f)", name, score)
                    return "interrupt"
                logger.info("[WakeWord] ACTIVATE (%s=%.2f)", name, score)
                return "activate"
        return None
