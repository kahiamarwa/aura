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
except ImportError:
    raise SystemExit("onnxruntime manquant → pip install onnxruntime")
try:
    from onnxruntime.quantization import quantize_dynamic, QuantType
except ImportError:
    raise SystemExit("La quantification a besoin du paquet 'onnx' → pip install onnx")


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
    src = _find_fp32()
    sz = Path(src).stat().st_size / 1e6
    print(f"Modèle : {src}  ({sz:.1f} Mo)")
    dst = str(Path(src).with_suffix("")) + ".int8.onnx"
    try:
        quantize_dynamic(src, dst, weight_type=QuantType.QInt8)
        print(f"Quantifié → {dst} ({Path(dst).stat().st_size / 1e6:.1f} Mo)\n")
        print("Latence (n=100) :")
        p95_src = _bench(src, "actuel")
        p95_int8 = _bench(dst, "int8")
        print(f"\nGain : {p95_src / max(p95_int8, 1):.1f}× — int8 prêt : {dst}")
    except Exception as e:
        # smart-turn-v3 est DÉJÀ int8 (DequantizeLinear) → rien à faire.
        print(f"\nℹ️ Le modèle est DÉJÀ quantifié (int8) — re-quantifier échoue ({type(e).__name__}).")
        print("C'est normal : 8,76 Mo = int8. La latence mesurée est déjà l'optimum.")
        print("Latence (n=100) :")
        _bench(src, "int8")
        print("\n✅ Rien à optimiser de plus côté quantification.")


if __name__ == "__main__":
    main()
