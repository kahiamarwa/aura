"""Entrées/sorties audio du device : capture micro + lecture TTS.

Capture : sounddevice (ReSpeaker / carte par défaut), frames int16 16 kHz mono.
Lecture : MP3 décodé par `mpg123` (léger, standard sur Raspberry Pi OS),
          interruptible (pour le barge-in / "Stop Aura").
"""

import os
import queue
import logging
import subprocess
from math import gcd

import numpy as np
from scipy.signal import resample_poly
import sounddevice as sd

from . import config

logger = logging.getLogger(__name__)


def _mpg123_cmd() -> list[str]:
    """Commande mpg123 (MP3 sur stdin). Si AEC activé, sort via le bridge ALSA→
    PipeWire (config.PLAYBACK_ALSA_DEVICE) pour servir de référence d'écho."""
    cmd = ["mpg123", "-q"]
    if config.PLAYBACK_ALSA_DEVICE:
        cmd += ["-o", "alsa", "-a", config.PLAYBACK_ALSA_DEVICE]
    cmd.append("-")
    return cmd


def _aplay_cmd(path: str) -> list[str]:
    """Commande aplay, routée via le bridge AEC si activé."""
    cmd = ["aplay", "-q"]
    if config.PLAYBACK_ALSA_DEVICE:
        cmd += ["-D", config.PLAYBACK_ALSA_DEVICE]
    cmd.append(path)
    return cmd


class MicStream:
    """Flux micro continu. Itère des frames int16 de FRAME_SAMPLES à 16 kHz.

    Beaucoup de cartes USB (ex: SF-558) ne supportent pas le 16 kHz natif.
    On capture alors au taux supporté (48 kHz) et on sous-échantillonne en 16 kHz.
    """

    def __init__(self, on_lost=None, on_back=None):
        # Callbacks optionnels : micro perdu (USB coupé) / micro de retour → LED.
        self._q: "queue.Queue[bytes]" = queue.Queue()
        self._on_lost = on_lost
        self._on_back = on_back
        self._lost = False
        self._stream = None
        self._open()

    def _open(self):
        """(Re)crée le flux sounddevice sur le périphérique courant (réutilisé au
        rebranchement USB). Peut lever si le micro est absent → géré par l'appelant."""
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

    def _try_reopen(self) -> bool:
        """Tente de rouvrir le micro (USB rebranché). True si le flux redémarre."""
        try:
            if self._stream:
                self._stream.stop()
                self._stream.close()
        except Exception:
            pass
        try:
            self._open()
            self._stream.start()
            return True
        except Exception:
            return False

    def _mark_lost(self, reason: str):
        if not self._lost:
            self._lost = True
            logger.warning("[mic] micro perdu (%s) — LED rouge, j'attends son retour", reason)
            if self._on_lost:
                try:
                    self._on_lost()
                except Exception:
                    pass

    def _mark_back(self):
        if self._lost:
            self._lost = False
            logger.info("[mic] micro de retour ✓")
            if self._on_back:
                try:
                    self._on_back()
                except Exception:
                    pass

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
        try:
            if self._stream:
                self._stream.stop()
                self._stream.close()
        except Exception:
            pass

    def flush(self):
        """Vide le backlog audio accumulé pendant un traitement long (ex: appel
        cloud en THINKING). SANS ça, le barge-in « Stop Aura » traite des frames
        PÉRIMÉES → détecté en retard (après qu'Aura ait fini de parler)."""
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            pass

    def frames(self):
        """Générateur infini de frames int16 16 kHz (np.ndarray de FRAME_SAMPLES).

        Auto-récup USB : si le micro disparaît (interrupteur/USB coupé), on le
        signale (callback → LED rouge) et on tente de le ROUVRIR en boucle ; dès
        qu'il revient, l'audio reprend tout seul (callback → LED restaurée). Pas
        de blocage silencieux ni de redémarrage manuel d'Aura.
        """
        empties = 0
        flat = 0
        _frame_s = config.FRAME_SAMPLES / config.SAMPLE_RATE     # ~0.08s par frame
        flat_limit = max(10, int(config.MIC_DEAD_S / _frame_s))  # ~MIC_DEAD_S de silence plat
        while True:
            try:
                raw = self._q.get(timeout=2.0)
            except queue.Empty:
                # Cas SANS AEC : le flux meurt → plus aucune frame. → perdu + réouverture.
                empties += 1
                if empties >= 2:
                    self._mark_lost("USB coupé ?")
                    self._try_reopen()
                # ANTI-BRIQUE (C1) : on YIELD du silence au lieu de bloquer. Sinon le
                # consommateur (boucle SPEAKING/écoute) reste figé sur next(frames) et ne
                # réévalue jamais sa condition de sortie → enceinte bloquée jusqu'au reboot.
                yield np.zeros(config.FRAME_SAMPLES, dtype=np.int16)
                continue
            empties = 0
            frame = np.frombuffer(raw, dtype=np.int16)
            # Cas AVEC AEC : PipeWire continue d'envoyer du SILENCE PLAT (min==max,
            # que des zéros) quand l'USB est coupé. → micro mort, on passe en rouge.
            if frame.size and int(frame.min()) == int(frame.max()):
                flat += 1
                if flat >= flat_limit:
                    self._mark_lost("silence numérique — USB coupé ?")
            else:
                flat = 0
                self._mark_back()                            # audio réel → micro de retour
            if self._native_rate != config.SAMPLE_RATE:
                g = gcd(config.SAMPLE_RATE, self._native_rate)
                frame = resample_poly(
                    frame, config.SAMPLE_RATE // g, self._native_rate // g
                ).astype(np.int16)
            yield frame


