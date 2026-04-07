// ═══════════════════════════════════════════════════════════════
// OUTILS DE L'AGENT (définitions pour Claude)
// Extracted from index.ts for modularity
// ═══════════════════════════════════════════════════════════════

export const AGENT_TOOLS = [
  {
    name: "get_recent_context",
    description:
      "Récupère les transcriptions récentes depuis la base de données. " +
      "Utilise ce tool quand l'utilisateur fait référence à quelque chose " +
      "dit récemment ('tout à l'heure', 'ce matin', 'la réunion de 10h'). " +
      "Retourne le texte brut des transcriptions récentes.",
    input_schema: {
      type: "object" as const,
      properties: {
        minutes_back: {
          type: "integer",
          description:
            "Nombre de minutes en arrière à récupérer. Défaut: 60. Max: 180. " +
            "Exemples: 30 pour la dernière demi-heure, 60 pour la dernière heure, " +
            "180 pour les 3 dernières heures.",
        },
      },
      required: [],
    },
  },
  {
    name: "generate_summary",
    description:
      "Génère un résumé structuré en markdown à partir d'un texte de transcription. " +
      "Utilise ce tool APRÈS avoir récupéré le contexte avec get_recent_context " +
      "ou search_memory. Retourne un titre et un résumé complet.",
    input_schema: {
      type: "object" as const,
      properties: {
        context: {
          type: "string",
          description: "Le texte de transcription à résumer.",
        },
        format: {
          type: "string",
          enum: ["short", "detailed"],
          description:
            "Format du résumé: 'short' pour un résumé concis, 'detailed' pour un résumé exhaustif. " +
            "Défaut: 'detailed'.",
        },
      },
      required: ["context"],
    },
  },
  {
    name: "save_summary",
    description:
      "Sauvegarde un résumé complet dans la base de données. " +
      "Utilise ce tool APRÈS generate_summary pour persister le résumé. " +
      "Retourne l'ID de la ligne insérée.",
    input_schema: {
      type: "object" as const,
      properties: {
        transcription_text: {
          type: "string",
          description: "Le texte brut de la transcription originale.",
        },
        title: {
          type: "string",
          description: "Le titre du résumé (5-8 mots).",
        },
        summary: {
          type: "string",
          description: "Le résumé complet en markdown.",
        },
      },
      required: ["transcription_text", "title", "summary"],
    },
  },
  {
    name: "search_memory",
    description:
      "Recherche dans les transcriptions passées par mots-clés. " +
      "Utilise ce tool quand l'utilisateur cherche une information spécifique " +
      "dans des conversations passées ('qu'est-ce qu'on a dit sur X', " +
      "'retrouve le prix mentionné mardi', 'la réunion avec M. Azoulay'). " +
      "Supporte les filtres temporels optionnels.",
    input_schema: {
      type: "object" as const,
      properties: {
        query: {
          type: "string",
          description: "Mots-clés ou phrase à rechercher.",
        },
        date_start: {
          type: "string",
          description:
            "Date de début au format ISO 8601 (ex: '2026-02-27T00:00:00Z'). Optionnel.",
        },
        date_end: {
          type: "string",
          description:
            "Date de fin au format ISO 8601 (ex: '2026-02-27T23:59:59Z'). Optionnel.",
        },
      },
      required: ["query"],
    },
  },
  {
    name: "send_email",
    description:
      "Envoie un email via le compte Gmail ou Outlook connecté de l'utilisateur. " +
      "Utilise ce tool quand l'utilisateur demande d'envoyer un email ou un message. " +
      "Exemples: 'envoie un email à jean@example.com pour lui dire...', " +
      "'écris un mail à mon collègue pour confirmer la réunion'.",
    input_schema: {
      type: "object" as const,
      properties: {
        to: {
          type: "string",
          description:
            "Adresse email du destinataire (ex: jean.dupont@gmail.com).",
        },
        subject: {
          type: "string",
          description: "Sujet de l'email (court et descriptif).",
        },
        body: {
          type: "string",
          description:
            "Contenu de l'email. Rédige un email professionnel et clair basé sur la demande de l'utilisateur.",
        },
      },
      required: ["to", "subject", "body"],
    },
  },
  {
    name: "list_emails",
    description:
      "Liste les emails récents de la boîte de réception de l'utilisateur (Gmail ou Outlook). " +
      "Utilise ce tool quand l'utilisateur demande de lire ses emails, vérifier sa boîte mail, " +
      "ou voir ses messages récents. " +
      "Exemples: 'lis mes emails', 'qu'est-ce que j'ai reçu ?', 'des nouveaux messages ?'.",
    input_schema: {
      type: "object" as const,
      properties: {
        max_results: {
          type: "number",
          description: "Nombre maximum d'emails à retourner (défaut: 10, max: 20).",
        },
        query: {
          type: "string",
          description: "Recherche par mots-clés dans les emails (ex: 'facture', 'réunion'). Optionnel.",
        },
        unread_only: {
          type: "boolean",
          description: "Si true, ne retourne que les emails non lus. Défaut: false.",
        },
      },
      required: [],
    },
  },
  {
    name: "read_email",
    description:
      "Lit le contenu complet d'un email spécifique par son identifiant. " +
      "Utilise ce tool après list_emails quand l'utilisateur demande de lire un email en particulier. " +
      "Exemples: 'lis le premier email', 'ouvre le mail de Jean'.",
    input_schema: {
      type: "object" as const,
      properties: {
        email_id: {
          type: "string",
          description: "Identifiant unique de l'email à lire (obtenu via list_emails).",
        },
      },
      required: ["email_id"],
    },
  },
  {
    name: "search_contacts",
    description:
      "Recherche un contact dans le carnet d'adresses par nom, entreprise ou email. " +
      "Utilise ce tool AVANT send_email quand l'utilisateur mentionne un nom de personne " +
      "pour trouver son adresse email. Retourne les informations du contact.",
    input_schema: {
      type: "object" as const,
      properties: {
        query: {
          type: "string",
          description: "Nom, entreprise ou email à rechercher (ex: 'Jean', 'Dupont', 'Acme').",
        },
      },
      required: ["query"],
    },
  },
  {
    name: "save_contact",
    description:
      "Crée un nouveau contact dans le carnet d'adresses. " +
      "Utilise ce tool quand l'utilisateur demande d'ajouter ou créer un contact.",
    input_schema: {
      type: "object" as const,
      properties: {
        name: {
          type: "string",
          description: "Nom complet du contact.",
        },
        email: {
          type: "string",
          description: "Adresse email du contact.",
        },
        phone: {
          type: "string",
          description: "Numéro de téléphone du contact.",
        },
        company: {
          type: "string",
          description: "Entreprise ou organisation du contact.",
        },
      },
      required: ["name"],
    },
  },
  {
    name: "add_meeting_note",
    description:
      "Ajoute un résumé de réunion au dossier d'un contact/client. " +
      "Utilise ce tool après une réunion pour sauvegarder les points discutés dans le dossier du client. " +
      "Si le contact n'existe pas, il sera créé automatiquement.",
    input_schema: {
      type: "object" as const,
      properties: {
        contact_name: {
          type: "string",
          description: "Nom du contact/client concerné par la réunion.",
        },
        title: {
          type: "string",
          description: "Titre de la réunion (ex: 'Point projet Mars 2026').",
        },
        summary: {
          type: "string",
          description: "Résumé structuré de la réunion (points discutés, décisions, actions).",
        },
      },
      required: ["contact_name", "title", "summary"],
    },
  },
  {
    name: "create_calendar_event",
    description:
      "Crée un rendez-vous ou événement dans le Google Calendar ou Outlook Calendar de l'utilisateur. " +
      "Utilise ce tool quand l'utilisateur demande de planifier, programmer ou créer un rendez-vous. " +
      "Exemples: 'planifie un rendez-vous demain à 14h', 'mets une réunion jeudi matin avec Jean'.",
    input_schema: {
      type: "object" as const,
      properties: {
        title: {
          type: "string",
          description: "Titre du rendez-vous (ex: 'Réunion projet Mars', 'Déjeuner avec Jean').",
        },
        start_datetime: {
          type: "string",
          description:
            "Date et heure de début au format ISO 8601 avec fuseau horaire Europe/Paris " +
            "(ex: '2026-03-03T14:00:00+01:00'). Déduis la date depuis le contexte et la date actuelle.",
        },
        end_datetime: {
          type: "string",
          description:
            "Date et heure de fin au format ISO 8601. Si non précisé par l'utilisateur, " +
            "mets 1 heure après start_datetime.",
        },
        description: {
          type: "string",
          description: "Description ou notes pour le rendez-vous. Optionnel.",
        },
        location: {
          type: "string",
          description: "Lieu du rendez-vous. Optionnel.",
        },
        attendees: {
          type: "array",
          items: { type: "string" },
          description:
            "Liste des adresses email des participants. " +
            "Utilise search_contacts AVANT pour trouver les emails des participants mentionnés par nom.",
        },
      },
      required: ["title", "start_datetime"],
    },
  },
  {
    name: "list_calendar_events",
    description:
      "Recherche les rendez-vous planifiés dans la base de données par titre ou date. " +
      "Utilise ce tool AVANT update_calendar_event pour retrouver l'event_id du rendez-vous à modifier. " +
      "Retourne la liste des rendez-vous avec leur id, titre, date, participants et provider.",
    input_schema: {
      type: "object" as const,
      properties: {
        query: {
          type: "string",
          description: "Recherche par titre du rendez-vous (ex: 'réunion', 'déjeuner'). Optionnel.",
        },
        date_start: {
          type: "string",
          description: "Date de début de la plage de recherche au format ISO 8601. Optionnel.",
        },
        date_end: {
          type: "string",
          description: "Date de fin de la plage de recherche au format ISO 8601. Optionnel.",
        },
      },
      required: [],
    },
  },
  {
    name: "update_calendar_event",
    description:
      "Modifie un rendez-vous existant dans Google Calendar ou Outlook Calendar. " +
      "Utilise list_calendar_events d'abord pour obtenir l'event_id. " +
      "Tu peux modifier le titre, les horaires, la description, le lieu et les participants.",
    input_schema: {
      type: "object" as const,
      properties: {
        event_id: {
          type: "string",
          description: "L'UUID du rendez-vous dans la base de données (obtenu via list_calendar_events).",
        },
        title: {
          type: "string",
          description: "Nouveau titre du rendez-vous. Optionnel.",
        },
        start_datetime: {
          type: "string",
          description: "Nouvelle date/heure de début au format ISO 8601. Optionnel.",
        },
        end_datetime: {
          type: "string",
          description: "Nouvelle date/heure de fin au format ISO 8601. Optionnel.",
        },
        description: {
          type: "string",
          description: "Nouvelle description. Optionnel.",
        },
        location: {
          type: "string",
          description: "Nouveau lieu. Optionnel.",
        },
        attendees: {
          type: "array",
          items: { type: "string" },
          description:
            "Liste COMPLÈTE des adresses email des participants (anciens + nouveaux). " +
            "Récupère les participants existants depuis list_calendar_events avant d'ajouter les nouveaux.",
        },
      },
      required: ["event_id"],
    },
  },
  {
    name: "send_sms",
    description:
      "Envoie un SMS via Twilio au numéro de téléphone indiqué. " +
      "Utilise ce tool quand l'utilisateur demande d'envoyer un SMS, un texto ou un message texte. " +
      "Si l'utilisateur mentionne un nom, utilise search_contacts d'abord pour trouver le numéro.",
    input_schema: {
      type: "object" as const,
      properties: {
        to: {
          type: "string",
          description:
            "Numéro de téléphone du destinataire. Accepte le format français (06 12 34 56 78) " +
            "ou international (+33 6 12 34 56 78).",
        },
        message: {
          type: "string",
          description: "Contenu du SMS à envoyer. Garde-le concis (160 caractères max recommandé).",
        },
      },
      required: ["to", "message"],
    },
  },
  {
    name: "send_whatsapp",
    description:
      "Envoie un message WhatsApp via l'API Meta WhatsApp Business. " +
      "Supporte le texte, les images et les documents (PDF, PPTX). " +
      "Utilise ce tool quand l'utilisateur demande d'envoyer un WhatsApp ou un message WhatsApp. " +
      "Si l'utilisateur mentionne un nom, utilise search_contacts d'abord pour trouver le numéro. " +
      "Pour envoyer une présentation créée avec create_presentation, utilise media_type='document' " +
      "avec le file_path et file_name retournés par create_presentation. " +
      "Pour envoyer une image depuis une URL, utilise media_type='image' avec media_url. " +
      "IMPORTANT : les messages texte libre ne sont délivrés que si le destinataire a envoyé un message " +
      "au numéro Business dans les dernières 24h. Sinon, utilise use_template=true.",
    input_schema: {
      type: "object" as const,
      properties: {
        to: {
          type: "string",
          description:
            "Numéro de téléphone du destinataire. Accepte le format français (06 12 34 56 78) " +
            "ou international (+33 6 12 34 56 78).",
        },
        message: {
          type: "string",
          description: "Contenu du message WhatsApp à envoyer (requis en mode texte, ignoré en mode template/média).",
        },
        use_template: {
          type: "boolean",
          description:
            "Si true, envoie un template pré-approuvé (hello_world) au lieu d'un message texte libre. Défaut: false.",
        },
        media_type: {
          type: "string",
          enum: ["image", "document"],
          description: "Type de média à envoyer. 'image' pour une photo/image, 'document' pour un fichier (PDF, PPTX, etc.).",
        },
        file_path: {
          type: "string",
          description: "Chemin du fichier dans le storage Supabase (retourné par create_presentation). Utilisé pour envoyer un fichier stocké.",
        },
        media_url: {
          type: "string",
          description: "URL publique HTTPS du média à envoyer (pour les images ou fichiers externes).",
        },
        file_name: {
          type: "string",
          description: "Nom du fichier tel qu'il apparaîtra au destinataire (requis pour type 'document').",
        },
        caption: {
          type: "string",
          description: "Légende ou message accompagnant le média (optionnel).",
        },
      },
      required: ["to"],
    },
  },
  // ── HubSpot CRM Tools ──
  {
    name: "hubspot_search_contacts",
    description:
      "Recherche des contacts dans le CRM HubSpot par nom, email ou entreprise. " +
      "Utilise ce tool quand l'utilisateur demande des informations sur un client/prospect dans le CRM, " +
      "ou veut vérifier si un contact existe dans HubSpot.",
    input_schema: {
      type: "object" as const,
      properties: {
        query: {
          type: "string",
          description: "Nom, email ou entreprise à rechercher dans HubSpot CRM.",
        },
      },
      required: ["query"],
    },
  },
  {
    name: "hubspot_create_contact",
    description:
      "Crée un nouveau contact dans le CRM HubSpot. " +
      "Utilise ce tool quand l'utilisateur demande d'ajouter un contact/prospect/client dans HubSpot ou le CRM. " +
      "Le prénom est obligatoire. Les autres champs sont optionnels.",
    input_schema: {
      type: "object" as const,
      properties: {
        firstname: { type: "string", description: "Prénom du contact." },
        lastname: { type: "string", description: "Nom de famille du contact." },
        email: { type: "string", description: "Adresse email du contact." },
        phone: { type: "string", description: "Numéro de téléphone du contact." },
        company: { type: "string", description: "Entreprise du contact." },
        jobtitle: { type: "string", description: "Poste / fonction du contact." },
        mobilephone: { type: "string", description: "Téléphone portable du contact." },
        address: { type: "string", description: "Adresse postale du contact." },
        city: { type: "string", description: "Ville du contact." },
        zip: { type: "string", description: "Code postal du contact." },
        country: { type: "string", description: "Pays du contact." },
        website: { type: "string", description: "Site web du contact." },
        hs_lead_status: { type: "string", description: "Statut du lead (ex: NEW, OPEN, IN_PROGRESS, CONNECTED, BAD_TIMING, UNQUALIFIED)." },
        lifecyclestage: { type: "string", description: "Étape du cycle de vie (ex: subscriber, lead, marketingqualifiedlead, salesqualifiedlead, opportunity, customer, evangelist)." },
      },
      required: ["firstname"],
    },
  },
  {
    name: "hubspot_update_contact",
    description:
      "Met à jour un contact existant dans le CRM HubSpot (modifier ou vider n'importe quel champ). " +
      "Utilise hubspot_search_contacts d'abord pour trouver le contact_id. " +
      "Pour VIDER un champ, passe une chaîne vide \"\".",
    input_schema: {
      type: "object" as const,
      properties: {
        contact_id: { type: "string", description: "L'ID HubSpot du contact (obtenu via hubspot_search_contacts)." },
        firstname: { type: "string", description: "Nouveau prénom (ou \"\" pour vider)." },
        lastname: { type: "string", description: "Nouveau nom de famille (ou \"\" pour vider)." },
        email: { type: "string", description: "Nouvelle adresse email (ou \"\" pour vider)." },
        phone: { type: "string", description: "Nouveau téléphone (ou \"\" pour vider)." },
        company: { type: "string", description: "Nouvelle entreprise (ou \"\" pour vider)." },
        jobtitle: { type: "string", description: "Nouveau poste/fonction (ou \"\" pour vider)." },
        mobilephone: { type: "string", description: "Nouveau téléphone portable (ou \"\" pour vider)." },
        address: { type: "string", description: "Nouvelle adresse (ou \"\" pour vider)." },
        city: { type: "string", description: "Nouvelle ville (ou \"\" pour vider)." },
        zip: { type: "string", description: "Nouveau code postal (ou \"\" pour vider)." },
        country: { type: "string", description: "Nouveau pays (ou \"\" pour vider)." },
        website: { type: "string", description: "Nouveau site web (ou \"\" pour vider)." },
        hs_lead_status: { type: "string", description: "Nouveau statut du lead (ou \"\" pour vider)." },
        lifecyclestage: { type: "string", description: "Nouvelle étape du cycle de vie (ou \"\" pour vider)." },
      },
      required: ["contact_id"],
    },
  },
  {
    name: "hubspot_delete_contact",
    description:
      "Supprime un contact du CRM HubSpot. ATTENTION : action irréversible. " +
      "Utilise hubspot_search_contacts d'abord pour trouver le contact_id. " +
      "Demande TOUJOURS confirmation à l'utilisateur avant de supprimer.",
    input_schema: {
      type: "object" as const,
      properties: {
        contact_id: { type: "string", description: "L'ID HubSpot du contact à supprimer (obtenu via hubspot_search_contacts)." },
      },
      required: ["contact_id"],
    },
  },
  {
    name: "hubspot_search_deals",
    description:
      "Recherche des deals (opportunités commerciales) dans le pipeline HubSpot. " +
      "Utilise ce tool quand l'utilisateur demande l'état d'un deal, d'une opportunité, " +
      "ou veut lister les deals en cours.",
    input_schema: {
      type: "object" as const,
      properties: {
        query: {
          type: "string",
          description: "Nom du deal ou de l'entreprise à rechercher. Optionnel pour lister tous les deals.",
        },
        dealstage: {
          type: "string",
          description: "Filtrer par étape du pipeline. Utilise hubspot_get_pipeline d'abord pour connaître les étapes.",
        },
      },
      required: [],
    },
  },
  {
    name: "hubspot_create_deal",
    description:
      "Crée un nouveau deal (opportunité commerciale) dans le pipeline HubSpot. " +
      "Si l'étape du pipeline n'est pas précisée, utilise hubspot_get_pipeline d'abord.",
    input_schema: {
      type: "object" as const,
      properties: {
        dealname: {
          type: "string",
          description: "Nom du deal (ex: 'Contrat Acme Corp Q2 2026').",
        },
        amount: {
          type: "string",
          description: "Montant du deal en euros (ex: '50000').",
        },
        dealstage: {
          type: "string",
          description: "Identifiant de l'étape du pipeline. Utilise hubspot_get_pipeline pour les valeurs valides.",
        },
        pipeline: {
          type: "string",
          description: "Identifiant du pipeline. Par défaut 'default'.",
        },
        closedate: {
          type: "string",
          description: "Date de clôture prévue au format ISO 8601 (ex: '2026-06-30').",
        },
      },
      required: ["dealname"],
    },
  },
  {
    name: "hubspot_update_deal",
    description:
      "Met à jour un deal existant dans HubSpot (changer l'étape, le montant, la date de clôture). " +
      "Utilise hubspot_search_deals d'abord pour trouver le deal_id.",
    input_schema: {
      type: "object" as const,
      properties: {
        deal_id: {
          type: "string",
          description: "L'ID HubSpot du deal (obtenu via hubspot_search_deals).",
        },
        dealname: {
          type: "string",
          description: "Nouveau nom du deal.",
        },
        amount: {
          type: "string",
          description: "Nouveau montant en euros.",
        },
        dealstage: {
          type: "string",
          description: "Nouvelle étape du pipeline.",
        },
        closedate: {
          type: "string",
          description: "Nouvelle date de clôture au format ISO 8601.",
        },
      },
      required: ["deal_id"],
    },
  },
  {
    name: "hubspot_get_pipeline",
    description:
      "Récupère les pipelines de deals et leurs étapes depuis HubSpot. " +
      "Utilise ce tool AVANT hubspot_create_deal ou hubspot_update_deal " +
      "pour connaître les identifiants valides des étapes du pipeline.",
    input_schema: {
      type: "object" as const,
      properties: {},
      required: [],
    },
  },
  {
    name: "hubspot_get_notes",
    description:
      "Récupère les notes associées à un contact dans HubSpot CRM. " +
      "Utilise ce tool quand l'utilisateur demande les notes, commentaires ou historique d'un contact HubSpot. " +
      "Utilise hubspot_search_contacts d'abord pour trouver le contact_id.",
    input_schema: {
      type: "object" as const,
      properties: {
        contact_id: {
          type: "string",
          description: "L'ID HubSpot du contact (obtenu via hubspot_search_contacts).",
        },
        limit: {
          type: "integer",
          description: "Nombre maximum de notes à récupérer. Défaut: 10.",
        },
      },
      required: ["contact_id"],
    },
  },
  {
    name: "hubspot_create_note",
    description:
      "Crée une note sur un contact dans HubSpot CRM. " +
      "Utilise ce tool quand l'utilisateur demande d'ajouter une note, un commentaire ou une observation sur un contact/client HubSpot. " +
      "Utilise hubspot_search_contacts d'abord pour trouver le contact_id.",
    input_schema: {
      type: "object" as const,
      properties: {
        contact_id: {
          type: "string",
          description: "L'ID HubSpot du contact (obtenu via hubspot_search_contacts).",
        },
        body: {
          type: "string",
          description: "Contenu de la note à créer.",
        },
      },
      required: ["contact_id", "body"],
    },
  },
  {
    name: "hubspot_update_note",
    description:
      "Met à jour une note existante dans HubSpot CRM. " +
      "Utilise hubspot_get_notes d'abord pour trouver le note_id.",
    input_schema: {
      type: "object" as const,
      properties: {
        note_id: {
          type: "string",
          description: "L'ID HubSpot de la note (obtenu via hubspot_get_notes).",
        },
        body: {
          type: "string",
          description: "Nouveau contenu de la note.",
        },
      },
      required: ["note_id", "body"],
    },
  },
  // ── Slack Tools ──
  {
    name: "slack_send_message",
    description:
      "Envoie un message dans un canal Slack, avec ou sans pièce jointe. " +
      "Utilise ce tool quand l'utilisateur demande d'envoyer un message sur Slack dans un canal. " +
      "Pour envoyer un fichier (ex: présentation PPTX), ajoute file_path et file_name. " +
      "Exemples: 'envoie Bonjour dans #general sur Slack', 'envoie la présentation sur #social'.",
    input_schema: {
      type: "object" as const,
      properties: {
        channel: {
          type: "string",
          description: "Nom du canal (ex: #general, #marketing) ou ID du canal Slack.",
        },
        message: {
          type: "string",
          description: "Le message à envoyer. Supporte les mentions avec <@USER_ID>.",
        },
        file_path: {
          type: "string",
          description: "Chemin du fichier dans le storage Supabase (retourné par create_presentation). Optionnel.",
        },
        file_name: {
          type: "string",
          description: "Nom du fichier tel qu'il apparaîtra dans Slack (ex: 'Presentation_IA.pptx'). Requis si file_path est fourni.",
        },
      },
      required: ["channel", "message"],
    },
  },
  {
    name: "slack_send_dm",
    description:
      "Envoie un message privé (DM) à un utilisateur Slack, avec ou sans fichier joint. " +
      "Utilise slack_list_users d'abord pour trouver le user_id de la personne. " +
      "Pour envoyer un fichier (ex: présentation), ajoute file_path et file_name. " +
      "Exemples: 'envoie un message privé à Jean sur Slack', 'envoie la présentation en DM à Marie'.",
    input_schema: {
      type: "object" as const,
      properties: {
        user_id: {
          type: "string",
          description: "L'ID Slack de l'utilisateur destinataire (obtenu via slack_list_users).",
        },
        message: {
          type: "string",
          description: "Le message privé à envoyer.",
        },
        file_path: {
          type: "string",
          description: "Chemin du fichier dans le storage Supabase (retourné par create_presentation). Optionnel.",
        },
        file_name: {
          type: "string",
          description: "Nom du fichier tel qu'il apparaîtra dans Slack (ex: 'Presentation_IA.pptx'). Requis si file_path est fourni.",
        },
      },
      required: ["user_id", "message"],
    },
  },
  {
    name: "slack_list_channels",
    description:
      "Liste les canaux Slack accessibles (publics et privés). " +
      "Utilise ce tool pour trouver le nom ou l'ID d'un canal avant d'envoyer un message.",
    input_schema: {
      type: "object" as const,
      properties: {},
      required: [],
    },
  },
  {
    name: "slack_list_users",
    description:
      "Liste les utilisateurs du workspace Slack avec leur nom, email et ID. " +
      "Utilise ce tool pour trouver le user_id d'une personne avant d'envoyer un DM " +
      "ou pour mentionner quelqu'un avec <@USER_ID>.",
    input_schema: {
      type: "object" as const,
      properties: {},
      required: [],
    },
  },
  {
    name: "slack_get_channel_history",
    description:
      "Récupère les messages récents d'un canal Slack. " +
      "Utilise ce tool pour lire l'activité récente, résumer les discussions " +
      "ou retrouver des informations partagées sur un canal.",
    input_schema: {
      type: "object" as const,
      properties: {
        channel: {
          type: "string",
          description: "Nom ou ID du canal Slack.",
        },
        limit: {
          type: "integer",
          description: "Nombre de messages à récupérer. Défaut: 20. Max: 100.",
        },
      },
      required: ["channel"],
    },
  },
  // ── Web Search (Tavily) ──
  {
    name: "web_search",
    description:
      "Effectue une recherche sur internet en temps réel via Tavily Search. " +
      "Utilise ce tool quand l'utilisateur demande des informations actuelles, " +
      "des actualités, des prix, la météo, des résultats sportifs, ou toute information " +
      "non disponible dans la mémoire des transcriptions. " +
      "Exemples: 'cherche les dernières actualités sur...', 'quel temps fait-il à...', " +
      "'combien coûte...', 'qui a gagné le match...'.",
    input_schema: {
      type: "object" as const,
      properties: {
        query: {
          type: "string",
          description: "Requête de recherche en langage naturel (ex: 'actualités IA mars 2026', 'météo Paris demain').",
        },
        search_depth: {
          type: "string",
          enum: ["basic", "advanced"],
          description: "Profondeur: 'basic' (rapide, défaut) ou 'advanced' (plus complet mais plus lent).",
        },
        topic: {
          type: "string",
          enum: ["general", "news"],
          description: "Catégorie: 'general' (défaut) ou 'news' (actualités récentes).",
        },
        max_results: {
          type: "integer",
          description: "Nombre de résultats (1-10). Défaut: 5.",
        },
      },
      required: ["query"],
    },
  },
  {
    name: "datagouv_search",
    description:
      "Recherche des datasets sur data.gouv.fr, la plateforme française de données ouvertes. " +
      "Utilise ce tool pour trouver des jeux de données publics sur n'importe quel sujet " +
      "(prix immobiliers, transport, santé, éducation, environnement, etc.). " +
      "Retourne les titres, descriptions et IDs des datasets trouvés.",
    input_schema: {
      type: "object" as const,
      properties: {
        query: { type: "string", description: "Mots-clés de recherche (ex: 'prix immobilier Paris', 'transport ferroviaire')." },
        page_size: { type: "integer", description: "Nombre de résultats (1-20). Défaut: 5." },
      },
      required: ["query"],
    },
  },
  {
    name: "datagouv_get_dataset",
    description:
      "Obtient le détail complet d'un dataset data.gouv.fr et la liste de ses ressources (fichiers). " +
      "Utilise ce tool APRÈS datagouv_search pour voir les fichiers disponibles dans un dataset. " +
      "Retourne le titre, la description, l'organisation et les ressources avec leurs IDs.",
    input_schema: {
      type: "object" as const,
      properties: {
        dataset_id: { type: "string", description: "L'ID du dataset (obtenu via datagouv_search)." },
      },
      required: ["dataset_id"],
    },
  },
  {
    name: "datagouv_query_data",
    description:
      "Interroge les données d'une ressource CSV ou XLSX sur data.gouv.fr via l'API Tabular. " +
      "Utilise ce tool APRÈS datagouv_get_dataset pour lire les lignes de données d'un fichier. " +
      "Supporte le filtrage par colonne et le tri.",
    input_schema: {
      type: "object" as const,
      properties: {
        question: { type: "string", description: "Description de ce que tu cherches dans les données (aide le MCP à formater la réponse)." },
        resource_id: { type: "string", description: "L'ID de la ressource (obtenu via datagouv_get_dataset)." },
        page_size: { type: "integer", description: "Nombre de lignes à récupérer (1-200). Défaut: 20." },
        filter_column: { type: "string", description: "Colonne sur laquelle filtrer. Optionnel." },
        filter_value: { type: "string", description: "Valeur du filtre. Optionnel." },
        filter_operator: { type: "string", enum: ["exact", "contains", "less", "greater"], description: "Opérateur de filtre. Défaut: 'exact'." },
        sort_column: { type: "string", description: "Colonne de tri. Optionnel." },
        sort_direction: { type: "string", enum: ["asc", "desc"], description: "Direction du tri. Défaut: 'asc'." },
      },
      required: ["question", "resource_id"],
    },
  },
  {
    name: "datagouv_get_resource_info",
    description:
      "Obtient les métadonnées détaillées d'une ressource data.gouv.fr (colonnes disponibles, type de fichier, taille, date de mise à jour). " +
      "Utilise ce tool APRÈS datagouv_get_dataset pour connaître les colonnes d'un fichier CSV avant de l'interroger avec datagouv_query_data.",
    input_schema: {
      type: "object" as const,
      properties: {
        resource_id: { type: "string", description: "L'ID de la ressource (obtenu via datagouv_get_dataset)." },
      },
      required: ["resource_id"],
    },
  },
  {
    name: "datagouv_get_metrics",
    description:
      "Obtient les statistiques globales de la plateforme data.gouv.fr (nombre total de datasets, ressources, réutilisations, etc.).",
    input_schema: {
      type: "object" as const,
      properties: {},
      required: [],
    },
  },
  {
    name: "datagouv_search_dataservices",
    description:
      "Recherche des APIs (dataservices) publiques sur data.gouv.fr. " +
      "Utilise ce tool pour trouver des APIs de données en temps réel (météo, transport, géographie, etc.).",
    input_schema: {
      type: "object" as const,
      properties: {
        query: { type: "string", description: "Mots-clés de recherche (ex: 'météo', 'transport', 'adresse')." },
        page_size: { type: "integer", description: "Nombre de résultats (1-20). Défaut: 5." },
      },
      required: ["query"],
    },
  },
  // ─── Presentation tools ──────────────────────────────────
  {
    name: "create_presentation",
    description:
      "Crée une présentation PowerPoint (PPTX) ET sa version PDF automatiquement. " +
      "Utilise ce tool quand l'utilisateur demande de créer une présentation, un PowerPoint, des slides, ou un PDF de présentation. " +
      "Le fichier PPTX et le PDF sont générés et stockés. Le retour contient file_path (PPTX) et pdf_file_path (PDF). " +
      "Pour l'envoyer par email, appelle ensuite send_email_with_attachment avec le file_path retourné (PPTX ou pdf_file_path pour le PDF). " +
      "Exemples: 'crée une présentation sur le budget Q2', 'fais un PowerPoint résumant la réunion', 'envoie-moi un PDF sur l'IA'.",
    input_schema: {
      type: "object" as const,
      properties: {
        title: {
          type: "string",
          description: "Titre de la présentation (affiché sur la première slide).",
        },
        slides: {
          type: "array",
          description: "Liste des slides. Utilise des layouts variés pour un rendu professionnel.",
          items: {
            type: "object",
            properties: {
              title: { type: "string", description: "Titre de la slide." },
              layout: {
                type: "string",
                enum: [
                  "title", "section", "content", "two_column",
                  "image_right", "image_left", "image_full",
                  "key_metrics", "quote", "table", "timeline",
                ],
                description:
                  "Type de mise en page. title=couverture, section=séparateur, content=bullets/texte, " +
                  "two_column=2 colonnes, image_right/left=image+texte, image_full=image plein écran, " +
                  "key_metrics=chiffres clés, quote=citation, table=tableau, timeline=étapes.",
              },
              content: {
                type: "string",
                description: "Paragraphe de texte ou sous-titre.",
              },
              bullets: {
                type: "array",
                items: { type: "string" },
                description: "Liste de points détaillés (2-3 phrases chacun, pas juste des mots-clés).",
              },
              image_url: {
                type: "string",
                description: "URL d'une image (Unsplash, Pexels, etc.). Utilise pour image_right, image_left, image_full.",
              },
              image_caption: {
                type: "string",
                description: "Légende sous l'image.",
              },
              columns: {
                type: "array",
                description: "Pour layout two_column : 2 colonnes [{title, bullets}, {title, bullets}].",
                items: {
                  type: "object",
                  properties: {
                    title: { type: "string" },
                    bullets: { type: "array", items: { type: "string" } },
                  },
                },
              },
              key_metrics: {
                type: "array",
                description: "Pour layout key_metrics : 3-4 chiffres clés [{value, label}].",
                items: {
                  type: "object",
                  properties: {
                    value: { type: "string", description: "Chiffre ou valeur (ex: '85%', '2.4M', '+34%')." },
                    label: { type: "string", description: "Description du chiffre." },
                  },
                },
              },
              quote: {
                type: "string",
                description: "Pour layout quote : texte de la citation.",
              },
              quote_author: {
                type: "string",
                description: "Pour layout quote : auteur de la citation.",
              },
              table_data: {
                type: "object",
                description: "Pour layout table : {headers: [...], rows: [[...]]}.",
                properties: {
                  headers: { type: "array", items: { type: "string" } },
                  rows: { type: "array", items: { type: "array", items: { type: "string" } } },
                },
              },
            },
            required: ["title"],
          },
        },
        theme: {
          type: "string",
          enum: ["professional", "minimal", "corporate", "modern", "creative"],
          description: "Thème visuel. professional=bleu corporate, minimal=épuré, corporate=formel, modern=sombre, creative=violet/rose.",
        },
      },
      required: ["title", "slides"],
    },
  },
  {
    name: "create_report",
    description:
      "Crée un rapport/document PDF professionnel via HTML+CSS (rendu Gotenberg/Chromium). " +
      "Utilise ce tool quand l'utilisateur demande un rapport, document, compte-rendu, " +
      "analyse, mémo, brief technique, convention, ou PDF structuré (PAS une présentation/slides). " +
      "Génère le HTML complet dans html_content pour un rendu parfait. " +
      "Pour l'envoyer par email, appelle ensuite send_email_with_attachment.",
    input_schema: {
      type: "object" as const,
      properties: {
        title: {
          type: "string",
          description: "Titre du rapport.",
        },
        subtitle: {
          type: "string",
          description: "Sous-titre optionnel (ex: département, auteur, période).",
        },
        theme: {
          type: "string",
          enum: ["professional", "minimal", "corporate", "modern", "creative"],
          description: "Thème de couleurs. Ignoré si custom_color est fourni.",
        },
        template: {
          type: "string",
          enum: ["executive", "modern", "creative"],
          description:
            "Style de mise en page. 'executive' = couverture claire, numérotation auto, professionnel (par défaut). " +
            "'modern' = couverture sombre, style classique. 'creative' = couverture colorée.",
        },
        document_type: {
          type: "string",
          enum: [
            "rapport_intervention",
            "brief_technique",
            "recap_brief",
            "convention_publicitaire",
            "analyse",
            "compte_rendu",
            "custom",
          ],
          description:
            "Type de document structuré. Chaque type a des métadonnées et un footer par défaut. " +
            "'custom' = document libre (par défaut).",
        },
        custom_color: {
          type: "string",
          description:
            "Couleur principale pour le thème (hex #RRGGBB ou nom: rouge, bleu, vert, orange, violet, " +
            "rose, noir, turquoise, bordeaux, marine, corail, indigo, emeraude). " +
            "Si fourni, un thème complet est généré à partir de cette couleur.",
        },
        metadata: {
          type: "array",
          items: {
            type: "object",
            properties: {
              key: { type: "string" },
              value: { type: "string" },
            },
            required: ["key", "value"],
          },
          description:
            "Métadonnées affichées en tableau sur la page de couverture " +
            "(ex: Destinataires, Date, Statut, Durée estimée, Client, Intervenant).",
        },
        footer_text: {
          type: "string",
          description: "Texte de pied de page personnalisé (ex: 'Document confidentiel — Ne pas diffuser').",
        },
        include_logo: {
          type: "boolean",
          description:
            "Inclure le logo de l'utilisateur (true par défaut). " +
            "Mettre false uniquement si l'utilisateur demande explicitement pas de logo.",
        },
        reference: {
          type: "string",
          description:
            "Référence du document affichée sur la couverture (ex: 'RI-2026-0323-SERDOUN', 'BT-2026-0325-PROJET'). " +
            "Génère un code pertinent basé sur le type de document, la date et le contexte.",
        },
        html_content: {
          type: "string",
          description:
            "HTML complet du rapport (<html><head><style>...</style></head><body>...</body></html>). " +
            "Si fourni, le PDF est généré via Gotenberg (Chromium) pour un rendu parfait. " +
            "Utilise CSS inline, @page { size: A4; margin: 0; }, police Segoe UI/Arial. " +
            "Pour le logo utilisateur, utilise <img src=\"{{LOGO_URL}}\" /> — le proxy remplacera automatiquement. " +
            "Si html_content est fourni, sections[] n'est pas nécessaire.",
        },
        sections: {
          type: "array",
          description:
            "Contenu structuré du rapport. Utilise des types variés pour un rendu professionnel.",
          items: {
            type: "object",
            properties: {
              type: {
                type: "string",
                enum: [
                  "heading",
                  "paragraph",
                  "bullets",
                  "numbered_list",
                  "table",
                  "key_metrics",
                  "quote",
                  "page_break",
                  "info_box",
                  "alert_box",
                  "metadata_table",
                  "separator",
                ],
                description:
                  "Type de section. info_box = encadré coloré avec titre et contenu. " +
                  "alert_box = boîte d'alerte avec icône. metadata_table = tableau clé-valeur. " +
                  "separator = ligne décorative.",
              },
              level: {
                type: "integer",
                enum: [1, 2, 3],
                description: "Niveau de titre (pour heading). 1=principal, 2=sous-section, 3=sous-sous-section.",
              },
              text: {
                type: "string",
                description: "Texte (pour heading, paragraph, quote, info_box, alert_box).",
              },
              items: {
                type: "array",
                items: { type: "string" },
                description: "Éléments de liste (pour bullets, numbered_list, info_box).",
              },
              headers: {
                type: "array",
                items: { type: "string" },
                description: "En-têtes de colonnes (pour table).",
              },
              rows: {
                type: "array",
                items: {
                  type: "array",
                  items: { type: "string" },
                },
                description: "Lignes de données (pour table).",
              },
              metrics: {
                type: "array",
                items: {
                  type: "object",
                  properties: {
                    label: { type: "string" },
                    value: { type: "string" },
                  },
                  required: ["label", "value"],
                },
                description: "Métriques clés avec label et valeur (pour key_metrics, max 4).",
              },
              author: {
                type: "string",
                description: "Auteur de la citation (pour quote).",
              },
              box_type: {
                type: "string",
                enum: ["warning", "info", "tip", "security", "hardware", "forbidden"],
                description:
                  "Type d'alerte (pour alert_box). warning=⚠ attention, info=ℹ information, " +
                  "tip=✶ conseil, security=▲ sécurité/RGPD, hardware=■ matériel, forbidden=⊘ interdit.",
              },
              box_title: {
                type: "string",
                description: "Titre optionnel de la boîte (pour info_box, alert_box).",
              },
              metadata: {
                type: "array",
                items: {
                  type: "object",
                  properties: {
                    key: { type: "string" },
                    value: { type: "string" },
                  },
                  required: ["key", "value"],
                },
                description: "Paires clé-valeur (pour metadata_table).",
              },
            },
            required: ["type"],
          },
        },
      },
      required: ["title"],
    },
  },
  {
    name: "send_email_with_attachment",
    description:
      "Envoie un email avec un fichier en pièce jointe (ex: présentation PPTX ou rapport PDF). " +
      "Utilise ce tool APRÈS create_presentation ou create_report quand l'utilisateur demande d'envoyer le fichier par email. " +
      "Nécessite le file_path et file_name retournés par create_presentation ou create_report.",
    input_schema: {
      type: "object" as const,
      properties: {
        to: {
          type: "string",
          description: "Adresse email du destinataire.",
        },
        subject: {
          type: "string",
          description: "Sujet de l'email.",
        },
        body: {
          type: "string",
          description: "Contenu texte de l'email accompagnant la pièce jointe.",
        },
        file_path: {
          type: "string",
          description:
            "Chemin du fichier dans le storage (retourné par create_presentation).",
        },
        file_name: {
          type: "string",
          description:
            "Nom du fichier tel qu'il apparaîtra dans l'email (ex: 'Presentation_Budget_Q2.pptx').",
        },
      },
      required: ["to", "subject", "body", "file_path", "file_name"],
    },
  },
];
