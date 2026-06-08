# PLAN : Remplacer Porcupine par openWakeWord dans Aura

## Contexte

L'assistant vocal Aura utilise actuellement Picovoice Porcupine (WASM, browser-side) pour la detection du wake word "Aura test". On veut remplacer Porcupine par openWakeWord (Python, server-side) car on a un modele custom entraine `aura_best.onnx`.

**Changement d'architecture** : Porcupine tourne dans le browser (WASM). openWakeWord tourne en Python. Il faut donc streamer l'audio du browser vers le backend FastAPI via WebSocket, faire l'inference cote serveur, et renvoyer un evenement "wake_word_detected" au frontend.

Le projet se trouve dans `/Users/badreddine/Desktop/Aura/aura-etoile/`.

---

## Etape 1 : Backend - Service openWakeWord (singleton)

**Creer** `backend/app/services/wakeword_service.py`

```python
import numpy as np
from openwakeword.model import Model

class WakeWordService:
    """Singleton qui charge le modele openWakeWord une seule fois."""

    _instance = None

    def __init__(self):
        self.model = Model(
            wakeword_models=["openwake/aura_best.onnx"],
            inference_framework="onnx",
        )
        self.model_names = list(self.model.models.keys())
        self.threshold = 0.3

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def process_audio(self, audio_int16: np.ndarray) -> dict | None:
        """Retourne {"model": name, "score": float} si detection, sinon None."""
        predictions = self.model.predict(audio_int16)
        for name, score in predictions.items():
            if score > self.threshold:
                return {"model": name, "score": float(score)}
        return None
```

Note : le chemin du modele `openwake/aura_best.onnx` est relatif au repertoire de lancement du backend (la racine du projet). Adapter si necessaire.

---

## Etape 2 : Backend - Endpoint WebSocket

**Creer** `backend/app/routes/wakeword.py`

```python
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
import numpy as np
from app.services.wakeword_service import WakeWordService

router = APIRouter()

@router.websocket("/api/wakeword")
async def wakeword_ws(websocket: WebSocket):
    await websocket.accept()
    service = WakeWordService.get_instance()

    try:
        while True:
            # Le frontend envoie des bytes bruts (Int16 PCM, 16kHz)
            data = await websocket.receive_bytes()
            audio_array = np.frombuffer(data, dtype=np.int16)

            result = service.process_audio(audio_array)
            if result is not None:
                await websocket.send_json({
                    "event": "wake_word_detected",
                    "model": result["model"],
                    "score": result["score"],
                })
    except WebSocketDisconnect:
        pass
```

**Protocole WebSocket** :
- Client envoie : bytes bruts (Int16 PCM, 16kHz mono) tous les ~80ms (1280 samples = 2560 bytes)
- Serveur envoie : `{"event": "wake_word_detected", "model": "...", "score": 0.xx}` quand detecte

---

## Etape 3 : Backend - Brancher le router + deps

**Modifier** `backend/app/main.py` - ajouter le router wakeword :

```python
from app.routes import chat, health, stt_token, wakeword

# ... apres les autres include_router :
app.include_router(wakeword.router)
```

**Modifier** `backend/requirements.txt` - ajouter :

```
openwakeword==0.6.0
numpy>=1.21.6
```

Puis `pip install -r requirements.txt` dans le venv du backend.

Aussi, s'assurer que les modeles de preprocessing openWakeWord sont telecharges :
```python
python -c "import openwakeword; openwakeword.utils.download_models()"
```

---

## Etape 4 : Frontend - Resampling 48kHz vers 16kHz

L'AudioWorklet capture a 48kHz (sample rate natif du navigateur). openWakeWord attend du 16kHz. Il faut resampler AVANT d'envoyer au WebSocket.

**Modifier** `frontend/public/pcmProcessor.worklet.js` - ajouter le downsampling :

Remplacer le contenu par :

```javascript
class PCMProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buffer = [];
    this.bufferSize = 0;
    this.chunkThreshold = Math.floor(sampleRate * 0.1); // ~100ms

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

      // Envoyer le chunk original (48kHz) pour le STT
      this.port.postMessage(
        { type: "pcm", samples: merged, sampleRate: sampleRate },
        [merged.buffer]
      );

      // Envoyer aussi une version 16kHz pour le wake word
      const downsampled = this.downsample(merged, sampleRate, this.targetRate);
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
```

Note : on envoie maintenant 2 types de messages :
- `type: "pcm"` (48kHz, pour le STT ElevenLabs - existant)
- `type: "pcm16k"` (16kHz, pour le wake word WebSocket - nouveau)

---

## Etape 5 : Frontend - Modifier useAudioCapture pour exposer les chunks 16kHz

**Modifier** `frontend/src/hooks/useAudioCapture.ts`

Ajouter un second callback ref pour les chunks 16kHz :

