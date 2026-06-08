from pathlib import Path

import os

import numpy as np
from openwakeword.model import Model


def _resolve_model(filename: str) -> str:
    docker_path = f"/openwake/{filename}"
    local_path = str(Path(__file__).resolve().parents[3] / "openwake" / filename)
    return docker_path if os.path.exists(docker_path) else local_path


_ACTIVATE_MODEL = _resolve_model("dis_aura.onnx")
_INTERRUPT_MODEL = _resolve_model("stop_aura.onnx")


import time


class WakeWordService:
    """Singleton qui charge les modeles openWakeWord (activate + interrupt)."""

    _instance = None

    # Per-model thresholds (stop_aura has more false positives → higher threshold)
    THRESHOLDS = {
        "dis_aura": 0.5,
        "stop_aura": 0.85,
    }
    COOLDOWN_SECONDS = 1.5

    def __init__(self):
        models_to_load = [_ACTIVATE_MODEL]
        if os.path.exists(_INTERRUPT_MODEL):
            models_to_load.append(_INTERRUPT_MODEL)

        self.model = Model(
            wakeword_models=models_to_load,
            inference_framework="onnx",
        )
        self.model_names = list(self.model.models.keys())
        self.last_detection_time = 0.0
        print(f"[WakeWordService] Models loaded: {models_to_load}")
        print(f"[WakeWordService] Names: {self.model_names}, thresholds: {self.THRESHOLDS}")

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def process_audio(self, audio_int16: np.ndarray) -> dict | None:
        """Retourne {"model": name, "score": float} si detection, sinon None."""
        now = time.time()
        # Cooldown to prevent repeated detections of same utterance
        if now - self.last_detection_time < self.COOLDOWN_SECONDS:
            self.model.predict(audio_int16)
            return None

        predictions = self.model.predict(audio_int16)
        for name, score in predictions.items():
            threshold = self.THRESHOLDS.get(name, 0.5)
            if score > threshold:
                self.last_detection_time = now
                return {"model": name, "score": float(score)}
        return None
