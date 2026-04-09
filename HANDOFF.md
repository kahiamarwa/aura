# AURA ETOILE - Document de Handoff Complet

## Vue d'ensemble

**Projet** : AURA Etoile — Assistant vocal IA professionnel
**Stack** : Next.js 16 (Frontend) + FastAPI Python (Backend) + Supabase Edge Functions + PostgreSQL
**Branche active** : `frontend`
**Langue** : Interface en français, transcription multilingue

---

## 1. Architecture globale

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────────────┐
│   Frontend       │    │   Backend        │    │   Supabase              │
│   Next.js 16     │◄──►│   FastAPI        │◄──►│   Edge Functions        │
│   localhost:3000  │    │   localhost:8000  │    │   PostgreSQL            │
└─────────────────┘    └──────────────────┘    └─────────────────────────┘
        │                       │
        │ WebSocket             │ WebSocket
        ├── /api/gemini-stt     ├── Mistral Voxtral Mini (passive STT)
        ├── /api/wakeword       ├── OpenWakeWord (wake word)
        └── ElevenLabs WS       └── ElevenLabs Scribe (command STT)
```

### Flux audio

```
Microphone (48kHz PCM)
  ↓
useAudioCapture → Int16 PCM chunks
  ↓
useAuraSession (routeur audio)
  ├── Mode passif → usePassiveSTT → Mistral Voxtral Mini (via backend WS proxy)
  │                                  → contextBuffer (mémoire locale)
  ├── Mode commande → useCommandSTT → ElevenLabs Scribe v2 (direct WS)
  │                                    → onCommandComplete() → LLM
  └── Wake word → useOpenWakeWord → OpenWakeWord ONNX (via backend WS)
                                     → déclenche mode commande
```

### Flux chat (après commande vocale)

```
Commande utilisateur (texte)
  ↓
POST /api/chat (SSE streaming)
  ├── context-enrichment (Supabase Edge Function)
  │    → enrichit avec résumés récents, contacts, calendrier
  ├── aura-agent (Supabase Edge Function)
  │    → Claude Anthropic + ~30 outils (email, SMS, HubSpot, Slack, etc.)
  │    → SSE stream: text_delta, tool_start, tool_result, done
  └── TTS via ElevenLabs (audio)
```

---

## 2. Structure des fichiers

### Frontend (`frontend/src/`)

```
app/
├── page.tsx              # Page principale (orbe, chat, contexte)
├── login/page.tsx        # Authentification Supabase
├── contacts/page.tsx     # Gestion contacts
├── discussions/page.tsx  # Historique discussions
├── summaries/page.tsx    # Résumés automatiques
├── activity/page.tsx     # Journal d'activité
├── settings/page.tsx     # Paramètres + connecteurs OAuth
├── settings/callback/    # Callback OAuth
└── layout.tsx            # Layout racine (AuthProvider + AuraSessionProvider)

hooks/
├── useAuraSession.ts     # ⭐ FICHIER PRINCIPAL (~570 lignes) - Orchestration complète
├── usePassiveSTT.ts      # STT passif via Mistral (WebSocket backend proxy)
├── useCommandSTT.ts      # STT commande via ElevenLabs Scribe v2 (WebSocket direct)
├── useOpenWakeWord.ts    # Détection mot de réveil (WebSocket backend)
├── useAudioCapture.ts    # Capture micro + conversion PCM
├── useAudioPlayer.ts     # Lecture audio TTS
└── useAuth.ts            # État d'authentification

context/
├── AuraSessionContext.tsx  # Context React pour session persistante entre pages
└── AuthContext.tsx         # Context Supabase Auth

components/
├── AuraOrb.tsx           # Orbe animée avec état visuel
├── StatusBar.tsx         # Indicateur d'état
├── VolumeIndicator.tsx   # Volume micro
├── LiveTranscript.tsx    # Transcription commande en temps réel
├── ContextPanel.tsx      # Panel contexte passif
├── Sidebar.tsx           # Navigation + conversations récentes
├── LayoutShell.tsx       # Wrapper layout conditionnel
├── PresentationViewer.tsx # Viewer PPTX
└── TranscriptPanel.tsx   # Panel transcription discussions

