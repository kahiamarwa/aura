"""Détection d'activité vocale (VAD).

Utilise Silero VAD (ONNX) si disponible — bien plus robuste au bruit/ronflement
que l'énergie. Repli automatique sur un seuil d'énergie RMS si le modèle ne
charge pas (le device reste fonctionnel quoi qu'il arrive).

Silero est téléchargé par openwakeword (download_models()) sous
.../openwakeword/resources/models/silero_vad.onnx. On gère les interfaces
legacy (h,c) et v5 (state) en inspectant les entrées du modèle.
"""

import logging
from pathlib import Path

import numpy as np

from . import config

logger = logging.getLogger(__name__)


def _find_silero() -> Path | None:
    if config.SILERO_VAD_PATH.exists():
        return config.SILERO_VAD_PATH
    try:
        import openwakeword
        res = Path(openwakeword.__file__).parent / "resources" / "models" / "silero_vad.onnx"
        if res.exists():
            return res
    except Exception:
        pass
    return None


class VAD:
    """is_speech(frame_int16_16k) -> bool. Silero si possible, sinon énergie."""

    def __init__(self):
        self.available = False
        self._buf = np.zeros(0, dtype=np.float32)
        self._sess = None
        self._mode = None  # "v5" | "legacy"
        try:
            import onnxruntime as ort
            path = _find_silero()
            if path is None:
                raise FileNotFoundError("silero_vad.onnx introuvable")
            self._sess = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
            names = {i.name for i in self._sess.get_inputs()}
            if "state" in names:
                self._mode = "v5"
                self._state = np.zeros((2, 1, 128), dtype=np.float32)
                self._ctx = np.zeros((1, 64), dtype=np.float32)
            elif "h" in names and "c" in names:
                self._mode = "legacy"
                self._h = np.zeros((2, 1, 64), dtype=np.float32)
                self._c = np.zeros((2, 1, 64), dtype=np.float32)
            else:
                raise RuntimeError(f"interface Silero inconnue: {names}")
            self._sr = np.array(config.SAMPLE_RATE, dtype=np.int64)
            self.available = True
            logger.info("[VAD] Silero chargé (%s) : %s", self._mode, path)
        except Exception as e:
            logger.warning("[VAD] Silero indisponible (%s) → repli énergie RMS", e)

    def reset(self):
        self._buf = np.zeros(0, dtype=np.float32)
        if self._mode == "v5":
            self._state[:] = 0
            self._ctx[:] = 0
        elif self._mode == "legacy":
            self._h[:] = 0
            self._c[:] = 0

    def _silero_prob(self, frame512: np.ndarray) -> float:
        if self._mode == "v5":
            inp = np.concatenate([self._ctx, frame512.reshape(1, -1)], axis=1).astype(np.float32)
            out, self._state = self._sess.run(None, {"input": inp, "state": self._state, "sr": self._sr})
            self._ctx = frame512.reshape(1, -1)[:, -64:].astype(np.float32)
            return float(np.ravel(out)[0])
        else:
            out, self._h, self._c = self._sess.run(
                None,
                {"input": frame512.reshape(1, -1).astype(np.float32), "h": self._h, "c": self._c, "sr": self._sr},
            )
            return float(np.ravel(out)[0])

    def is_speech(self, frame_int16: np.ndarray) -> bool:
        """True si la frame (int16, 16 kHz) contient de la parole."""
        if not self.available:
            rms = float(np.sqrt(np.mean(frame_int16.astype(np.float32) ** 2)))
            return rms >= config.CMD_SILENCE_RMS

        self._buf = np.concatenate([self._buf, frame_int16.astype(np.float32) / 32768.0])
        prob = 0.0
        n = config.VAD_FRAME_SIZE
        while len(self._buf) >= n:
            chunk, self._buf = self._buf[:n], self._buf[n:]
            try:
                prob = max(prob, self._silero_prob(chunk))
            except Exception as e:
                logger.warning("[VAD] erreur Silero (%s) → repli énergie", e)
                self.available = False
                rms = float(np.sqrt(np.mean(frame_int16.astype(np.float32) ** 2)))
                return rms >= config.CMD_SILENCE_RMS
        return prob >= config.VAD_PROB_THRESHOLD
