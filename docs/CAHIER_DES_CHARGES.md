# Catalogue des fonctionnalités — Aura Étoile

> Liste des fonctionnalités d'Aura, par module. Document court de référence ; pour le détail technique voir [ARCHITECTURE.md](ARCHITECTURE.md).
>
> Légende état : ✅ fait · ⚙️ partiel / POC · ❌ à construire (cible device).

## Présentation

Aura est un **assistant vocal** activé par le mot « dis Aura ». La détection du mot d'activation et la reconnaissance du locuteur se font **sur l'appareil** (edge) ; la **conversation, les actions et la réponse vocale** se font dans le **cloud** via un agent IA (Claude) doté de nombreux outils métier.

---

## A. Activation & interaction vocale

| Fonctionnalité | Description | État |
|---|---|---|
| Mot d'activation | Détection « dis Aura » (activation) et « stop Aura » (interruption) | ✅ |
| Son de confirmation | Bip audio quand le mot-clé est détecté (réglable) | ✅ |
| Machine à états | idle → listening → thinking → speaking → conversing | ✅ |
| Conversation continue | Rester en écoute ~12 s après une réponse, sans répéter « Aura » | ✅ |
| Barge-in | Interrompre Aura pendant qu'elle parle (détection VAD) | ✅ |
| Mute (logiciel) | Couper le micro depuis l'interface | ✅ |
| Mute (matériel) | Coupure physique du micro via MOSFET (RGPD) | ❌ |
| Voix & vitesse TTS | Choix du timbre de voix et vitesse (0.5×–2×) | ✅ |
| Indicateurs visuels | Orbe réactif au volume + barre de volume + messages d'état | ✅ |

## B. Reconnaissance du locuteur

| Fonctionnalité | Description | État |
|---|---|---|
| Enrôlement biométrique | Création d'une empreinte vocale (~25 s, 5 échantillons) | ✅ |
| Vérification du locuteur | Comparaison temps réel (ECAPA-TDNN, similarité cosinus) | ✅ |
| Parole adressée | Décider si l'utilisateur parle à Aura (VAD + locuteur + intention) | ✅ |
| Gestion des voix | Liste des voix enrôlées + suppression | ✅ |

## C. Transcription (STT)

| Fonctionnalité | Description | État |
|---|---|---|
| STT de commande | Transcription de la commande dirigée (ElevenLabs Scribe) | ✅ |
| STT passive | Transcription continue d'ambiance (Mistral Voxtral) | ✅ |
| Écoute passive & contexte | Capture du contexte ambiant, mémoire glissante | ✅ |
| Langue de transcription | FR / EN / détection automatique | ✅ |
| Rétention | Durée de conservation des transcriptions (1–30 j) | ✅ |

## D. Assistant conversationnel (agent)