lib/
├── api.ts                # Client API backend (chat, contacts, discussions, etc.)
├── constants.ts          # Configuration (URLs, timeouts, seuils)
├── types.ts              # Types TypeScript
├── supabase.ts           # Client Supabase
├── audioUtils.ts         # Encodage audio (int16ToBase64)
├── contextBuffer.ts      # Buffer contexte en mémoire (max 50 segments, 30 min)
├── contextPersistence.ts # Persistance IndexedDB
└── integrations.ts       # OAuth helpers (HubSpot, Slack, Gmail, Outlook)
```

### Backend (`backend/app/`)

```
main.py                   # Entry point FastAPI + CORS + routes
config.py                 # Settings Pydantic (env vars)

routes/
├── health.py             # GET /api/health
├── stt_token.py          # GET /api/stt-token → token ElevenLabs
├── chat.py               # POST /api/chat → SSE streaming (agent)
├── wakeword.py           # WS /api/wakeword → OpenWakeWord
├── gemini_stt.py         # WS /api/gemini-stt → Mistral Voxtral Mini (transcription passive)
├── contacts.py           # CRUD /api/contacts
├── conversations.py      # CRUD /api/conversations
├── discussions.py        # CRUD /api/discussions
├── summaries.py          # GET/DELETE /api/summaries
├── activity.py           # GET /api/activity
└── settings.py           # GET/PUT /api/settings

services/
├── llm_service.py        # Communication avec aura-agent (SSE proxy)
├── stt_service.py        # Génération token ElevenLabs
├── wakeword_service.py   # Singleton OpenWakeWord (ONNX)
├── supabase_client.py    # Client Supabase
└── context_service.py    # Services contexte
```

### Supabase (`supabase/`)

```
functions/
├── aura-agent/           # ⭐ Agent principal (Claude + ~30 outils)
│   ├── index.ts          # Entry point SSE
│   ├── systemPrompt.ts   # Prompt système
│   ├── toolDefinitions.ts # Définitions outils
│   ├── activityLog.ts    # Logging activité
│   └── tools/            # Implémentations outils
│       ├── core.ts       # Contexte, résumés
│       ├── email.ts      # Email (Gmail/Outlook)
│       ├── contacts.ts   # Contacts + calendrier
│       ├── messaging.ts  # SMS + WhatsApp
│       ├── hubspot.ts    # HubSpot CRM
│       ├── slack.ts      # Slack
│       ├── web.ts        # Recherche web
│       ├── datagouv.ts   # data.gouv.fr
│       ├── presentation.ts # Génération PPTX
│       └── report.ts     # Génération PDF
├── context-enrichment/   # Enrichissement contexte avant agent
├── summarize-context/    # Résumé automatique contexte
├── transcribe-and-summarize/ # Agrégation transcription passive
├── send-email/           # Envoi email
├── send-sms/             # Envoi SMS
├── send-whatsapp/        # Envoi WhatsApp
├── slack-api/            # Opérations Slack
├── hubspot-api/          # Opérations HubSpot
├── contacts-api/         # Opérations contacts
├── calendar-api/         # Opérations calendrier
├── pptx-proxy/           # Génération PPTX
├── stt-proxy/            # Proxy STT
└── scribe-token/         # Token ElevenLabs

migrations/ (17 fichiers SQL)
├── 001_create_user_settings
├── 002_create_conversations
├── 20260227_create_transcriptions_table
├── 20260228_create_contacts
├── 20260229_create_email_integrations
├── 20260302_create_calendar_events
├── 20260303_create_sms_messages
├── 20260304_add_user_id_columns
├── 20260305_create_hubspot_integrations
├── 20260306_create_slack_integrations
├── 20260313_create_activity_logs
├── 20260314_create_presentations_bucket
├── 20260323_alter_whatsapp_integrations
├── 20260324_add_pdf_to_presentations_bucket
└── 20260325_add_logo_to_user_settings
```

---

## 3. Machine à états (useAuraSession)

```
initializing → (mic + STT + wakeword init)
  ↓
