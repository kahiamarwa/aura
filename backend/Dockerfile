FROM python:3.11-slim

WORKDIR /app

# System deps for onnxruntime
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Install tflite-runtime first (required by openwakeword on Linux), then the rest
RUN pip install --no-cache-dir tflite-runtime && \
    pip install --no-cache-dir -r requirements.txt

# Download openwakeword prerequisite models (melspectrogram, embedding, etc.)
RUN python -c "import openwakeword; openwakeword.utils.download_models()"

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
