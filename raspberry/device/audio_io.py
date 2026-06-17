"""Entrées/sorties audio du device : capture micro + lecture TTS.

Capture : sounddevice (ReSpeaker / carte par défaut), frames int16 16 kHz mono.
Lecture : MP3 décodé par `mpg123` (léger, standard sur Raspberry Pi OS),
          interruptible (pour le barge-in / "Stop Aura").
"""

import queue
import logging
import subprocess
from math import gcd

import numpy as np
from scipy.signal import resample_poly
import sounddevice as sd

from . import config

logger = logging.getLogger(__name__)


class MicStream:
    """Flux micro continu. Itère des frames int16 de FRAME_SAMPLES à 16 kHz.

    Beaucoup de cartes USB (ex: SF-558) ne supportent pas le 16 kHz natif.
    On capture alors au taux supporté (48 kHz) et on sous-échantillonne en 16 kHz.
    """

    def __init__(self):
        self._q: "queue.Queue[bytes]" = queue.Queue()
        self._native_rate = self._pick_rate()
        ratio = self._native_rate // config.SAMPLE_RATE if self._native_rate >= config.SAMPLE_RATE else 1
        blocksize = config.FRAME_SAMPLES * max(ratio, 1)
        self._stream = sd.RawInputStream(
            samplerate=self._native_rate,
            blocksize=blocksize,
            dtype="int16",
            channels=1,
            device=config.INPUT_DEVICE,
            callback=self._cb,
        )

    def _pick_rate(self) -> int:
        """16 kHz si supporté, sinon 48 kHz (puis sous-échantillonnage ×3)."""
        for rate in (config.SAMPLE_RATE, 48000, 44100):
            try:
                sd.check_input_settings(
                    device=config.INPUT_DEVICE, samplerate=rate, channels=1, dtype="int16"
                )
                return rate
            except Exception:
                continue
        return 48000

    def _cb(self, indata, frames, time_info, status):
        if status:
            logger.debug("[mic] status: %s", status)
        self._q.put(bytes(indata))

    def __enter__(self):
        self._stream.start()
        logger.info(
            "[mic] capture démarrée (%d Hz%s)",
            self._native_rate,
            "" if self._native_rate == config.SAMPLE_RATE else f" → resample {config.SAMPLE_RATE} Hz",
        )
        return self

    def __exit__(self, *a):
        self._stream.stop()
        self._stream.close()

    def frames(self):
        """Générateur infini de frames int16 16 kHz (np.ndarray de FRAME_SAMPLES)."""
        while True:
            raw = self._q.get()
            frame = np.frombuffer(raw, dtype=np.int16)
            if self._native_rate != config.SAMPLE_RATE:
                g = gcd(config.SAMPLE_RATE, self._native_rate)
                frame = resample_poly(
                    frame, config.SAMPLE_RATE // g, self._native_rate // g
                ).astype(np.int16)
            yield frame


class Player:
    """Lecture d'un flux MP3 via mpg123, interruptible."""

    def __init__(self):
        self._proc: subprocess.Popen | None = None

    def play_mp3(self, mp3_bytes: bytes):
        """Joue le MP3 (bloquant). Coupé si stop() est appelé depuis un autre thread."""
        self.stop()
        try:
            self._proc = subprocess.Popen(
                ["mpg123", "-q", "-"],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._proc.stdin.write(mp3_bytes)
            self._proc.stdin.close()
            self._proc.wait()
        except FileNotFoundError:
            logger.error("[player] mpg123 introuvable — installe-le : sudo apt install mpg123")
        except Exception as e:
            logger.warning("[player] lecture interrompue: %s", e)
        finally:
            self._proc = None

    def stop(self):
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            self._proc = None

    @property
    def is_playing(self) -> bool:
        return self._proc is not None and self._proc.poll() is None
