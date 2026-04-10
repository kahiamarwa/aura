FROM python:3.11-slim

WORKDIR /app

# System deps: ffmpeg (audio conversion), libgomp (ONNX Runtime)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Install tflite-runtime (openwakeword on Linux), then all deps
RUN pip install --no-cache-dir tflite-runtime && \
    pip install --no-cache-dir -r requirements.txt

# Download openwakeword prerequisite models
RUN python -c "import openwakeword; openwakeword.utils.download_models()"

# Pre-download SpeechBrain ECAPA-TDNN model (~300MB) to avoid cold start
RUN python -c "\
from speechbrain.inference import SpeakerRecognition; \
SpeakerRecognition.from_hparams(source='speechbrain/spkrec-ecapa-voxceleb', savedir='/app/app/services/.speaker_model_cache'); \
print('SpeechBrain model cached.')"

COPY . .

EXPOSE 8000

# Health check: restart container if unresponsive
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/stt-token')" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--timeout-keep-alive", "120"]