idle (mic actif, STT passif Mistral écoute, wakeword écoute)
  ↓ [wake word détecté] ou [push-to-talk]
listening (STT commande ElevenLabs actif, passif pausé)
  ↓ [silence 2s] → finalise commande
thinking (appel LLM agent)
  ↓ [réponse reçue]
speaking (TTS audio joue)
  ↓ [audio fini]
conversing (fenêtre 8s pour réponse sans wake word)
  ├─ [utilisateur parle] → listening (barge-in)
  └─ [timer expire] → idle

Barge-in : détection volume > seuil pendant speaking/conversing
  - Seuil conversing : 12/100
  - Seuil speaking (TTS écho) : 25/100
  - 3 frames consécutives (~300ms) → interruption
```

---

## 4. STT : Architecture double

### Écoute passive (continue) — Mistral Voxtral Mini
- **Fichier backend** : `backend/app/routes/gemini_stt.py` (nom legacy, utilise Mistral)
- **Endpoint** : WebSocket `/api/gemini-stt`
- **Fonctionnement** :
  - Frontend envoie chunks audio PCM en base64 via WebSocket
  - Backend accumule pendant **15 secondes**
  - Resample à 16kHz, convertit en WAV
  - Envoie à `POST https://api.mistral.ai/v1/audio/transcriptions` (model: `voxtral-mini-latest`, language: `fr`)
  - Retourne la transcription au frontend