# Cache des WAV de bips PAR séquence de tons : l'ancien cache unique rejouait
# le TOUT PREMIER bip généré quels que soient freq/dur → les bips différenciés
# (erreur 300 Hz, rejet, « rien entendu » 500→350) sonnaient tous pareil.
_beeps_ready: set = set()


def _beep_wav(seq, gain: float) -> str:
    """Synthétise (et met en cache) le WAV d'une séquence de (freq, dur)."""
    key = "_".join(f"{int(f)}-{int(d * 1000)}" for f, d in seq) + f"-{int(gain * 100)}"
    path = f"/tmp/aura_beep_{key}.wav"
    if path not in _beeps_ready:
        import wave as _wave
        sr = 16000
        parts = []
        for freq, dur in seq:
            n = int(sr * dur)
            t = np.linspace(0, dur, n, endpoint=False)
            env = np.minimum(1.0, np.minimum(t, dur - t) * 40)  # fade in/out
            parts.append((np.sin(2 * np.pi * freq * t) * env * gain * 32767).astype(np.int16))
        with _wave.open(path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(np.concatenate(parts).tobytes())
        _beeps_ready.add(path)
    return path


def play_beep(freq: float = 880.0, dur: float = 0.18, gain: float = 0.3):
    """Bip « je t'écoute » joué sur le HAUT-PARLEUR (aplay, comme le TTS).

    On évite sounddevice (souvent pas de sortie sur les micros USB) : on passe
    par aplay/ALSA, le même chemin que mpg123 pour le TTS. Désactivable AURA_BEEP=0.
    """
    play_beep_seq(((freq, dur),), gain=gain)


def play_beep_seq(seq, gain: float = 0.3):
    """Séquence de bips ((freq, dur), …) en UN SEUL WAV / UN SEUL aplay.

    Deux play_beep() successifs = deux aplay quasi simultanés : sur un device
    hardware sans dmix (plughw), le second se prend « device busy » et est avalé.
    Le composite garantit l'enchaînement (ex. double bip descendant 500→350)."""
    if os.getenv("AURA_BEEP", "1") != "1":
        return
    try:
        subprocess.Popen(_aplay_cmd(_beep_wav(tuple(seq), gain)),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


class Player:
    """Lecture d'un flux MP3 via mpg123, interruptible."""

    def __init__(self):
        self._proc: subprocess.Popen | None = None

    def play_mp3(self, mp3_bytes: bytes):
        """Joue le MP3 (bloquant). Coupé si stop() est appelé depuis un autre thread."""
        self.stop()
        try:
            self._proc = subprocess.Popen(
                _mpg123_cmd(),
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

    # ── Lecture en STREAMING (Aura parle dès le 1er chunk) ───────────
    def start_stream(self):
        self.stop()
        try:
            self._proc = subprocess.Popen(
                _mpg123_cmd(),
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            logger.error("[player] mpg123 introuvable — sudo apt install mpg123")
            self._proc = None

    def feed(self, chunk: bytes):
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.stdin.write(chunk)
            except Exception:
                pass

    def end_stream(self):
        """Ferme l'entrée et attend la fin de la lecture (bloquant)."""
        p = self._proc
        if p and p.poll() is None:
            try:
                p.stdin.close()
            except Exception:
                pass
            try:
                p.wait()
            except Exception:
                pass
        if self._proc is p:
            self._proc = None

    def stop(self):
        p = self._proc
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=1.0)          # reaper (évite les zombies defunct)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
        self._proc = None

    @property
    def is_playing(self) -> bool:
        return self._proc is not None and self._proc.poll() is None
