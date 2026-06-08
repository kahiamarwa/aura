class PCMProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buffer = [];
    this.bufferSize = 0;
    // Send chunks every ~100ms worth of samples at native sample rate
    this.chunkThreshold = Math.floor(sampleRate * 0.1);

    // Resampling config pour le wake word
    this.targetRate = 16000;
    this.resampleRatio = this.targetRate / sampleRate;
  }

  downsample(samples, fromRate, toRate) {
    if (fromRate === toRate) return samples;
    const ratio = fromRate / toRate;
    const newLength = Math.floor(samples.length / ratio);
    const result = new Int16Array(newLength);
    for (let i = 0; i < newLength; i++) {
      result[i] = samples[Math.floor(i * ratio)];
    }
    return result;
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || !input[0]) return true;

    const channelData = input[0];

    const int16 = new Int16Array(channelData.length);
    for (let i = 0; i < channelData.length; i++) {
      const s = Math.max(-1, Math.min(1, channelData[i]));
      int16[i] = s < 0 ? s * 32768 : s * 32767;
    }

    this.buffer.push(int16);
    this.bufferSize += int16.length;

    if (this.bufferSize >= this.chunkThreshold) {
      const merged = new Int16Array(this.bufferSize);
      let offset = 0;
      for (const chunk of this.buffer) {
        merged.set(chunk, offset);
        offset += chunk.length;
      }

      // Downsample AVANT de transférer le buffer (sinon merged est détaché)
      const downsampled = this.downsample(merged, sampleRate, this.targetRate);

      // Envoyer le chunk original (48kHz) pour le STT
      this.port.postMessage(
        { type: "pcm", samples: merged, sampleRate: sampleRate },
        [merged.buffer]
      );

      // Envoyer la version 16kHz pour le wake word
      this.port.postMessage(
        { type: "pcm16k", samples: downsampled, sampleRate: this.targetRate },
        [downsampled.buffer]
      );

      this.buffer = [];
      this.bufferSize = 0;
    }

    return true;
  }
}

registerProcessor("pcm-processor", PCMProcessor);