- **Coût** : Très faible (~$0.02/heure d'audio)
- **Fichier frontend** : `frontend/src/hooks/usePassiveSTT.ts`

### Commande (après wake word) — ElevenLabs Scribe v2
- **Fichier frontend** : `frontend/src/hooks/useCommandSTT.ts`
- **Connexion directe** : `wss://api.elevenlabs.io/v1/speech-to-text/realtime`
- **Token** : Via `GET /api/stt-token` (backend génère token éphémère)
- **Fonctionnement** :
  - Temps réel, VAD intégré (silence 0.8s → commit)
  - Envoie `previous_text` (contexte passif) pour améliorer la précision
  - Timeout max : 30s
  - Accumule les `committed_transcript_with_timestamps`
  - Finalise après 2s de silence → `onCommandComplete()`
- **Qualité** : Supérieure (temps réel, optimisé pour commandes)
- **Coût** : Plus élevé qu'Mistral

---

## 5. Variables d'environnement

### Backend (`.env`)
```
ELEVENLABS_API_KEY=        # Clé API ElevenLabs (STT commande + TTS)
ELEVENLABS_VOICE_ID=       # ID voix TTS
AURA_AGENT_URL=            # URL edge function aura-agent Supabase
AURA_AGENT_TOKEN=          # Token anon Supabase (pour apikey header)
SUPABASE_URL=              # URL projet Supabase
SUPABASE_ANON_KEY=         # Clé anon Supabase
ANTHROPIC_API_KEY=         # Clé Claude (utilisée dans edge functions)
GEMINI_API_KEY=            # Clé Google Gemini (plus utilisée activement)
MISTRAL_API_KEY=           # Clé Mistral (STT passif Voxtral Mini)
```

### Frontend (`.env`)
```
NEXT_PUBLIC_BACKEND_URL=http://localhost:8000
NEXT_PUBLIC_SUPABASE_URL=
NEXT_PUBLIC_SUPABASE_ANON_KEY=
NEXT_PUBLIC_GOOGLE_CLIENT_ID=       # OAuth Google
NEXT_PUBLIC_HUBSPOT_CLIENT_ID=      # OAuth HubSpot
NEXT_PUBLIC_MICROSOFT_CLIENT_ID=    # OAuth Microsoft
NEXT_PUBLIC_SLACK_CLIENT_ID=        # OAuth Slack
```

---

## 6. Routes API backend

| Méthode | Route | Description |
|---------|-------|-------------|
| GET | `/api/health` | Health check |
| GET | `/api/stt-token` | Token ElevenLabs éphémère |
| POST | `/api/chat` | Chat streaming SSE (agent LLM) |
| WS | `/api/wakeword` | Détection wake word |
| WS | `/api/gemini-stt` | STT passif Mistral |
| GET/POST/PUT/DELETE | `/api/contacts` | CRUD contacts |
| GET/POST/DELETE | `/api/conversations` | CRUD conversations |
| POST | `/api/conversations/{id}/messages` | Ajouter message |
| GET/DELETE | `/api/discussions` | Discussions |
| GET/DELETE | `/api/summaries` | Résumés |
| GET | `/api/activity` | Journal d'activité |
| GET/PUT | `/api/settings` | Paramètres utilisateur |

---

## 7. Pages frontend

| Route | Page | Description |
|-------|------|-------------|
| `/` | Main | Interface vocale, chat, orbe, contexte |
| `/login` | Login | Auth Supabase (OAuth) |
| `/contacts` | Contacts | Liste, recherche, CRUD contacts |
| `/discussions` | Discussions | Historique des discussions |
| `/summaries` | Résumés | Résumés auto du contexte passif |
| `/activity` | Activité | Journal des actions AURA |
| `/settings` | Paramètres | Préférences, connecteurs OAuth, déconnexion |

---

## 8. Base de données (Supabase PostgreSQL)

### Tables principales
| Table | Description |
|-------|-------------|
| `user_settings` | Préférences utilisateur (timezone, langue, logo) |
| `conversations` | Métadonnées conversations (titre, dates) |
| `conversation_messages` | Messages (role, content, attachments) |
| `transcriptions` | Segments transcription passive |
| `contacts` | Contacts (name, email, phone, company) |
| `activity_logs` | Journal d'activité (action_type, details) |
| `email_integrations` | Tokens OAuth email (Gmail/Outlook) |
| `calendar_events` | Événements calendrier |
| `sms_messages` | Messages SMS |
| `hubspot_integrations` | Config HubSpot |
| `slack_integrations` | Config Slack |
| `presentations` | Bucket storage PPTX/PDF |

---

## 9. Constantes clés (`constants.ts`)

```typescript
BACKEND_URL = "http://localhost:8000"
CONVERSATION_WINDOW_MS = 8000       // 8s fenêtre conversation
BARGEIN_VOLUME_THRESHOLD = 12       // Seuil barge-in conversing
BARGEIN_VOLUME_THRESHOLD_SPEAKING = 25  // Seuil pendant TTS
BARGEIN_CONSECUTIVE_FRAMES = 3      // Frames pour déclencher (~300ms)
COMMAND_TIMEOUT_MS = 30000          // Max écoute commande
COMMIT_SILENCE_MS = 2000            // Silence avant commit
TOKEN_REFRESH_MS = 14 * 60 * 1000  // Refresh token 14 min
MAX_CONTEXT_SEGMENTS = 50           // Max segments contexte
MAX_CONTEXT_AGE_MS = 30 * 60 * 1000 // 30 min max
BATCH_INTERVAL_SECS = 15            // Intervalle batch Mistral STT (backend)
```

---

## 10. Outils agent (aura-agent edge function)

L'agent Claude dispose de ~30 outils :

| Catégorie | Outils |
|-----------|--------|
| **Core** | get_recent_context, generate_summary, save_summary, search_memory |
| **Email** | send_email, list_emails, read_email, send_email_with_attachment |
| **Contacts** | search_contacts, save_contact, add_meeting_note |
| **Calendrier** | create_calendar_event, list_events, update_event |
| **Messagerie** | send_sms, send_whatsapp |
| **HubSpot** | search/create/update contacts, deals, pipeline, notes |
| **Slack** | send_message, send_dm, list_channels, list_users, get_history |
| **Web** | web_search |
| **data.gouv.fr** | search_datasets, get_dataset, query_data |
| **Documents** | create_report (PDF), create_presentation (PPTX) |

---

## 11. Démarrage local

```bash
# Backend
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000

# Frontend
cd frontend
npm install
npm run dev  # localhost:3000
```

**Note** : Ne pas utiliser `--reload` avec uvicorn si le venv est dans le dossier backend — watchfiles détecte les changements dans venv et redémarre en boucle.

---

## 12. Problèmes connus et points d'attention

### Wake word
- Le modèle `dis_aura.onnx` doit être dans `/openwake/dis_aura.onnx` (chemin Docker) ou ajuster le chemin dans `wakeword_service.py`
- Erreur actuelle : `ValueError: Could not find pretrained model`
- **Solution** : Vérifier le chemin du modèle ONNX ou utiliser le fallback push-to-talk

### STT passif (Mistral)
- Batch de 15 secondes → latence de ~15-20s pour voir la transcription
- Si rate limit 429 : réduire la fréquence ou upgrader le plan API
- Le fichier s'appelle `gemini_stt.py` mais utilise **Mistral** (nom legacy)

### OAuth connecteurs
- Les redirect URIs doivent correspondre à ce qui est enregistré dans les consoles développeur
- Callback : `/oauth-callback.html` (fichier statique dans `public/`)
- Slack : erreur `invalid_team_for_non_distributed_app` → activer "Distribute App" dans Slack console

### Contacts
- La table utilise un seul champ `name` (pas `first_name`/`last_name`)
- Le backend a été corrigé, le frontend peut encore avoir des références à l'ancien schéma

### Pydantic
- Versions sensibles : `pydantic==2.12.5` + `pydantic-core==2.41.5`
- Si erreur d'import : `pip install --force-reinstall pydantic pydantic-core`

---

## 13. Déploiement (Hostinger VPS)

```bash
# 1. Upload code
scp -r backend/ root@ip:/opt/aura-backend

# 2. Setup venv + deps
cd /opt/aura-backend && python3.11 -m venv venv
source venv/bin/activate && pip install -r requirements.txt

# 3. Service systemd
# Créer /etc/systemd/system/aura-backend.service
# ExecStart=/opt/aura-backend/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000

# 4. Nginx reverse proxy + HTTPS (Let's Encrypt)
# 5. Mettre à jour CORS_ORIGINS dans .env
# 6. Frontend : déployer sur Vercel ou en SSR sur le VPS
```

---

## 14. Historique Git récent

```
862c54c Merge Marwa's deployed project - accept all her changes
3deeeaf chore: remove docs/specs from repo, update .gitignore
f818c5a feat: streaming SSE responses + Gotenberg HTML PDF integration
630448d feat: advanced PDF reports, Slack/WhatsApp improvements, logo upload
755c3b3 Fix: always send Authorization header, fix double-slash in Supabase URL
98ec90b Fix: add apikey header to edge function calls
ea7895c Modularize aura-agent, add Slack file uploads, complete all features
ef55395 Add Docker setup and fix rendering/wakeword issues
499750d v1 terminé
```

---

## 15. Checklist pour le prochain développeur

- [ ] Lire `useAuraSession.ts` (fichier clé, ~570 lignes)
- [ ] Comprendre la machine à états (idle → listening → thinking → speaking → conversing)
- [ ] Vérifier le chemin du modèle wake word (`dis_aura.onnx`)
- [ ] Tester le token ElevenLabs (`/api/stt-token`)
- [ ] Vérifier la config Supabase (AURA_AGENT_URL, tokens)
- [ ] Tester le streaming SSE du chat
- [ ] Vérifier les OAuth redirect URIs (Google, Microsoft, Slack, HubSpot)
- [ ] Tester la transcription passive Mistral (WebSocket `/api/gemini-stt`)
- [ ] Vérifier les politiques RLS Supabase
- [ ] Tester le barge-in (parler pendant que AURA parle)
