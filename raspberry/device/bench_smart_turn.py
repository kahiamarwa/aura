#!/usr/bin/env python3
"""Étape 0 (BLOQUANT) — Bench de faisabilité du turn detector Smart Turn v3 sur LE Pi.

But : mesurer la latence d'inférence de Pipecat Smart Turn v3 (ONNX, CPU) sur TA
cible (Pi 3/4/5) AVANT de bâtir le reste. Critère GO : p95 < 250 ms.
Si trop lent → plan B (timing via Deepgram Flux EndOfTurn côté serveur).

Prérequis :
    pip install onnxruntime huggingface_hub numpy
Récupérer le modèle (une fois) :
    huggingface-cli download pipecat-ai/smart-turn-v3   # ou laisse le script tenter
Lancer :
    python device/bench_smart_turn.py [chemin_vers_modele.onnx]
"""
import sys
import time

import numpy as np

try:
    import onnxruntime as ort
except ImportError:
    raise SystemExit("onnxruntime manquant → pip install onnxruntime")


def _find_model() -> str:
    if len(sys.argv) > 1:
        return sys.argv[1]
    # tente un download HuggingFace (pipecat-ai/smart-turn-v3)
    try:
        from huggingface_hub import hf_hub_download, list_repo_files
        repo = "pipecat-ai/smart-turn-v3"
        files = [f for f in list_repo_files(repo) if f.endswith(".onnx")]
        if not files:
            raise SystemExit(f"Aucun .onnx dans {repo} — passe le chemin en argument.")
        # privilégie une version int8/quantized si dispo
        pick = next((f for f in files if "int8" in f or "quant" in f), files[0])
        print(f"Téléchargement {repo}/{pick} …")
        return hf_hub_download(repo, pick)
    except ImportError:
        raise SystemExit("huggingface_hub manquant → pip install huggingface_hub, "
                         "ou passe le chemin du .onnx en argument.")


def main():
    path = _find_model()
    sess = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
    print(f"\nModèle : {path}")
    print("Entrées :")
    feed = {}
    for inp in sess.get_inputs():
        # remplace les dims dynamiques : batch=1, longueur audio = 8s @ 16k = 128000
        dims = []
        for j, d in enumerate(inp.shape):
            if isinstance(d, int) and d > 0:
                dims.append(d)
            else:
                dims.append(1 if j == 0 else 16000 * 8)
        dtype = np.float32 if "float" in inp.type else np.int64
        arr = (np.random.randn(*dims).astype(np.float32) if dtype == np.float32
               else np.zeros(dims, dtype=np.int64))
        feed[inp.name] = arr
        print(f"  {inp.name}  shape={inp.shape} → test {dims}  ({inp.type})")

    # warmup
    for _ in range(5):
        sess.run(None, feed)
    # mesure
    ts = []
    for _ in range(100):
        t = time.perf_counter()
        sess.run(None, feed)
        ts.append((time.perf_counter() - t) * 1000.0)
    ts.sort()
    p50, p95, p99 = ts[50], ts[95], ts[99]
    print(f"\nLatence inférence (n=100) : p50={p50:.0f}ms  p95={p95:.0f}ms  p99={p99:.0f}ms")
    print(f"Cœurs CPU : {ort.get_available_providers()}")
    if p95 < 250:
        print("\n✅ GO — Smart Turn local tient sur ce Pi (p95 < 250ms).")
    else:
        print("\n⚠️ TROP LENT (p95 ≥ 250ms) — plan B : timing via Deepgram Flux EndOfTurn.")


if __name__ == "__main__":
    main()