```typescript
// Ajouter ce type et ce callback :
type PCM16kCallback = (samples: Int16Array) => void;

// Dans le hook, ajouter :
const pcm16kCallbackRef = useRef<PCM16kCallback | null>(null);

const onPCM16kChunk = useCallback((callback: PCM16kCallback) => {
  pcm16kCallbackRef.current = callback;
}, []);

// Dans le workletNode.port.onmessage, ajouter le cas "pcm16k" :
workletNode.port.onmessage = (event) => {
  if (event.data.type === "pcm" && pcmCallbackRef.current) {
    pcmCallbackRef.current(event.data.samples, event.data.sampleRate);
  }
  if (event.data.type === "pcm16k" && pcm16kCallbackRef.current) {
    pcm16kCallbackRef.current(event.data.samples);
  }
};

// Exposer dans le return :
return { ..., onPCM16kChunk };
```

---

## Etape 6 : Frontend - Creer useOpenWakeWord.ts

**Creer** `frontend/src/hooks/useOpenWakeWord.ts`

Ce hook remplace `usePorcupine.ts` avec la meme interface :

```typescript
"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { BACKEND_URL } from "@/lib/constants";

type FallbackMode = "custom" | "push-to-talk";

interface UseOpenWakeWordReturn {
  isLoaded: boolean;
  fallbackMode: FallbackMode;
  error: string | null;
  startListening: (stream: MediaStream) => Promise<void>;
  stopListening: () => void;
  onKeywordDetected: (callback: () => void) => void;
  triggerManual: () => void;
  sendAudio: (samples: Int16Array) => void;
}

export function useOpenWakeWord(): UseOpenWakeWordReturn {
  const [isLoaded, setIsLoaded] = useState(false);
  const [fallbackMode, setFallbackMode] = useState<FallbackMode>("push-to-talk");
  const [error, setError] = useState<string | null>(null);

  const callbackRef = useRef<(() => void) | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  const onKeywordDetected = useCallback((callback: () => void) => {
    callbackRef.current = callback;
  }, []);

  const triggerManual = useCallback(() => {
    callbackRef.current?.();
  }, []);

  const sendAudio = useCallback((samples: Int16Array) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(samples.buffer);
    }
  }, []);

  const startListening = useCallback(async (_stream: MediaStream) => {
    try {
      const wsUrl = BACKEND_URL.replace(/^http/, "ws") + "/api/wakeword";
      const ws = new WebSocket(wsUrl);

      ws.binaryType = "arraybuffer";

      ws.onopen = () => {
        console.log("[OpenWakeWord] WebSocket connected");
        wsRef.current = ws;
        setFallbackMode("custom");
        setIsLoaded(true);
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.event === "wake_word_detected") {
            console.log("[OpenWakeWord] Detected!", data.score);
            callbackRef.current?.();
          }
        } catch {
          // ignore parse errors
        }
      };

      ws.onerror = (err) => {
        console.error("[OpenWakeWord] WebSocket error:", err);
        setError("Wake word WebSocket error");
        setFallbackMode("push-to-talk");
        setIsLoaded(true);
      };

      ws.onclose = () => {
        console.log("[OpenWakeWord] WebSocket closed");
        wsRef.current = null;
      };
    } catch (err) {
      console.warn("OpenWakeWord init failed, using push-to-talk:", err);
      setFallbackMode("push-to-talk");
      setIsLoaded(true);
      setError("Wake word indisponible - mode push-to-talk");
    }
  }, []);

  const stopListening = useCallback(() => {
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    setIsLoaded(false);
  }, []);

  useEffect(() => {
    return () => { stopListening(); };
  }, [stopListening]);

  return {
    isLoaded,
    fallbackMode,
    error,
    startListening,
    stopListening,
    onKeywordDetected,
    triggerManual,
    sendAudio,
  };
}
```

---

## Etape 7 : Frontend - Modifier useAuraSession.ts

**Modifier** `frontend/src/hooks/useAuraSession.ts`

Changements a faire :

### 7.1 - Remplacer l'import :
```typescript
// AVANT:
import { usePorcupine } from "./usePorcupine";
// APRES:
import { useOpenWakeWord } from "./useOpenWakeWord";
```

### 7.2 - Remplacer l'instanciation du hook :
```typescript
// AVANT (ligne 61):
const porcupine = usePorcupine();
// APRES:
const wakeword = useOpenWakeWord();
```

### 7.3 - Brancher les chunks 16kHz vers le WebSocket :
Ajouter un useEffect pour envoyer les chunks audio 16kHz au wake word service :

```typescript
// Apres le useEffect qui enregistre handlePCMChunk (ligne 153-155) :
useEffect(() => {
  audio.onPCM16kChunk((samples: Int16Array) => {
    wakeword.sendAudio(samples);
  });
}, [audio, wakeword]);
```

