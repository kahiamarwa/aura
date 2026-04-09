from pathlib import Path

import os

import numpy as np
from openwakeword.model import Model

_CUSTOM_DOCKER = "/openwake/Aura_test.onnx"
_CUSTOM_LOCAL = str(Path(__file__).resolve().parents[3] / "openwake" / "Aura_test.onnx")
_MODEL_PATH = os.getenv("WAKEWORD_MODEL_PATH", _CUSTOM_DOCKER if os.path.exists(_CUSTOM_DOCKER) else _CUSTOM_LOCAL)


class WakeWordService:
    """Singleton qui charge le modele openWakeWord une seule fois."""

    _instance = None

    def __init__(self):
        self.model = Model(
            wakeword_models=[_MODEL_PATH],
            inference_framework="onnx",
        )
        self.model_names = list(self.model.models.keys())
        self.threshold = 0.3
        print(f"[WakeWordService] Model: {_MODEL_PATH}")
        print(f"[WakeWordService] Models: {self.model_names}, threshold: {self.threshold}")

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def process_audio(self, audio_int16: np.ndarray) -> dict | None:
        """Retourne {"model": name, "score": float} si detection, sinon None."""
        predictions = self.model.predict(audio_int16)
        for name, score in predictions.items():
            if score > self.threshold:
                return {"model": name, "score": float(score)}
        return None
