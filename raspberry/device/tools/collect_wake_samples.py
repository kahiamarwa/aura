"""Collecte d'échantillons pour ré-entraîner le wake word sur le VRAI micro.

Enregistre à travers le pipeline de capture RÉEL d'Aura (MicStream : canal ASR
du XVF3800 + gain numérique + resample éventuel) — le modèle apprend donc
exactement ce qu'il verra à l'exécution. Trois modes :

  # 1. RECOMMANDÉ — déclenché par le wake word ACTUEL : laisse tourner, dis
  #    « Dis Aura » quand tu veux, où tu veux ; chaque détection (seuil abaissé
  #    à 0.20) sauvegarde automatiquement les ~3 s autour. Zéro bip, flexible.
  python -m device.tools.collect_wake_samples --wake-triggered 60

  # 2. Prises guidées par bips (ancienne méthode, rythme imposé)
  python -m device.tools.collect_wake_samples --positives 60

  # 3. Ambiant : 30 min de vie de bureau (fonds NÉGATIFS réalistes)
  python -m device.tools.collect_wake_samples --ambient-minutes 30

Sortie : ~/wake_samples/positive_real/*.wav et ~/wake_samples/background_real/*.wav
(16 kHz mono 16-bit — le format attendu par le notebook d'entraînement).

Mode wake-triggered : le score pic est dans le nom du fichier (wake_0007_s042.wav
= 0.42) → à la fin, réécouter/purger les captures suspectes (< 0.30 = possible
faux positif à vérifier). Varier distance (1/3/5 m), voix, locuteurs, orientation.
"""

import os
import sys
import time
import wave
import argparse

import numpy as np

from .. import config
from ..audio_io import MicStream, play_beep

SR = config.SAMPLE_RATE
OUT_DIR = os.path.expanduser(os.getenv("WAKE_SAMPLES_DIR", "~/wake_samples"))


def _write_wav(path: str, samples: np.ndarray):
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(samples.astype(np.int16).tobytes())


def _record_seconds(frames, seconds: float) -> np.ndarray:
    chunks, need = [], int(seconds * SR)
    got = 0
    for frame in frames:
        chunks.append(frame)
        got += len(frame)
        if got >= need:
            break
    return np.concatenate(chunks)[:need]


def collect_positives(n: int, seconds: float):
    out = os.path.join(OUT_DIR, "positive_real")
    os.makedirs(out, exist_ok=True)
    start_idx = len(os.listdir(out))
    print(f"── {n} prises de « Dis Aura » ({seconds:.1f} s chacune) → {out}")
    print("   Après CHAQUE bip : dis « Dis Aura » naturellement. Varie distance/voix.")
    print("   (Ctrl+C pour arrêter — les prises déjà faites sont conservées)\n")
    with MicStream() as mic:
        frames = mic.frames()
        _record_seconds(frames, 0.5)              # purge le démarrage
        for i in range(n):
            play_beep()                            # top départ
            time.sleep(0.15)                       # laisse le bip sortir des frames
            clip = _record_seconds(frames, seconds)
            rms = float(np.sqrt(np.mean((clip.astype(np.float32) / 32768.0) ** 2)))
            path = os.path.join(out, f"real_{start_idx + i:04d}.wav")
            _write_wav(path, clip)
            verdict = "ok" if rms > 0.005 else "⚠️ très faible — trop loin ?"
            print(f"  [{i + 1}/{n}] {os.path.basename(path)}  rms={rms:.4f}  {verdict}")
            time.sleep(0.6)                        # respiration entre les prises
    print(f"\n✓ Terminé — {n} prises dans {out}")


