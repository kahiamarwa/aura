"""Contexte ambiant : transcription passive de l'environnement.

Pendant que l'enceinte est au repos (IDLE), elle accumule l'audio ambiant et,
toutes les AMBIENT_BATCH_S, l'envoie au cloud pour transcription. Les phrases
captées alimentent un buffer roulant préfixé "[Conversation ambiante]: ",
joint à la commande suivante pour donner du contexte au LLM (parité web).
"""

import time
import queue
import logging
import threading

import numpy as np

from . import config
from . import cloud

logger = logging.getLogger(__name__)


class AmbientContext:
    def __init__(self):
        self._segments: list[tuple[str, float]] = []   # (texte, timestamp)
        self._lock = threading.Lock()
        self._audio_q: "queue.Queue[bytes]" = queue.Queue()
        self._enabled = False
        self._stop = False
        self._thread: threading.Thread | None = None

    # ── Alimentation audio (depuis la boucle, en IDLE) ───────────────
    def feed(self, frame_int16: np.ndarray):
        if self._enabled:
            self._audio_q.put(frame_int16.astype(np.int16).tobytes())

    def set_enabled(self, on: bool):
        """Active la capture ambiante (IDLE) ou la suspend (listening/speaking)."""
        self._enabled = on
        if not on:
            # vider la file pour ne pas mélanger commande et ambiant
            try:
                while True:
                    self._audio_q.get_nowait()
            except queue.Empty:
                pass

    # ── Buffer de contexte ───────────────────────────────────────────
    def add_segment(self, text: str):
        text = text.strip()
        if not text:
            return
        with self._lock:
            self._segments.append((config.AMBIENT_PREFIX + text, time.time()))
            self._prune()

    def _prune(self):
        now = time.time()
        self._segments = [(t, ts) for (t, ts) in self._segments if now - ts < config.MAX_CONTEXT_AGE_S]
        if len(self._segments) > config.MAX_CONTEXT_SEGMENTS:
            self._segments = self._segments[-config.MAX_CONTEXT_SEGMENTS:]

    def get_context(self) -> list[str]:
        with self._lock:
            self._prune()
            return [t for (t, _) in self._segments]

    # ── Boucle de transcription (thread) ─────────────────────────────
    def start(self):
        if not config.AMBIENT_ENABLED:
            logger.info("[ambient] désactivé (AMBIENT_ENABLED=0)")
            return
        self._stop = False
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info("[ambient] STT passif démarré (batch %.0fs)", config.AMBIENT_BATCH_S)

    def stop(self):
        self._stop = True

    def _loop(self):
        min_bytes = int(config.SAMPLE_RATE * 2 * 1.0)  # ~1s @16kHz 16-bit
        while not self._stop:
            deadline = time.time() + config.AMBIENT_BATCH_S
            chunks = bytearray()
            while time.time() < deadline:
                try:
                    chunks += self._audio_q.get(timeout=0.5)
                except queue.Empty:
                    pass
                if self._stop:
                    return
            if len(chunks) < min_bytes:
                continue
            try:
                pcm = np.frombuffer(bytes(chunks), dtype=np.int16)
                text = cloud.transcribe(pcm)
                if text:
                    self.add_segment(text)
                    logger.info("[ambient] %s", text[:80])
            except Exception as e:
                logger.warning("[ambient] transcription échouée: %s", e)
