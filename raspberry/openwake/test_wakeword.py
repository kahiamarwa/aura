"""
Test standalone du wake word "Aura" avec openWakeWord.

Usage:
  cd openwake
  ./venv/Scripts/python test_wakeword.py
"""

import os
import sys
import pyaudio
import numpy as np
from openwakeword.model import Model

# --- Config ---
MODEL_PATH = os.path.join(os.path.dirname(__file__), "aura_best.onnx")
CHUNK_SIZE = 1280        # 80ms à 16kHz
SAMPLE_RATE = 16000
THRESHOLD = 0.3

# --- Load model ---
print(f"Modele: {MODEL_PATH}")
print(f"Taille: {os.path.getsize(MODEL_PATH)} bytes")

model = Model(
    wakeword_models=[MODEL_PATH],
    inference_framework="onnx",
)

model_names = list(model.models.keys())
print(f"Modeles charges: {model_names}")

# --- Setup microphone ---
audio = pyaudio.PyAudio()

print("\n--- Peripheriques d'entree ---")
default_idx = audio.get_default_input_device_info()["index"]
for i in range(audio.get_device_count()):
    info = audio.get_device_info_by_index(i)
    if info["maxInputChannels"] > 0:
        marker = " <-- DEFAULT" if i == default_idx else ""
        print(f"  [{i}] {info['name']} ({int(info['defaultSampleRate'])}Hz){marker}")

stream = audio.open(
    format=pyaudio.paInt16,
    channels=1,
    rate=SAMPLE_RATE,
    input=True,
    frames_per_buffer=CHUNK_SIZE,
)

print(f"\n{'='*60}")
print(f"Ecoute active - dis 'Aura' (seuil: {THRESHOLD})")
print(f"{'='*60}\n")

# --- Step 1: Test micro 3 seconds ---
print("--- Test micro (parle pendant 3 secondes) ---")
max_vol = 0
for i in range(int(3 * SAMPLE_RATE / CHUNK_SIZE)):
    data = stream.read(CHUNK_SIZE, exception_on_overflow=False)
    arr = np.frombuffer(data, dtype=np.int16)
    rms = np.sqrt(np.mean(arr.astype(np.float32) ** 2))
    peak = np.max(np.abs(arr))
    if rms > max_vol:
        max_vol = rms
    bar_len = min(int(rms / 500 * 40), 40)
    bar = "|" * bar_len + " " * (40 - bar_len)
    sys.stdout.write(f"\r  Volume: RMS={rms:6.0f}  Peak={peak:5d}  [{bar}]")
    sys.stdout.flush()

print(f"\n  Volume max: {max_vol:.0f}")
if max_vol < 100:
    print("  *** ATTENTION: Volume tres bas! Le micro ne capte peut-etre rien. ***")
    print("  Verifie que le bon micro est selectionne dans Windows.")
elif max_vol < 500:
    print("  Volume faible mais present.")
else:
    print("  Volume OK!")

# --- Step 2: Detection loop ---
print(f"\n--- Detection wake word (Ctrl+C pour arreter) ---\n")
detection_count = 0
frame_count = 0
try:
    while True:
        audio_data = stream.read(CHUNK_SIZE, exception_on_overflow=False)
        audio_array = np.frombuffer(audio_data, dtype=np.int16)
        frame_count += 1

        rms = np.sqrt(np.mean(audio_array.astype(np.float32) ** 2))
        predictions = model.predict(audio_array)

        for model_name, score in predictions.items():
            vol_bar = min(int(rms / 500 * 10), 10)
            score_bar = int(score * 40)
            sys.stdout.write(
                f"\r  vol:{'|'*vol_bar}{' '*(10-vol_bar)} | "
                f"{model_name}: {score:.4f} [{'#'*score_bar}{'.'*(40-score_bar)}]"
                f"{'  <<< DETECT!' if score > THRESHOLD else '              '}"
            )
            sys.stdout.flush()

            if score > THRESHOLD:
                detection_count += 1
                print(f"\n>>> AURA DETECTE #{detection_count} (score: {score:.4f}) <<<\n")

except KeyboardInterrupt:
    print(f"\n\nArret. Detections: {detection_count}")
finally:
    stream.stop_stream()
    stream.close()
    audio.terminate()
