# Chemin B — Turn-taking « façon Alexa » (branche `feat/streaming-turn-detection`)

Décision d'architecture issue de la recherche multi-agents. Objectif : remplacer
l'endpointing « silence fixe 2s » par une vraie **détection de tour de parole**
temps réel (tolère les pauses, sait quand l'utilisateur a fini), sans latence LLM.

## ⚠️ Correctif de contexte (vs la recherche)
La recherche a pointé `front/src/hooks/useCommandSTT.ts` (React) — c'est le **front
web** (dashboard navigateur), PAS le device. Le **vrai device** = le **Python**
`device/orchestrator.py` (openWakeWord + Silero VAD + sounddevice). Donc le Smart
Turn s'intègre **en Python sur le Pi**, pas en React.

## 🏆 Décision : turn detector AUDIO local + STT ElevenLabs (on garde notre vendeur)

Insight clé : un turn detector **AUDIO** décide « fini/pas fini » sur l'**audio** →
il ne dépend PAS du transcript → **on découple la détection de tour du STT**. Donc
on garde ElevenLabs (déjà utilisé) et on ajoute juste le turn detector local.

**A. Détection de fin de tour = LOCALE sur le Pi (audio-only)** — **Pipecat Smart
Turn v3** (ONNX 8 Mo, audio 16k/fenêtre 8s, **BSD-2**, **français inclus** parmi 23
langues, ~8M params encodeur Whisper-tiny). Empilé sur le Silero VAD. Standalone
avec **juste onnxruntime + numpy** (script `predict.py`/`inference.py` du repo).
→ **LiveKit v1-mini ÉCARTÉ** : sa licence (LiveKit Model License) **interdit
explicitement** l'usage standalone hors framework LiveKit Agents → hors-licence pour
notre boucle Python custom + produit vendu. Vérifié verbatim dans le LICENSE.
Smart Turn (BSD-2) n'a aucune de ces restrictions.

**B. STT streaming = via le BACKEND (proxy)** — **ElevenLabs Scribe v2 realtime**
(qu'on utilise DÉJÀ côté web). Le device ouvre **UN** WebSocket vers **notre backend**
(auth `DEVICE_TOKEN`) ; le backend relaie vers ElevenLabs (clé **côté cloud**).
→ respecte « aucune clé sur l'appareil » + **zéro nouveau vendeur**.
→ Le STT ne décide PAS le tour (c'est le turn detector A qui décide) ; Scribe fournit
juste le transcript au fil de l'eau.

## Flux
```
Pi (aucune clé) :
  micro → openWakeWord (local) → Silero VAD (local)
        → [commande] Turn detector AUDIO local (LiveKit v1-mini) → décide EOT
        → pousse le PCM 16k en continu sur 1 WS → backend
Backend (toutes les clés) :
  /api/device-stream (WS) → relaie PCM → ElevenLabs Scribe v2 realtime → transcript
  EOT (Pi, autorité) → fige transcript → intent → verif → LLM → TTS (ElevenLabs)
```
Autorité du timing = le **Pi** (turn detector audio). Garde anti-boucle : timeout 8s.

## Composants
| Rôle | Composant | Où |
|---|---|---|
| Wake word | openWakeWord (déjà) | Pi |
| VAD | Silero (déjà) | Pi |
| **Turn detector** | **Pipecat Smart Turn v3** (audio, ONNX, BSD-2) | **Pi (nouveau, Python)** |
| STT streaming | **ElevenLabs Scribe v2 realtime** (`scribe_v2_realtime`, PCM 16k) | Cloud, via proxy backend |
| Proxy WS | `backend/app/routes/device_stream.py` (nouveau) | Backend |
| TTS | ElevenLabs (déjà) | Backend |
| Intent/verif/LLM | inchangés | Backend |

## Pourquoi pas les autres
- **Deepgram (Flux)** : nouveau vendeur inutile — on a déjà ElevenLabs (STT Scribe + TTS).
  Et son turn-taking est côté cloud (dépend du réseau) ; nous, on décide en LOCAL.
- **Tout-local STT (Vosk FR)** : WER 19-27 % → trop dégradé pour un produit vendu.
- **Turn detector TEXTE** (LiveKit text / approche transcript) : déprécié + impose un STT
  streaming synchronisé. L'**audio** est plus précis et indépendant du STT.
- **Smart Turn v3** : excellent fallback (BSD-2, standalone), mais AUC 0.83 < LiveKit 0.96.
  On le garde comme repli si la licence/intégration LiveKit pose souci.
- **Voxtral batch + Haiku `_is_complete`** : c'est ce qu'on remplace (LLM réseau par pause = lent + boucle).

## Plan d'implémentation
- **Étape 0 (BLOQUANT)** : bencher Smart Turn v3 sur le Pi cible. Critère go : p95 < 250 ms.
  → `device/bench_smart_turn.py`. Si Pi 3 trop lent → plan B (timing via Flux `EndOfTurn`).
- **Étape 1** : proxy WS backend → Deepgram (`device_stream.py`). Indépendant ; corrige aussi la sécu.
- **Étape 2** : module Python `device/smart_turn.py` + intégration dans `_record_command`.
- **Étape 3** : remplacer l'endpointing silence par l'EOT Smart Turn ; retirer `_is_complete` Haiku.
- **Étape 4** : fallback (Deepgram KO → batch Voxtral existant ; Smart Turn KO → `EndOfTurn` Flux).

## Coût / latence
- Turn detector LiveKit v1-mini : **0 €** (local CPU, gratuit). STT : ElevenLabs Scribe
  realtime (déjà au budget). **Zéro nouveau vendeur, zéro nouveau coût** vs aujourd'hui.
- Gain : supprime l'aller-retour réseau ~1,5-2 s par pause (vs `_is_complete` actuel).

## Dépendances à obtenir
- Modèle turn detector : **smart-turn-v3** (HF `pipecat-ai/smart-turn-v3`, BSD-2).
  Entrée : audio **mono 16 kHz, ≤8 s, zero-padé au début** → onnxruntime.
- ElevenLabs : déjà en place (clé backend) — ajouter le proxy WS vers
  `wss://api.elevenlabs.io/v1/speech-to-text/realtime` (header `xi-api-key` serveur,
  `model_id=scribe_v2_realtime`, chunks PCM16k base64 ; messages `partial_transcript`
  / `committed_transcript`). Région EU dispo (`api.eu.residency.elevenlabs.io`) pour RGPD.
- Cible Pi exacte pour le bench (Pi 3 / 4 / 5).
