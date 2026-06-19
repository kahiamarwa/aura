"""Speaker verification service using ONNX Runtime (ECAPA-TDNN).

Uses a pre-exported ONNX model (~0.7MB) instead of PyTorch+SpeechBrain (~1.5GB).
10x faster inference on CPU, compatible with Raspberry Pi.
"""

import io
import os
import base64
import wave
import logging
from pathlib import Path

import numpy as np
import onnxruntime as ort

logger = logging.getLogger(__name__)

# ── Config ──────────────────────────────────────────────────────────────
# 0.25 (et non 0.40) : l'ECAPA score le vrai utilisateur ~0.33 sur une commande
# propre ; 0.40 le rejetait de justesse. Les autres locuteurs scorent bien plus
# bas (souvent <0.15 ou négatif) → la sécurité tient. Ajustable SPEAKER_THRESHOLD.
SIMILARITY_THRESHOLD = float(os.getenv("SPEAKER_THRESHOLD", "0.25"))
SAMPLE_RATE = 16000
ENROLLMENT_SEGMENT_DURATION = 5.0  # seconds per enrollment segment
VAD_THRESHOLD = 0.01
ONNX_MODEL_PATH = str(Path(__file__).resolve().parent / "ecapa_tdnn.onnx")


class SpeakerService:
    """Singleton for speaker verification using ONNX Runtime."""

    _instance = None

    def __init__(self):
        logger.info("[SpeakerService] Loading ECAPA-TDNN ONNX model from %s", ONNX_MODEL_PATH)
        self.session = ort.InferenceSession(
            ONNX_MODEL_PATH,
            providers=["CPUExecutionProvider"],
        )
        logger.info("[SpeakerService] ONNX model loaded.")

    @classmethod
    def get_instance(cls) -> "SpeakerService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ── Audio preprocessing ───────────────────────────────────────────

    @staticmethod
    def preprocess_audio(audio: np.ndarray) -> np.ndarray:
        """DC removal, pre-emphasis, silence trimming, peak normalization."""
        audio = audio - np.mean(audio)
        audio = np.append(audio[0], audio[1:] - 0.97 * audio[:-1])
        audio = SpeakerService._trim_silence(audio)
        peak = np.max(np.abs(audio))
        if peak > 0:
            audio = audio * (0.95 / peak)
        return audio.astype(np.float32)

    @staticmethod
    def _trim_silence(audio: np.ndarray, frame_length: int = 512, threshold_db: float = -40.0) -> np.ndarray:
        threshold = 10 ** (threshold_db / 20.0)
        num_frames = len(audio) // frame_length
        start = 0
        for i in range(num_frames):
            frame = audio[i * frame_length:(i + 1) * frame_length]
            if np.sqrt(np.mean(frame ** 2)) > threshold:
                start = i * frame_length
                break
        end = len(audio)
        for i in range(num_frames - 1, -1, -1):
            frame = audio[i * frame_length:(i + 1) * frame_length]
            if np.sqrt(np.mean(frame ** 2)) > threshold:
                end = min((i + 1) * frame_length, len(audio))
                break
        trimmed = audio[start:end]
        if len(trimmed) < SAMPLE_RATE // 2:
            return audio
        return trimmed

    @staticmethod
    def has_speech(audio: np.ndarray) -> bool:
        rms = float(np.sqrt(np.mean(audio ** 2)))
        return rms > VAD_THRESHOLD

    # ── Embedding (ONNX) ─────────────────────────────────────────────

    def get_embedding(self, audio: np.ndarray) -> np.ndarray:
        """Extract a 192-dim L2-normalized embedding using ONNX Runtime."""
        audio_input = audio.reshape(1, -1).astype(np.float32)
        outputs = self.session.run(None, {"audio": audio_input})
        embedding = outputs[0].squeeze()
        norm = np.linalg.norm(embedding)
        if norm > 0:
            embedding = embedding / norm
        return embedding

    # ── Enrollment ────────────────────────────────────────────────────

    def enroll_from_wav_bytes(self, wav_bytes: bytes) -> tuple[np.ndarray, np.ndarray]:
        """Create enrollment from an audio file (WAV, webm, ogg, mp3, etc.)."""
        try:
            audio = self._wav_bytes_to_float32(wav_bytes)
        except Exception:
            logger.info("[SpeakerService] Not a WAV file, converting via ffmpeg...")
            audio = self._any_audio_to_float32(wav_bytes)

        segment_len = int(ENROLLMENT_SEGMENT_DURATION * SAMPLE_RATE)
        num_segments = max(1, len(audio) // segment_len)

        embeddings = []
        valid_segments = []

        for i in range(num_segments):
            start = i * segment_len
            end = start + segment_len
            seg = audio[start:end]
            if len(seg) < SAMPLE_RATE:
                continue
            if not self.has_speech(seg):
                continue
            processed = self.preprocess_audio(seg)
            emb = self.get_embedding(processed)
            embeddings.append(emb)
            valid_segments.append(processed)

        if not embeddings:
            raise ValueError("Aucun segment avec de la parole detecte.")

        embeddings = self._filter_outliers(embeddings)
        mean_emb = np.mean(embeddings, axis=0)
        mean_emb = mean_emb / np.linalg.norm(mean_emb)

        ref_audio = np.concatenate(valid_segments[:5])
        return mean_emb, ref_audio

    # ── Verification ──────────────────────────────────────────────────

    def verify_multi(self, audio: np.ndarray,
                     speakers: list[dict]) -> tuple[str | None, float, bool]:
        """Verify audio against multiple enrolled speakers (cosine similarity)."""
        processed = self.preprocess_audio(audio)
        rms = float(np.sqrt(np.mean(processed**2)))
        logger.info("[verify_multi] processed: len=%d rms=%.6f has_speech=%s", len(processed), rms, self.has_speech(processed))

        if not self.has_speech(processed):
            logger.warning("[verify_multi] No speech detected (rms=%.6f)", rms)
            return None, 0.0, False

        emb = self.get_embedding(processed)
        best_name = None
        best_score = -1.0

        for spk in speakers:
            score = float(np.dot(emb, spk["embedding"]))
            logger.info("[verify_multi] '%s' cosine=%.4f", spk["name"], score)
            if score > best_score:
                best_score = score
                best_name = spk["name"]

        accepted = best_score >= SIMILARITY_THRESHOLD
        logger.info("[verify_multi] RESULT: best=%s score=%.4f accepted=%s", best_name, best_score, accepted)
        return best_name, best_score, accepted

    # ── Diarization (sliding window) ─────────────────────────────────

    # Lower threshold for per-window diarization (vs 0.40 for global verify):
    # individual 1.5s windows have less data → noisier embeddings → lower scores
    # while still being the same speaker overall.
    DIARIZATION_THRESHOLD = 0.30

    def diarize_against_speakers(
        self,
        audio: np.ndarray,
        speakers: list[dict],
        window_sec: float = 1.5,
        hop_sec: float = 0.5,
    ) -> list[dict]:
        """Slide a window over audio, identify which (if any) enrolled speaker matches each window.

        Args:
            audio: float32 mono audio at SAMPLE_RATE (16kHz)
            speakers: list of {"name": str, "embedding": np.ndarray (192-dim L2-normed)}
            window_sec: window length in seconds
            hop_sec: hop between windows in seconds

        Returns:
            list of segments: {"start_ms": int, "end_ms": int, "speaker": str|None, "score": float}
        """
        if len(audio) < int(SAMPLE_RATE * window_sec):
            # Audio too short for sliding window — treat as single segment
            name, score, _ = self.verify_multi(audio, speakers)
            return [{
                "start_ms": 0,
                "end_ms": int(len(audio) / SAMPLE_RATE * 1000),
                "speaker": name if score >= SIMILARITY_THRESHOLD else None,
                "score": float(score),
            }]

        window_samples = int(SAMPLE_RATE * window_sec)
        hop_samples = int(SAMPLE_RATE * hop_sec)
        segments: list[dict] = []

        for start in range(0, len(audio) - window_samples + 1, hop_samples):
            end = start + window_samples
            window = audio[start:end]

            if not self.has_speech(window):
                segments.append({
                    "start_ms": int(start / SAMPLE_RATE * 1000),
                    "end_ms": int(end / SAMPLE_RATE * 1000),
                    "speaker": None,
                    "score": 0.0,
                })
                continue

            processed = self.preprocess_audio(window)
            emb = self.get_embedding(processed)

            best_name = None
            best_score = -1.0
            for spk in speakers:
                score = float(np.dot(emb, spk["embedding"]))
                if score > best_score:
                    best_score = score
                    best_name = spk["name"]

            segments.append({
                "start_ms": int(start / SAMPLE_RATE * 1000),
                "end_ms": int(end / SAMPLE_RATE * 1000),
                "speaker": best_name if best_score >= self.DIARIZATION_THRESHOLD else None,
                "score": best_score,
            })

        return segments

    # ── Helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _filter_outliers(embeddings: list[np.ndarray]) -> list[np.ndarray]:
        if len(embeddings) <= 2:
            return embeddings
        normed = [e / np.linalg.norm(e) for e in embeddings]
        avg_scores = []
        for i in range(len(normed)):
            scores = [float(np.dot(normed[i], normed[j]))
                      for j in range(len(normed)) if i != j]
            avg_scores.append(np.mean(scores))
        median = np.median(avg_scores)
        filtered = [embeddings[i] for i in range(len(embeddings))
                     if avg_scores[i] >= median]
        return filtered if len(filtered) >= 2 else embeddings

    @staticmethod
    def embedding_to_base64(embedding: np.ndarray) -> str:
        buf = io.BytesIO()
        np.save(buf, embedding)
        return base64.b64encode(buf.getvalue()).decode("ascii")

    @staticmethod
    def embedding_from_base64(b64: str) -> np.ndarray:
        buf = io.BytesIO(base64.b64decode(b64))
        return np.load(buf)

    @staticmethod
    def audio_to_wav_bytes(audio: np.ndarray) -> bytes:
        int16_data = (audio * 32767).astype(np.int16)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(int16_data.tobytes())
        return buf.getvalue()

    @staticmethod
    def _wav_bytes_to_float32(wav_bytes: bytes) -> np.ndarray:
        buf = io.BytesIO(wav_bytes)
        with wave.open(buf, "rb") as wf:
            raw = wf.readframes(wf.getnframes())
            samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        return samples

    @staticmethod
    def _any_audio_to_float32(audio_bytes: bytes) -> np.ndarray:
        import subprocess
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        try:
            cmd = [
                "ffmpeg", "-nostdin", "-i", tmp_path,
                "-f", "s16le", "-acodec", "pcm_s16le",
                "-ar", str(SAMPLE_RATE), "-ac", "1",
                "-v", "quiet", "-y", "pipe:1",
            ]
            result = subprocess.run(cmd, capture_output=True, timeout=30, stdin=subprocess.DEVNULL)
            if result.returncode != 0:
                raise RuntimeError(f"ffmpeg failed: {result.stderr.decode()[:200]}")
            samples = np.frombuffer(result.stdout, dtype=np.int16).astype(np.float32) / 32768.0
            return samples
        finally:
            os.unlink(tmp_path)

    @staticmethod
    def pcm_int16_to_float32(pcm_bytes: bytes) -> np.ndarray:
        return np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
