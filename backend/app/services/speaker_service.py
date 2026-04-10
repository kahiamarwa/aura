"""Speaker verification service using SpeechBrain ECAPA-TDNN.

Adapted from the POC at /Users/badreddine/Desktop/speaker-verification.
Singleton model loading — heavy model is loaded once and reused.
"""

import io
import os
import base64
import wave
import logging
from pathlib import Path

import numpy as np
import torch
from speechbrain.inference import SpeakerRecognition

logger = logging.getLogger(__name__)

# ── Config ──────────────────────────────────────────────────────────────
SIMILARITY_THRESHOLD = 0.40
SAMPLE_RATE = 16000
CHUNK_DURATION = 3.0          # seconds per verification chunk
ENROLLMENT_SEGMENT_DURATION = 5.0  # seconds per enrollment segment
VAD_THRESHOLD = 0.01
MODEL_SOURCE = "speechbrain/spkrec-ecapa-voxceleb"
MODEL_CACHE = str(Path(__file__).resolve().parent / ".speaker_model_cache")


class SpeakerService:
    """Singleton for speaker verification."""

    _instance = None

    def __init__(self):
        logger.info("[SpeakerService] Loading ECAPA-TDNN model...")
        self.model = SpeakerRecognition.from_hparams(
            source=MODEL_SOURCE,
            savedir=MODEL_CACHE,
        )
        logger.info("[SpeakerService] Model loaded.")

    @classmethod
    def get_instance(cls) -> "SpeakerService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ── Audio preprocessing (from POC) ──────────────────────────────────

    @staticmethod
    def preprocess_audio(audio: np.ndarray) -> np.ndarray:
        """DC removal, pre-emphasis, silence trimming, peak normalization."""
        # DC offset
        audio = audio - np.mean(audio)
        # Pre-emphasis
        audio = np.append(audio[0], audio[1:] - 0.97 * audio[:-1])
        # Trim silence
        audio = SpeakerService._trim_silence(audio)
        # Peak normalize
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

    # ── Embedding ───────────────────────────────────────────────────────

    def get_embedding(self, audio: np.ndarray) -> np.ndarray:
        """Extract a 192-dim L2-normalized embedding from audio (float32, 16kHz)."""
        tensor = torch.tensor(audio).unsqueeze(0)
        embedding = self.model.encode_batch(tensor).squeeze().detach().cpu().numpy()
        norm = np.linalg.norm(embedding)
        if norm > 0:
            embedding = embedding / norm
        return embedding

    # ── Enrollment ──────────────────────────────────────────────────────

    def enroll_from_samples(self, audio_samples: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
        """Create enrollment from multiple audio samples (float32, 16kHz).

        Returns:
            (mean_embedding, reference_audio) — both as np.ndarray
        """
        embeddings = []
        valid_audio = []

        for audio in audio_samples:
            processed = self.preprocess_audio(audio)
            if not self.has_speech(processed):
                continue
            emb = self.get_embedding(processed)
            embeddings.append(emb)
            valid_audio.append(processed)

        if not embeddings:
            raise ValueError("Aucun segment avec de la parole détecté.")

        # Filter outliers
        embeddings = self._filter_outliers(embeddings)

        # Mean embedding, L2-normalized
        mean_emb = np.mean(embeddings, axis=0)
        mean_emb = mean_emb / np.linalg.norm(mean_emb)

        # Concatenate valid audio as reference
        ref_audio = np.concatenate(valid_audio)
        return mean_emb, ref_audio

    def enroll_from_wav_bytes(self, wav_bytes: bytes) -> tuple[np.ndarray, np.ndarray]:
        """Create enrollment from an audio file (WAV, webm, ogg, mp3, etc.).

        Segments into ENROLLMENT_SEGMENT_DURATION chunks, computes embeddings,
        filters outliers, returns (mean_embedding, reference_audio).
        """
        # Try WAV first, fallback to ffmpeg for other formats (webm, ogg, etc.)
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
            raise ValueError("Aucun segment avec de la parole détecté.")

        embeddings = self._filter_outliers(embeddings)
        mean_emb = np.mean(embeddings, axis=0)
        mean_emb = mean_emb / np.linalg.norm(mean_emb)

        ref_audio = np.concatenate(valid_segments[:5])  # max ~25s
        return mean_emb, ref_audio

    # ── Verification ────────────────────────────────────────────────────

    def verify(self, audio: np.ndarray, enrolled_embedding: np.ndarray,
               reference_audio: np.ndarray | None = None) -> tuple[float, bool]:
        """Verify audio against an enrolled speaker.

        Uses verify_batch if reference_audio is available, else cosine similarity.
        Returns (score, accepted).
        """
        processed = self.preprocess_audio(audio)
        if not self.has_speech(processed):
            return 0.0, False

        if reference_audio is not None:
            # verify_batch — calibrated scoring from SpeechBrain
            test_tensor = torch.tensor(processed).unsqueeze(0)
            ref_tensor = torch.tensor(reference_audio).unsqueeze(0)
            score_tensor, _ = self.model.verify_batch(test_tensor, ref_tensor)
            score = score_tensor.item()
        else:
            # Cosine similarity fallback
            emb = self.get_embedding(processed)
            score = float(np.dot(emb, enrolled_embedding))

        accepted = score >= SIMILARITY_THRESHOLD
        return score, accepted

    def verify_multi(self, audio: np.ndarray,
                     speakers: list[dict]) -> tuple[str | None, float, bool]:
        """Verify audio against multiple enrolled speakers.

        speakers: list of {"name": str, "embedding": np.ndarray, "reference_audio": np.ndarray|None}
        Returns (best_name, best_score, accepted).
        """
        processed = self.preprocess_audio(audio)
        if not self.has_speech(processed):
            return None, 0.0, False

        best_name = None
        best_score = -1.0

        for spk in speakers:
            ref = spk.get("reference_audio")
            if ref is not None:
                test_tensor = torch.tensor(processed).unsqueeze(0)
                ref_tensor = torch.tensor(ref).unsqueeze(0)
                score_tensor, _ = self.model.verify_batch(test_tensor, ref_tensor)
                score = score_tensor.item()
            else:
                emb = self.get_embedding(processed)
                score = float(np.dot(emb, spk["embedding"]))

            if score > best_score:
                best_score = score
                best_name = spk["name"]

        accepted = best_score >= SIMILARITY_THRESHOLD
        return best_name, best_score, accepted

    # ── Helpers ─────────────────────────────────────────────────────────

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
        """Convert float32 audio to WAV bytes (16kHz, mono, 16-bit)."""
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
        """Convert WAV bytes to float32 array."""
        buf = io.BytesIO(wav_bytes)
        with wave.open(buf, "rb") as wf:
            raw = wf.readframes(wf.getnframes())
            samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        return samples

    @staticmethod
    def _any_audio_to_float32(audio_bytes: bytes) -> np.ndarray:
        """Convert any audio format (webm, ogg, mp3, wav, etc.) to float32 16kHz mono via ffmpeg."""
        import subprocess
        import tempfile

        # Write input to temp file (ffmpeg needs seekable input for some formats)
        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        try:
            cmd = [
                "ffmpeg", "-nostdin", "-i", tmp_path,
                "-f", "s16le",
                "-acodec", "pcm_s16le",
                "-ar", str(SAMPLE_RATE),
                "-ac", "1",
                "-v", "quiet",
                "-y", "pipe:1",
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
        """Convert raw PCM int16 bytes to float32 array."""
        return np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
