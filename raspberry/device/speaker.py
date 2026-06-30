"""Reconnaissance du locuteur cible EN LOCAL (pour l'endpointing robuste).

L'enceinte connaît la voix de l'utilisateur (empreintes ECAPA mises en cache
depuis le cloud). Pendant la prise de commande, on vérifie en temps réel si
c'est bien LUI qui parle — pour s'arrêter quand SA voix s'arrête, en ignorant
les autres voix/bruits de la pièce.

Tout est local (ONNX), aucun round-trip réseau pendant la commande.
Dégradation gracieuse : si le modèle ou l'empreinte manque, is_target renvoie
None et l'orchestrateur retombe sur l'endpointing énergie/VAD.
"""

import os
import time
import logging
from pathlib import Path

import numpy as np

from . import config
from . import cloud

logger = logging.getLogger(__name__)


def _find_ecapa() -> Path | None:
    env = os.getenv("ECAPA_MODEL_PATH")
    candidates = [
        Path(env) if env else None,
        config.OPENWAKE_DIR.parent / "backend" / "app" / "services" / "ecapa_tdnn.onnx",
        config.OPENWAKE_DIR.parent / "device" / "ecapa_tdnn.onnx",
    ]
    for c in candidates:
        if c and c.exists():
            return c
    return None


class TargetSpeaker:
    """is_target(pcm_16k) -> (bool|None, score). None = indéterminable (fallback)."""

    def __init__(self):
        self.available = False
        self._sess = None
        self._refs: list = []   # [(nom, embedding 192-dim)]
        self.threshold = float(os.getenv("TARGET_THRESHOLD", "0.30"))
        # Seuil de VÉRIFICATION (gate locuteur) — plus haut que l'endpointing : seule TA
        # voix répond. Ré-enrôle proprement pour des scores stables, puis monte-le.
        self.verify_threshold = float(os.getenv("SPEAKER_VERIFY_THRESHOLD", "0.35"))
        try:
            import onnxruntime as ort
            path = _find_ecapa()
            if path is None:
                raise FileNotFoundError("ecapa_tdnn.onnx introuvable (ECAPA_MODEL_PATH)")
            self._sess = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
            self._input = self._sess.get_inputs()[0].name
            self.available = True
            logger.info("[TargetSpeaker] ECAPA chargé : %s (seuil %.2f)", path, self.threshold)
        except Exception as e:
            logger.warning("[TargetSpeaker] indisponible (%s) → fallback énergie/VAD", e)

    # ── Empreintes de référence (cache depuis le cloud) ──────────────
    def load_references(self):
        """Récupère les empreintes enrôlées depuis le cloud. RETRY (le VPS lent fait
        parfois échouer/timeout le fetch au démarrage → sans ça, vérif fail-open)."""
        if not self.available:
            return
        for attempt in range(4):
            try:
                self._refs = cloud.fetch_user_embeddings()
                if self._refs:
                    logger.info("[TargetSpeaker] %d empreinte(s) en cache", len(self._refs))
                    return
                logger.info("[TargetSpeaker] aucune voix enrôlée → fallback énergie/VAD")
                return
            except Exception as e:
                logger.warning("[TargetSpeaker] chargement empreintes échoué (essai %d/4): %s",
                               attempt + 1, e)
                time.sleep(2.0)
        self._refs = []
        logger.warning("[TargetSpeaker] empreintes NON chargées après 4 essais → vérif fail-open")

    @property
    def has_reference(self) -> bool:
        return self.available and len(self._refs) > 0

    # ── Préprocessing + embedding (port de speaker_service) ──────────
    @staticmethod
    def _preprocess(audio: np.ndarray) -> np.ndarray:
        audio = audio.astype(np.float32)
        audio = audio - np.mean(audio)
        audio = np.append(audio[0], audio[1:] - 0.97 * audio[:-1])
        peak = np.max(np.abs(audio))
        if peak > 0:
            audio = audio * (0.95 / peak)
        return audio.astype(np.float32)

    def _embed(self, pcm_int16: np.ndarray) -> np.ndarray:
        audio = pcm_int16.astype(np.float32) / 32768.0
        proc = self._preprocess(audio).reshape(1, -1)
        out = self._sess.run(None, {self._input: proc})[0].squeeze()
        norm = np.linalg.norm(out)
        return out / norm if norm > 0 else out

    def is_target(self, pcm_int16: np.ndarray):
        """(True/False, score) si on peut décider ; (None, 0.0) si pas de référence."""
        if not self.has_reference:
            return None, 0.0
        try:
            emb = self._embed(pcm_int16)
            score = max(float(np.dot(emb, ref)) for _, ref in self._refs)
            return score >= self.threshold, score
        except Exception as e:
            logger.warning("[TargetSpeaker] erreur embedding (%s) → fallback", e)
            self.available = False
            return None, 0.0

    def verify(self, pcm_int16: np.ndarray):
        """VÉRIFICATION du locuteur EN LOCAL (gate). Renvoie (nom, score, accepted).
        accepted=True si le meilleur cosine ≥ verify_threshold. Si AUCUNE empreinte enrôlée
        ou erreur → (None, 0.0, True) = fail-open (on laisse passer, comme le backend)."""
        if not self.has_reference:
            return None, 0.0, True
        try:
            emb = self._embed(pcm_int16)
            best_name, best_score = None, -1.0
            for name, ref in self._refs:
                s = float(np.dot(emb, ref))
                if s > best_score:
                    best_score, best_name = s, name
            return best_name, best_score, best_score >= self.verify_threshold
        except Exception as e:
            logger.warning("[TargetSpeaker] verify error (%s) → fail-open", e)
            return None, 0.0, True