### 7.4 - Mettre a jour les references de porcupine vers wakeword :
- Ligne 158-160 : `porcupine.onKeywordDetected(...)` -> `wakeword.onKeywordDetected(...)`
- Ligne 176 : `porcupine.error` -> `wakeword.error`
- Ligne 192 : `porcupine.startListening(mic.stream)` -> `wakeword.startListening(mic.stream)`
- Ligne 206 : `[audio, passiveSTT, porcupine]` -> `[audio, passiveSTT, wakeword]`
- Ligne 218 : `porcupine.fallbackMode` -> `wakeword.fallbackMode`

---

## Etape 8 : Frontend - Modifier constants.ts

**Modifier** `frontend/src/lib/constants.ts`

Supprimer :
```typescript
export const PORCUPINE_ACCESS_KEY =
  process.env.NEXT_PUBLIC_PORCUPINE_ACCESS_KEY || "";
```

La constante BACKEND_URL existante est suffisante pour construire l'URL WebSocket.

---

## Etape 9 : Nettoyage

### Supprimer ces fichiers :
- `frontend/src/hooks/usePorcupine.ts`
- `frontend/public/porcupine/Aura-test_fr_wasm_v4_0_0.ppn`
- `frontend/public/porcupine/porcupine_params_fr.pv`

### Modifier `frontend/package.json` - retirer les deps Picovoice :
Supprimer ces 2 lignes des dependencies :
```json
"@picovoice/porcupine-web": "^4.0.0",
"@picovoice/web-voice-processor": "^4.0.9",
```

Puis `npm install` pour mettre a jour le lockfile.

### Modifier `frontend/.env` :
Supprimer la ligne `NEXT_PUBLIC_PORCUPINE_ACCESS_KEY=...`

---

## Etape 10 : Copier le modele

S'assurer que le modele `aura_best.onnx` est accessible depuis la racine du projet :
```
aura-etoile/
  openwake/
    aura_best.onnx    <-- deja present
```

Le backend sera lance depuis la racine `aura-etoile/`, donc le chemin `openwake/aura_best.onnx` dans le service sera correct.

---

## Verification / Test

### 1. Backend
```bash
cd /Users/badreddine/Desktop/Aura/aura-etoile/backend
pip install -r requirements.txt
python -c "import openwakeword; openwakeword.utils.download_models()"
cd /Users/badreddine/Desktop/Aura/aura-etoile
uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000
```

Verifier dans les logs que le modele openWakeWord se charge sans erreur.

### 2. Frontend
```bash
cd /Users/badreddine/Desktop/Aura/aura-etoile/frontend
npm install
npm run dev
```

### 3. Test fonctionnel
1. Ouvrir http://localhost:3000
2. Autoriser le microphone
3. Verifier que le WebSocket se connecte (console: "[OpenWakeWord] WebSocket connected")
4. Dire "Aura test"
5. Verifier que le beep se joue et que l'etat passe a "listening"
6. Si la detection ne fonctionne pas, baisser le threshold dans `wakeword_service.py` (ex: 0.2)

### 4. Test WebSocket isole
On peut tester le WebSocket seul avec un script Python :
```python
import asyncio, websockets, numpy as np
async def test():
    async with websockets.connect("ws://localhost:8000/api/wakeword") as ws:
        # Envoyer du silence (pour verifier que ca ne detecte pas en faux positif)
        silence = np.zeros(1280, dtype=np.int16)
        for _ in range(100):
            await ws.send(silence.tobytes())
            await asyncio.sleep(0.08)
asyncio.run(test())
```

---

## Resume des fichiers

| Fichier | Action |
|---------|--------|
| `backend/app/services/wakeword_service.py` | CREER |
| `backend/app/routes/wakeword.py` | CREER |
| `backend/app/main.py` | MODIFIER (ajouter router) |
| `backend/requirements.txt` | MODIFIER (ajouter openwakeword, numpy) |
| `frontend/src/hooks/useOpenWakeWord.ts` | CREER |
| `frontend/src/hooks/useAuraSession.ts` | MODIFIER (swap porcupine -> wakeword) |
| `frontend/src/hooks/useAudioCapture.ts` | MODIFIER (ajouter onPCM16kChunk) |
| `frontend/public/pcmProcessor.worklet.js` | MODIFIER (ajouter downsample + pcm16k) |
| `frontend/src/lib/constants.ts` | MODIFIER (retirer PORCUPINE_ACCESS_KEY) |
| `frontend/package.json` | MODIFIER (retirer deps picovoice) |
| `frontend/.env` | MODIFIER (retirer cle porcupine) |
| `frontend/src/hooks/usePorcupine.ts` | SUPPRIMER |
| `frontend/public/porcupine/` | SUPPRIMER (dossier entier) |