| Fonctionnalité | Description | État |
|---|---|---|
| Agent IA à outils | Claude `claude-sonnet-4-6` avec appel d'outils (jusqu'à 5 tours) | ✅ |
| Mémoire / contexte | `get_recent_context`, `search_memory` (recherche dans l'historique) | ✅ |
| Génération de résumés | Résumé structuré (court/détaillé) + sauvegarde | ✅ |
| Résumés roulants | Synthèse automatique tous les ~30 segments d'écoute passive | ✅ |
| Réponse vocale | Restitution TTS de la réponse | ✅ |

## E. Outils & actions de l'agent

| Domaine | Outils / actions | Service |
|---|---|---|
| **Email** | envoyer, lister, lire, envoyer avec pièce jointe | Gmail / Outlook (OAuth) |
| **SMS** | envoyer un SMS | Twilio |
| **WhatsApp** | envoyer texte / image / document | Meta WhatsApp Business |
| **Slack** | message canal, DM, lister canaux/utilisateurs, historique | Slack (OAuth) |
| **CRM** | contacts (CRUD), deals, notes, pipeline | HubSpot (OAuth) |
| **Agenda** | créer / lister / modifier des événements | Google Calendar / Outlook |
| **Contacts** | rechercher, créer, ajouter une note de réunion | Supabase (local) |
| **Présentations** | créer un PPTX + PDF (layouts variés) | PPTX Server + Gotenberg |
| **Rapports** | créer un document PDF structuré (thèmes, types) | Gotenberg |
| **Recherche web** | recherche internet temps réel | Tavily |
| **Données ouvertes** | rechercher / interroger des datasets | data.gouv.fr |

> ~43 outils au total exposés à l'agent (détail : `back/supabase/functions/aura-agent/toolDefinitions.ts`).

## F. Gestion & historique (interface)

| Fonctionnalité | Description | État |
|---|---|---|
| Conversations / discussions | Historique groupé par date, recherche, restauration, suppression | ✅ |
| Résumés | Bibliothèque des synthèses (rolling / session), vue repliée/développée | ✅ |
| Activité | Frise chronologique des actions et outils exécutés, filtres | ✅ |
| Carnet de contacts | Contacts locaux (nom, email, tél, entreprise, notes) | ✅ |
| Pièces jointes | Téléchargement et visualisation des PDF / PPTX générés | ✅ |

## G. Paramètres & intégrations

| Fonctionnalité | Description | État |
|---|---|---|
| Connecteurs OAuth | Gmail, Outlook, HubSpot, Slack (connexion en 1 clic) | ✅ |
| Twilio | Configuration manuelle (SID, token, numéro) | ✅ |
| WhatsApp | Embedded Signup (Facebook) ou configuration manuelle | ✅ |
| Réglages généraux | Langue (FR/EN/ES), thème clair/sombre, fuseau, notifications | ✅ |
| Logo des rapports | Upload d'un logo inséré automatiquement dans les PDF | ✅ |
| Zone danger | Purge de l'historique, suppression du compte | ✅ |

## H. Compte & sécurité

| Fonctionnalité | Description | État |
|---|---|---|
| Authentification | Inscription / connexion email + mot de passe (Supabase, JWT) | ✅ |
| Empreintes vocales | Stockées localement, synchronisées chiffrées vers le cloud | ⚙️ |
| Mute matériel RGPD | Coupure électrique du micro (MOSFET) | ❌ |
| Cloisonnement edge/cloud | Wakeword & locuteur hors-ligne sur l'appareil | ✅ |

## I. Cible device (Raspberry) — à venir

| Fonctionnalité | Description | État |
|---|---|---|
| Capture micro locale | Lecture du ReSpeaker via ALSA (16 kHz) sur l'appareil | ❌ |
| Sortie haut-parleur | Lecture TTS sur enceinte locale | ❌ |
| GPIO bouton + LED | Bouton mute physique + LED d'état (couleurs par état) | ❌ |
| BLE | Onboarding WiFi, état temps réel, volume (app mobile) | ❌ |
| Mise à jour modèles (OTA) | Téléchargement signé des modèles depuis le cloud | ❌ |

> Détail matériel : [../raspberry/docs/HARDWARE_PROTOTYPE.md](../raspberry/docs/HARDWARE_PROTOTYPE.md).

---

## Récapitulatif (où vit chaque brique)

| Fonctionnalité | Où | État |
|---|---|---|
| Mot d'activation | edge (`raspberry`) | ✅ |
| Reconnaissance du locuteur | edge (`raspberry`) | ✅ |
| STT commande | externe (ElevenLabs) | ✅ |
| STT passive | cloud (`back/backend`) → Mistral | ✅ |
| Agent + outils | cloud (`back/supabase` aura-agent) | ✅ |
| Réponse vocale (TTS) | cloud → ElevenLabs | ✅ |
| Intégrations (email, CRM, agenda, Slack, SMS, WhatsApp) | cloud (Edge Functions) | ✅ |
| Génération documents (PPTX/PDF) | cloud (pptx-proxy + Gotenberg) | ✅ |
| Interface, historique, réglages | front (`front`) | ✅ |
| Capture micro locale / GPIO / BLE / OTA | edge device | ❌ |

---

*Voir aussi : [ARCHITECTURE.md](ARCHITECTURE.md) (technique), [HANDOFF.md](HANDOFF.md) (fonctionnel détaillé).*
