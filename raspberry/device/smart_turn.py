"""Détection de FIN DE TOUR de parole (Pipecat Smart Turn v3), 100% LOCALE sur le Pi.

Audio-only : décide « l'utilisateur a-t-il fini ? » sur le son (sémantique + prosodie),
sans transcript. Tourne aux PAUSES (silences VAD), pas en continu. ~70-200ms / Pi 4.

Reproduit `WhisperFeatureExtractor(chunk_length=8, do_normalize=True)` en NUMPY PUR
(vérifié bit-pour-bit vs transformers, max_abs_diff ~1e-5). Dépendances : numpy +
onnxruntime uniquement (ni transformers, ni librosa, ni torch).

Dégradation gracieuse : si onnxruntime/le modèle manque → available=False,
predict_endpoint → None (l'orchestrateur retombe sur l'endpointing silence classique).

Licence du modèle : BSD-2 (pipecat-ai/smart-turn-v3) → OK produit vendu.
"""
import logging

import numpy as np

from . import config

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
N_FFT = 400          # = frame_length = fft_length
HOP_LENGTH = 160
N_MELS = 80
CHUNK_SEC = 8        # fenêtre de 8s = 128000 samples


# ── Mel filterbank slaney (port de transformers.audio_utils.mel_filter_bank) ──
def _hz_to_mel(freq):
    freq = np.atleast_1d(np.asarray(freq, dtype=np.float64))
    logstep = 27.0 / np.log(6.4)
    mels = 3.0 * freq / 200.0
    m = freq >= 1000.0
    mels[m] = 15.0 + np.log(freq[m] / 1000.0) * logstep
    return mels


def _mel_to_hz(mels):
    mels = np.atleast_1d(np.asarray(mels, dtype=np.float64))
    logstep = np.log(6.4) / 27.0
    freq = 200.0 * mels / 3.0
    m = mels >= 15.0
    freq[m] = 1000.0 * np.exp(logstep * (mels[m] - 15.0))
    return freq


def _mel_filters():
    nfreq = 1 + N_FFT // 2                       # 201
    mel_freqs = np.linspace(_hz_to_mel(0.0)[0], _hz_to_mel(8000.0)[0], N_MELS + 2)
    filt = _mel_to_hz(mel_freqs)
    fft_freqs = np.linspace(0, SAMPLE_RATE // 2, nfreq)
    diff = np.diff(filt)
    slopes = filt[None, :] - fft_freqs[:, None]
    down = -slopes[:, :-2] / diff[:-1]
    up = slopes[:, 2:] / diff[1:]
    fb = np.maximum(0.0, np.minimum(down, up))   # [201, 80]
    enorm = 2.0 / (filt[2:N_MELS + 2] - filt[:N_MELS])
    fb *= enorm[None, :]
    return fb


_MEL = _mel_filters()                # [201, 80]
_HANN = np.hanning(N_FFT + 1)[:-1]    # hann périodique


def smart_turn_features(audio_16k: np.ndarray) -> np.ndarray:
    """Audio 16k mono float → input_features [1, 80, 800] (Whisper log-mel exact)."""
    audio = np.asarray(audio_16k, dtype=np.float32)
    n = CHUNK_SEC * SAMPLE_RATE                       # 128000
    # 1) garder les 8 dernières secondes / pad zéros au DÉBUT
    if len(audio) > n:
        audio = audio[-n:]
    elif len(audio) < n:
        audio = np.pad(audio, (n - len(audio), 0))
    # 2) zero-mean unit-var sur le waveform (do_normalize=True)
    audio = ((audio - audio.mean()) / np.sqrt(audio.var() + 1e-7)).astype(np.float32)
    # 3) STFT center=True (reflect pad 200), hann, power, mel, log10
    w = np.pad(audio, (N_FFT // 2, N_FFT // 2), mode="reflect")
    nframes = 1 + (w.shape[0] - N_FFT) // HOP_LENGTH  # 801
    idx = np.arange(N_FFT)[None, :] + HOP_LENGTH * np.arange(nframes)[:, None]
    stft = np.fft.rfft(w[idx] * _HANN, n=N_FFT, axis=1)          # [801, 201]
    power = (np.abs(stft).astype(np.float64) ** 2.0).T           # [201, 801]
    mel = np.maximum(1e-10, _MEL.T @ power)                      # [80, 801]
    log = np.log10(mel)
    # 4) post-traitement Whisper
    log = log[:, :-1]                                            # → 800
    log = np.maximum(log, log.max() - 8.0)
    log = (log + 4.0) / 4.0
    return np.expand_dims(log.astype(np.float32), 0)             # [1, 80, 800]


def _find_model() -> str:
    """Chemin du modèle : config explicite, sinon download HF (préfère la variante CPU int8)."""
    if config.SMART_TURN_MODEL:
        return config.SMART_TURN_MODEL
    from huggingface_hub import hf_hub_download, list_repo_files
    repo = "pipecat-ai/smart-turn-v3"
    files = [f for f in list_repo_files(repo) if f.endswith(".onnx")]
    # préfère la variante CPU int8 (-cpu), sinon la première
    pick = next((f for f in files if "cpu" in f.lower()), files[0])
    return hf_hub_download(repo, pick)


class SmartTurn:
    """predict_endpoint(audio_16k) → proba que le tour soit FINI (0..1), ou None si indispo."""

    def __init__(self):
        self.available = False
        self._sess = None
        self.threshold = config.SMART_TURN_THRESHOLD
        if not config.SMART_TURN_ENABLED:
            return
        try:
            import onnxruntime as ort
            path = _find_model()
            so = ort.SessionOptions()
            so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            self._sess = ort.InferenceSession(
                str(path), sess_options=so, providers=["CPUExecutionProvider"])
            self.available = True
            logger.info("[SmartTurn] chargé : %s (seuil %.2f)", path, self.threshold)
        except Exception as e:
            logger.warning("[SmartTurn] indisponible (%s) → endpointing silence classique", e)
            self._sess = None

    def predict_endpoint(self, audio_16k: np.ndarray) -> float | None:
        """Proba (sigmoïde, déjà dans le graphe ONNX) que le tour soit COMPLET.
        > threshold (0.5) ⇒ l'utilisateur a fini. None si modèle indisponible."""
        if not self._sess:
            return None
        try:
            feats = smart_turn_features(audio_16k)
            out = self._sess.run(None, {"input_features": feats})
            return float(out[0][0].item())
        except Exception as e:
            logger.debug("[SmartTurn] erreur inférence: %s", e)
            return None

    def is_complete(self, audio_16k: np.ndarray) -> bool | None:
        """True si le tour est fini, False sinon, None si indispo."""
        p = self.predict_endpoint(audio_16k)
        return None if p is None else (p >= self.threshold)