def collect_wake_triggered(n_target: int, min_score: float, window_s: float = 3.0,
                           tail_s: float = 0.8):
    """Mode flexible : le modèle wake ACTUEL déclenche la sauvegarde.

    Buffer circulaire de `window_s` ; dès que le score du modèle activate dépasse
    `min_score` (bien SOUS le seuil de prod : on veut AUSSI les essais faibles —
    ce sont eux qui manquent au modèle), on capture encore `tail_s` (fin de la
    phrase), puis on écrit la fenêtre. Cooldown 2 s + reset anti-doublon."""
    from ..wakeword import WakeWord
    out = os.path.join(OUT_DIR, "positive_real")
    os.makedirs(out, exist_ok=True)
    idx = len(os.listdir(out))
    wake = WakeWord()
    key = next((m for m in wake.names if "stop" not in m.lower()), wake.names[0])
    ring_max = int(window_s * SR)
    tail_frames = max(1, int(tail_s * SR / config.FRAME_SAMPLES))
    ring = np.zeros(0, dtype=np.int16)
    captured = 0
    cooldown_until = 0.0
    print(f"── Collecte déclenchée par « {key} » (score ≥ {min_score:.2f}) → {out}")
    print(f"   Dis « Dis Aura » librement (distances, voix, locuteurs variés).")
    print(f"   Objectif : {n_target} captures. Ctrl+C pour arrêter à tout moment.\n")
    try:
        with MicStream() as mic:
            frames = mic.frames()
            for frame in frames:
                ring = np.concatenate([ring, frame])[-ring_max:]
                score = float(wake.model.predict(frame).get(key, 0.0))
                if score >= min_score and time.monotonic() >= cooldown_until:
                    peak = score
                    for _ in range(tail_frames):     # fin de phrase
                        f2 = next(frames)
                        ring = np.concatenate([ring, f2])[-ring_max:]
                        peak = max(peak, float(wake.model.predict(f2).get(key, 0.0)))
                    fname = f"wake_{idx + captured:04d}_s{int(round(peak * 100)):03d}.wav"
                    _write_wav(os.path.join(out, fname), ring.copy())
                    captured += 1
                    print(f"  ✓ [{captured}/{n_target}] pic={peak:.2f} → {fname}")
                    try:
                        wake.model.reset()           # anti-doublon sur la même phrase
                    except Exception:
                        pass
                    cooldown_until = time.monotonic() + 2.0
                    if captured >= n_target:
                        break
    except KeyboardInterrupt:
        print("\n(arrêt demandé)")
    print(f"\n✓ {captured} captures dans {out}")
    print("  Relire les scores faibles (s0xx < 030) : purger les faux positifs.")


def collect_ambient(minutes: float, slice_s: float = 30.0):
    out = os.path.join(OUT_DIR, "background_real")
    os.makedirs(out, exist_ok=True)
    start_idx = len(os.listdir(out))
    n_slices = max(1, int(minutes * 60 / slice_s))
    print(f"── {minutes:.0f} min d'ambiance en tranches de {slice_s:.0f} s → {out}")
    print("   Vis normalement (conversations, bruits) — SANS dire « Dis Aura ».\n")
    with MicStream() as mic:
        frames = mic.frames()
        for i in range(n_slices):
            clip = _record_seconds(frames, slice_s)
            path = os.path.join(out, f"ambient_{start_idx + i:04d}.wav")
            _write_wav(path, clip)
            print(f"  [{i + 1}/{n_slices}] {os.path.basename(path)}")
    print(f"\n✓ Terminé — {n_slices} tranches dans {out}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--wake-triggered", type=int, default=0, metavar="N",
                    help="mode flexible : N captures déclenchées par le wake word actuel")
    ap.add_argument("--min-score", type=float, default=0.20,
                    help="seuil de capture du mode wake-triggered (défaut 0.20)")
    ap.add_argument("--positives", type=int, default=0, help="nombre de prises guidées par bips")
    ap.add_argument("--seconds", type=float, default=2.0, help="durée d'une prise positive")
    ap.add_argument("--ambient-minutes", type=float, default=0, help="minutes d'ambiance à capturer")
    args = ap.parse_args()
    if not args.wake_triggered and not args.positives and not args.ambient_minutes:
        ap.print_help()
        sys.exit(1)
    print(f"Pipeline de capture : device={config.INPUT_DEVICE!r} canaux={config.AUDIO_INPUT_CHANNELS} "
          f"canal={config.AUDIO_INPUT_CHANNEL} gain=×{config.AUDIO_INPUT_GAIN:g}\n")
    if args.wake_triggered:
        collect_wake_triggered(args.wake_triggered, args.min_score)
    if args.positives:
        collect_positives(args.positives, args.seconds)
    if args.ambient_minutes:
        collect_ambient(args.ambient_minutes)


if __name__ == "__main__":
    main()
