#!/usr/bin/env python3
"""Quantifie Smart Turn v3 en int8 (~2-3× plus rapide sur le Pi) + re-bench.

Lance après bench_smart_turn.py (qui a déjà téléchargé le modèle fp32) :
    pip install onnxruntime huggingface_hub
    python device/quantize_smart_turn.py
→ crée smart-turn-v3.int8.onnx à côté du fp32, mesure sa latence, et compare.
On garde l'int8 si la latence baisse nettement (et la précision reste bonne sur tes tests).
"""
import sys
import time
from pathlib import Path

import numpy as np

try:
    import onnxruntime as ort
    from onnxruntime.quantization import quantize_dynamic, QuantType
except ImportError:
    raise SystemExit("onnxruntime manquant → pip install onnxruntime")


def _find_fp32() -> str:
    if len(sys.argv) > 1:
        return sys.argv[1]
    try:
        from huggingface_hub import hf_hub_download, list_repo_files
        repo = "pipecat-ai/smart-turn-v3"
        files = [f for f in list_repo_files(repo) if f.endswith(".onnx") and "int8" not in f]
        return hf_hub_download(repo, files[0])
    except Exception as e:
        raise SystemExit(f"Modèle fp32 introuvable ({e}) — passe le chemin en argument.")


def _bench(path: str, label: str) -> float:
    sess = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
    feed = {}
    for inp in sess.get_inputs():
        dims = [d if isinstance(d, int) and d > 0 else (1 if j == 0 else 800)
                for j, d in enumerate(inp.shape)]
        feed[inp.name] = np.random.randn(*dims).astype(np.float32)
    for _ in range(5):
        sess.run(None, feed)
    ts = []
    for _ in range(100):
        t = time.perf_counter()
        sess.run(None, feed)
        ts.append((time.perf_counter() - t) * 1000.0)
    ts.sort()
    print(f"  {label:6} : p50={ts[50]:.0f}ms  p95={ts[95]:.0f}ms")
    return ts[95]


def main():
    fp32 = _find_fp32()
    int8 = str(Path(fp32).with_suffix("")) + ".int8.onnx"
    print(f"Quantification int8 → {int8}")
    quantize_dynamic(fp32, int8, weight_type=QuantType.QInt8)
    sz = Path(int8).stat().st_size / 1e6
    print(f"Taille int8 : {sz:.1f} Mo\n")

    print("Latence (n=100) :")
    p95_fp32 = _bench(fp32, "fp32")
    p95_int8 = _bench(int8, "int8")
    print(f"\nGain : {p95_fp32 / max(p95_int8, 1):.1f}× plus rapide")
    print(f"Modèle int8 prêt : {int8}")
    if p95_int8 < 100:
        print("✅ Excellent — int8 sous 100ms, parfait temps réel.")
    elif p95_int8 < p95_fp32 * 0.8:
        print("✅ int8 nettement plus rapide — on le garde.")
    else:
        print("⚠️ Peu de gain int8 sur ce Pi — garde le fp32 (déjà < 250ms).")


if __name__ == "__main__":
    main()
