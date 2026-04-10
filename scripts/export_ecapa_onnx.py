"""Export SpeechBrain ECAPA-TDNN to ONNX format (single file, no external data).

Run once locally (requires torch + speechbrain):
    python3 scripts/export_ecapa_onnx.py

Produces: backend/app/services/ecapa_tdnn.onnx (~25MB)
"""

import os
import torch
import numpy as np
from speechbrain.inference import SpeakerRecognition

MODEL_SOURCE = "speechbrain/spkrec-ecapa-voxceleb"
CACHE_DIR = "backend/app/services/.speaker_model_cache"
OUTPUT_PATH = "backend/app/services/ecapa_tdnn.onnx"

print("[1/4] Loading SpeechBrain model...")
model = SpeakerRecognition.from_hparams(source=MODEL_SOURCE, savedir=CACHE_DIR)


class EcapaWrapper(torch.nn.Module):
    def __init__(self, sb_model):
        super().__init__()
        self.mods = sb_model.mods
        self.compute_features = self.mods.compute_features
        self.mean_var_norm = self.mods.mean_var_norm
        self.embedding_model = self.mods.embedding_model

    def forward(self, wavs):
        feats = self.compute_features(wavs)
        feats = self.mean_var_norm(feats, torch.tensor([1.0]))
        embeddings = self.embedding_model(feats)
        embeddings = torch.nn.functional.normalize(embeddings, p=2, dim=-1)
        return embeddings.squeeze(1)


print("[2/4] Exporting to ONNX...")
wrapper = EcapaWrapper(model)
wrapper.eval()

dummy_audio = torch.randn(1, 48000)

with torch.no_grad():
    test_out = wrapper(dummy_audio)
    print(f"   Test output shape: {test_out.shape}, norm: {torch.norm(test_out).item():.4f}")

# Export with weights embedded in the ONNX file
torch.onnx.export(
    wrapper,
    dummy_audio,
    OUTPUT_PATH,
    input_names=["audio"],
    output_names=["embedding"],
    dynamic_axes={
        "audio": {0: "batch", 1: "time"},
        "embedding": {0: "batch"},
    },
    opset_version=17,
)

# Convert external data to internal (single file)
print("[3/4] Converting to single-file ONNX...")
import onnx
onnx_model = onnx.load(OUTPUT_PATH, load_external_data=True)
onnx.save_model(onnx_model, OUTPUT_PATH, save_as_external_data=False)

# Clean up external data file
data_file = OUTPUT_PATH + ".data"
if os.path.exists(data_file):
    os.unlink(data_file)
    print(f"   Removed external data file: {data_file}")

# Verify
print("[4/4] Verifying ONNX model...")
import onnxruntime as ort

session = ort.InferenceSession(OUTPUT_PATH)
onnx_out = session.run(None, {"audio": dummy_audio.numpy()})[0]

cosine_sim = np.dot(test_out.numpy().flatten(), onnx_out.flatten())
print(f"   ONNX output shape: {onnx_out.shape}")
print(f"   PyTorch vs ONNX cosine similarity: {cosine_sim:.6f}")

size_mb = os.path.getsize(OUTPUT_PATH) / (1024 * 1024)
print(f"\n   Exported: {OUTPUT_PATH} ({size_mb:.1f} MB)")
print("   Done!")
