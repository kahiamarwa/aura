# ReSpeaker XVF3800 USB — Manuel de référence (projet Aura)

> Référence complète et **vérifiée sur sources** de l'array micro XMOS XVF3800 en variante
> USB, pour l'enceinte Aura (Raspberry Pi 4 64-bit, assistant vocal malvoyants).
> Ce document est la **source de vérité** ; le runbook opérationnel est
> [`respeaker-xvf3800.md`](./respeaker-xvf3800.md).
>
> **⚠️ RÈGLE D'OR — à lire avant toute manipulation :**
> **Sur un firmware < 2.0.10, `xvf_host` est utilisé en LECTURE SEULE.**
> **Aucune écriture, et JAMAIS `save_configuration` (quelle que soit la version).**
> Les écritures de tuning ne sont autorisées qu'**après** une mise à jour firmware
> validée (§3) et **uniquement** sur une unité de test — voir §4 (bugs #8, #18, #20).

Chaque fait ci-dessous provient de la campagne de recherche (wiki Seeed, dépôt GitHub
`respeaker/reSpeaker_XVF3800_USB_4MIC_ARRAY` incl. source `xvf_host.py`, doc officielle
XMOS XM-014888-PC, retours terrain / issues GitHub). Les contradictions entre sources
sont explicitement signalées « **⚑ Divergence** ». Les valeurs de commande (resid/cmdid)
ont été recoupées avec `python_control/xvf_host.py`.

---

## Sommaire

1. [Identité, puce & modes firmware](#1-identité-puce--modes-firmware)
2. [Table des commandes `xvf_host`](#2-table-des-commandes-xvf_host)
3. [Firmware & procédure DFU](#3-firmware--procédure-dfu)
4. [⚠️ Bugs connus & contournements](#4--bugs-connus--contournements-critique)
5. [Réglages recommandés Aura](#5-réglages-recommandés-aura)
6. [Opportunités d'intégration](#6-opportunités-dintégration)
7. [Dépannage — arbre de décision](#7-dépannage--arbre-de-décision)
8. [Sources](#8-sources)

---

## 1. Identité, puce & modes firmware

### 1.1 Identité matérielle

| Élément | Valeur | Note |
|---|---|---|
| VID:PID USB | **`0x2886:0x001A`** (10374:26) | Identique en mode audio **et** en mode DFU |
| Interface de contrôle | **Interface vendor n°3** | `xvf_host` la revendique ; coexiste avec les interfaces audio UAC → on peut lire DoA/registres **pendant** le streaming |
| Variante appairée (ROS2) | idProduct `0018` aussi vue | Certains tutoriels ajoutent ce PID dans udev |
| Puce DSP | XMOS **XU316-1024-QF60B** | Rails 0,9 V core / 1,8 V / 3,3 V IO |
| Codec audio | TI **TLV320AIC3104** (I2C `0x18`) | ⚠️ un reflash DFU ne réinitialise PAS sa config (cf. bug #18) |
| Géométrie array | Carré **66 mm** de côté (±0,033 m), 4 micros | `AEC_MIC_ARRAY_TYPE=2` « squarecular », `AEC_NUM_MICS=4`, `AEC_NUM_FARENDS=1` |
| Sorties HP | Jack 3,5 mm AUX + JST « 5 W amplified » | `X0D31` = enable ampli (actif BAS) |
| Alimentation | USB Type-C unique (data + alim), USB Audio Class 2.0 | Consommation board-level **non publiée** (voir §4.7) |
| Anneau LED | **12× WS2812** adressables | Alim pilotée par GPO `X0D33` (actif haut) |

### 1.2 Chaîne de traitement (pipeline)

Ordre exact interne :

```
4 micros PDM
   └─▶ AEC (1 filtre adaptatif par micro, queue 192 ms)
        └─▶ Beamformer (2 beams focalisés + 1 beam libre scannant 360° + auto-select)
             └─▶ Post-processeur : déréverbération, suppression écho résiduel,
                 suppression bruit stationnaire + non-stationnaire, égalisation,
                 AGC, limiteur
```

- Traitement interne **16 kHz** ; interfaces I2S/USB à 16 **ou** 48 kHz ; bande utile **80 Hz–8 kHz**.
- Specs datasheet : distance **0,3–5 m**, suppression de bruit **jusqu'à 25 dB**, appariement micros **±2 dB**, latence de sortie **~50 ms** typique (entrée min ~58 ms). AGC annoncée **60 dB**.
- **Référence AEC** : doit être **MONO sur le canal gauche (ch0)** du flux de lecture USB ; le DAC rejoue ce canal gauche sur les deux sorties HP. → **la lecture TTS doit passer par l'array** (montage Aura correct).

### 1.3 Les deux sorties du firmware : « Conference » vs « ASR »

| Sortie | Où prélevée | Traitements appliqués | Canal ReSpeaker par défaut | Mux (cat, src) |
|---|---|---|---|---|
| **Conference** | Sortie **complète** du pipeline | AEC + beamforming + **post-proc** (déréverb, écho résiduel, NS, AGC, limiteur) | **ch0 / gauche** | `[8, 0]` (copie du beam auto-select post-processé) |
| **ASR** | **Après le beamformer**, **hors** post-processeur | AEC linéaire + beamforming **seulement** : **aucune NS, aucun AGC, aucune suppression d'écho non-linéaire** | **ch1 / droite** | `[7, 3]` (ASR du beam auto-select, avec `AEC_ASROUTONOFF=1`) |

> **Aura consomme ch1 (ASR)** via `AUDIO_INPUT_CHANNEL=1`. C'est le bon choix : le signal
> n'est pas déformé par l'AGC/NS, ce qui préserve openWakeWord et l'empreinte ECAPA.
> Retour terrain (Home-Assistant) : le ch1 (AEC linéaire plus légère) donne de **meilleurs
> résultats de wake word pendant la musique** que le ch0 Conference.

**⚑ Divergence doc :** la table XMOS générique dit « default right channel = silence `[0,0]` »,
mais le firmware ReSpeaker réel route ch1 = ASR `[7,3]` (confirmé wiki + lecture sur device
fw 2.0.7). C'est le comportement Seeed qui fait foi.

### 1.4 Modes firmware & mapping des canaux

| Firmware | Format natif | Mapping canaux | DFU |
|---|---|---|---|
| **USB 2 canaux** (`..._usb_dfu_firmware_v2.0.x.bin`) — **notre unité** | 16 kHz, 32-bit, 2 ch | **ch0 = Conference**, **ch1 = ASR** | USB DFU |
| **USB 6 canaux** (`..._usb_dfu_firmware_6chl_v2.0.8.bin`) | 16 kHz, 32-bit, 6 ch | ch0 Conference, ch1 ASR, **ch2–ch5 = micros bruts 0–3** | USB DFU |
| **USB 48 kHz** (`..._usb_dfu_firmware_v2.0.9_48k.bin`, build `ua-io48-sqr`) | **48 kHz** stéréo S16_LE (capture ET lecture) | ch0 Conference, ch1 ASR | USB DFU |
| **I2S** (`..._i2s_dfu_firmware_v1.0.x.bin`) | 32-bit, 2 ch | ch0 Conference, ch1 ASR — **PAS détecté en USB** | I2C DFU |
| **I2S master / Home-Assistant** (`..._i2s_master_dfu_firmware_v1.0.x_48k.bin`) | 48 kHz | **ch0 = ASR, ch1 = Wake word** | I2C DFU |

Notes :
- Le `S16_LE` que voit ALSA sur le Pi vient de la **conversion `plughw`** (le natif USB est 32-bit). Exemple wiki : `arecord -D plughw:X,0 -c 2 -r 16000 -f S16_LE -d 5 out.wav`.
- Le build est lisible : `BLD_MSG` → `ua-io16-sqr` (USB Audio, I/O 16 kHz, array carré) vs `ua-io48-sqr` (48 kHz).
- La seule variante **6 canaux publiée est v2.0.8** (le firmware 2ch va jusqu'à v2.0.10) — flasher le 6ch = renoncer aux corrections postérieures.
- **openWakeWord attend du 16 kHz mono** → si on adopte le 48k, il faut resampler côté Pi.

---

## 2. Table des commandes `xvf_host`

### 2.1 Outils hôtes

| Outil | Emplacement | Transport | Notes |
|---|---|---|---|
| **Binaire C `xvf_host`** | `host_control/rpi_64bit/` (natif Pi 4 64-bit) | `--use usb\|i2c\|spi` | `chmod +x` + `sudo` (ou règle udev). I2C @ `0x2C`. Supporte **toutes** les commandes XMOS de l'appendice XM-014888-PC **sauf les GPIO XMOS** (remplacées par les GPIO Seeed). Utilitaires : `--list-commands`, `--dump-params`, `--execute-command-list fichier.txt`. |
| **Script Python `xvf_host.py`** | `python_control/` | **USB uniquement** | `python xvf_host.py COMMANDE [--values v1 v2 …]` ; `--list`. Valeurs en décimal / `0xFF` / `$FF`, casse insensible. **Dépendances réelles : `pip install pyusb libusb-package`** (le readme ne cite que pyusb → `ImportError` sinon). Timeout USB 100 s. |
| **`xvf_i2c_dfu`** | `host_control/rpi_64bit/` | I2C | DFU par I2C sur le Pi (fourni compilé, sans source). |
| **Application Console** | `github.com/respeaker/respeaker-console/releases` | USB | GUI Win/mac/Linux : onglets Audio, Monitor (DoA/VAD live), LEDs, Parameters (plages recommandées), MAJ firmware. Linux nécessite dfu-util + règle udev ; Windows nécessite WinUSB via Zadig. |

### 2.2 Protocole de contrôle (bas niveau)

- **Écriture** : `bmRequestType=0x40`, `bRequest=0`, `wValue=cmdid`, `wIndex=resid`, payload **little-endian** (float/int32/uint32 = 4 o ; uint16 = 2 o ; uint8/char = 1 o).
- **Lecture** : `bmRequestType=0xC0`, `bRequest=0`, `wValue=0x80|cmdid`, `wIndex=resid`, `wLength=N×taille+1`.
- **1er octet de la réponse = statut** : `0` = `CONTROL_SUCCESS`, `64` = `SERVICER_COMMAND_RETRY` → re-tenter (~10 ms, jusqu'à **100 fois** ; `xvf_host.py` boucle). Un seul ordre de contrôle traité à la fois.
- I2C : adresse esclave **`0x2C`** (7-bit), même schéma resid/cmdid.

**⚑ Gap :** le format bas-niveau des vendor requests n'est PAS dans le user guide XMOS
public — il est reconstitué depuis `xvf_host.py`. À valider avec `pyusb` avant d'écrire un
client maison.

### 2.3 Resource IDs (`wIndex`) — firmware ReSpeaker

| resid | Servicer |
|---|---|
| **17** | Post-processing (`PP_*`) |
| **20** | GPO / LED / DoA **Seeed** |
| **33** | AEC / beamforming (`AEC_*`) |
| **35** | Audio manager (`AUDIO_MGR_*`) |
| **36** | GPI (lecture entrées, `GPI_VALUE_ALL`) — **contexte wiki/XIAO** ⚑ |
| **48** | Application (VERSION, config, DFU flags) |
| **0xF0** (240) | Servicer DFU |

---

### 2.4 Identité, firmware & configuration (resid 48)

| Commande | resid, cmd | Type | R/W | Plage / défaut | Pertinence Aura |
|---|---|---|---|---|---|
| `VERSION` | 48, 0 | 3×uint8 | **RO** | ex. `2 0 7` | **Healthcheck au boot** : vérifier fw attendu (≥ 2.0.10 visé) |
| `BLD_MSG` | 48, 1 | 50×char | **RO** | `ua-io16-sqr` / `ua-io48-sqr` | Détecter à distance 2ch vs 6ch vs 48k (télémétrie flotte) |
| `BLD_HOST` | 48, 2 | 30×char | RO | — | Inutile |
| `BLD_REPO_HASH` | 48, 3 | 40×char | RO | hash git | Traçabilité, peu utile |
| `BLD_MODIFIED` | 48, 4 | 6×char | RO | `TRUE` (fork Seeed) | Inutile |
| `BOOT_STATUS` | 48, 5 | 3×char | RO | boot SPI vs JTAG/FLASH | Diagnostic rare |
| `TEST_CORE_BURN` | 48, 6 | uint8 | RW | reboote + reset | **NE JAMAIS utiliser** (consommation ↑↑) |
| `REBOOT` | 48, 7 | uint8 | **WO** | valeur quelconque | **CRITIQUE** : re-calibre l'AEC, workaround bug #20 (§4.1) — à lancer à chaque boot |
| `USB_BIT_DEPTH` | 48, 8 | 2×uint8 (IN,OUT) | RW | 16/24/32 ; **l'écriture REBOOTE + reset tous les paramètres** | Workaround bug bInterval (§4.1) ; sinon ne pas toucher en prod |
| `SAVE_CONFIGURATION` | 48, 9 | uint8 | **WO** | — | **🚫 INTERDIT** — brique le device (bug #8, §4.2) |
| `CLEAR_CONFIGURATION` | 48, 10 | uint8 | **WO** | reboot requis après | Retour usine soft (n'a PAS récupéré le device de #18) |

### 2.5 AEC / beamforming (resid 33)

| Commande | resid, cmd | Type | R/W | Plage / défaut (XMOS → Seeed) | Pertinence Aura |
|---|---|---|---|---|---|
| `AEC_AECPATHCHANGE` | 33, 0 | int32 0/1 | **RO** | — | Détecter que l'enceinte a été déplacée |
| `AEC_HPFONOFF` | 33, 1 | int32 | RW | 0=off,1=70,2=125,3=150,4=180 Hz ; **déf 2** | Couper le rumble (ventilation) avant wake word |
| `AEC_AECSILENCELEVEL` | 33, 2 | 2×float | RW | déf 1e-9 (≈ −80 dBFS) | Tuning AEC fin, rare |
| `AEC_AECCONVERGED` | 33, 3 | int32 0/1 | **RO** | converge < 30 s de lecture | **Sonde santé AEC** (0 persistant pendant TTS = cassé, §4.1) |
| `AEC_AECEMPHASISONOFF` | 33, 4 | int32 | RW | 0/1/2 ; **déf 1** | Tester `on_eq` (2) si TTS égalisé |
| `AEC_FAR_EXTGAIN` | 33, 5 | float dB | RW | **déf 0.0** | Déclarer tout gain de volume TTS externe côté Pi (sinon AEC déréglée) |
| `AEC_PCD_COUPLINGI` | 33, 6 | float | RW | [0..1], hors plage = PCD off | Tuning fin |
| `AEC_PCD_MINTHR` | 33, 7 | float | RW | [0..0.02] déf 0.005 | Tuning fin |
| `AEC_PCD_MAXTHR` | 33, 8 | float | RW | [0.025..0.2] déf 0.1 | Tuning fin |
| `AEC_RT60` | 33, 9 | float s | **RO** | sain **0.25–0.9** ; `1.4e-45` = cassé | **Meilleur marqueur single-shot de l'état warm-reboot cassé** (§4.1) |
| `AEC_ASROUTONOFF` | 33, 35 | int32 0/1 | RW | 0=résidus AEC/mic, **1=ASR/beam** | Doit rester **1** : notre ch1 STT en dépend |
| `AEC_ASROUTGAIN` | 33, 36 | float | RW | [0..1000] **déf 1.0** | **Levier n°1** si openWakeWord/Deepgram trop faible/fort (sans toucher au Conference) |
| `AEC_FIXEDBEAMSONOFF` | 33, 37 | int32 0/1 | RW | déf off (fw ≥ **2.0.9**) | Figer l'écoute vers une position connue (lit patient) |
| `AEC_FIXEDBEAMNOISETHR` | 33, 38 | 2×float | RW | [0..1] déf 0.4,0.4 | Tuning beams fixes |
| `SHF_BYPASS` | 33, 70 | uint8 0/1 | RW | — | **Debug uniquement** : brut vs traité (JAMAIS en prod) |
| `AEC_NUM_MICS` | 33, 71 | int32 | RO | 4 | Sanity check |
| `AEC_NUM_FARENDS` | 33, 72 | int32 | RO | 1 | Sanity check |
| `AEC_MIC_ARRAY_TYPE` | 33, 73 | int32 | RO | 2 (carré) | Info |
| `AEC_MIC_ARRAY_GEO` | 33, 74 | 12×float | RO | carré ±0.033 m | Base pour interpréter le DoA |
| `AEC_AZIMUTH_VALUES` | 33, 75 | 4×float rad | **RO** | beam1, beam2, libre, **auto-select** | **DoA fin** (radians) ; croiser avec ECAPA (position ≈ identité) |
| `TEST_AEC_DISABLE_CONTROL` | 33, 76 | uint32 | WO | irréversible sans reboot | **NE PAS utiliser** |
| `AEC_SPENERGY_VALUES` | 33, 80 | 4×float | **RO** | >0 = parole (par beam) | **VAD matériel gratuit** : gate avant openWakeWord/Deepgram |
| `AEC_FIXEDBEAMSAZIMUTH_VALUES` | 33, 81 | 2×float rad | RW | déf 0,0 | Azimuts des 2 beams fixes |
| `AEC_FIXEDBEAMSELEVATION_VALUES` | 33, 82 | 2×float rad | RW | déf 0,0 | Élévations beams fixes |
| `AEC_FIXEDBEAMSGATING` | 33, 83 | uint8 0/1 | RW | déf 0 | Silence les beams sans parole |
| `SPECIAL_CMD_AEC_*` (90-94) | 33, 90-94 | — | mixte | filtres AEC | **Debug AEC avancé** seulement |

### 2.6 Audio manager & routage (resid 35)

| Commande | resid, cmd | Type | R/W | Plage / défaut Seeed | Pertinence Aura |
|---|---|---|---|---|---|
| `AUDIO_MGR_MIC_GAIN` | 35, 0 | float | RW | **déf 90** (XMOS 10) | Gain d'entrée global — impacte AEC **et** niveaux ECAPA, prudence |
| `AUDIO_MGR_REF_GAIN` | 35, 1 | float | RW | **déf 8.0** (XMOS 1.5) | Équilibre référence AEC vs volume TTS |
| `AUDIO_MGR_SELECTED_AZIMUTHS` | 35, 11 | 2×float rad | **RO** | [0]=DoA processed (NAN si pas de parole), [1]=DoA auto-select | DoA « propre » avec indicateur d'absence de parole |
| `AUDIO_MGR_SELECTED_CHANNELS` | 35, 12 | 2×uint8 | RW | — | Change ce que copie le canal Conference |
| `AUDIO_MGR_OP_L` | 35, 15 | 2×uint8 (cat,src) | RW | **déf `[8,0]`** Conference | **Contrôle total ch0** (cœur du routage) |
| `AUDIO_MGR_OP_R` | 35, 19 | 2×uint8 (cat,src) | RW | **déf `[7,3]`** ASR | **Contrôle total ch1** (consommé par le STT) |
| `AUDIO_MGR_OP_ALL` | 35, 23 | 12×uint8 | RW | 6 paires (3 L + 3 R) | Configurer tout le mux en un appel (provisioning) |
| `AUDIO_MGR_SYS_DELAY` | 35, 26 | int32 | RW | [-64..256] éch. ; **déf 12** (XMOS −32) | **Param n°1** si l'AEC laisse passer l'écho TTS (HP sur jack). Causalité : micro doit arriver **après** la référence, écart ≤40 éch. (2,5 ms) |
| `AUDIO_MGR_OP_*_PKx`, `OP_PACKED`, `OP_UPSAMPLE`, `I2S_*`, idle-times… | 35, div. | — | mixte | — | Avancé / inutile en USB standard |

**Table du mux `AUDIO_MGR_OP_L/R` — (catégorie, source)** (Table 26 XMOS, plage catégorie [0..12], source [0..5]) :

| Cat | Signification | Sources |
|---|---|---|
| 0 | Silence | 0 |
| 1 | Micros bruts pré-ampli, sans délai | 0-3 |
| 2 | Micros dépackés (entrée packée only) | 0-3 |
| 3 | Micros amplifiés + délai système (= entrée du SHF) | 0-3 |
| 4 | Référence far-end post-conversion 16 kHz | 0 |
| 5 | Référence + délai système | 0 |
| 6 | Beams post-processés (0,1 = lents, 2 = rapide, **3 = auto-select recommandé**) | 0-3 |
| 7 | Résidus AEC / **sortie ASR** par beam | 0-3 |
| 8 | Canaux « user chosen » copiant l'auto-select | 0-1 |
| 9 | Canaux DSP utilisateur post-SHF | 0-3 |
| 10 | Far-end au taux natif | 0-5 |
| 11 | Micros amplifiés **sans** délai | 0-3 |
| 12 | Far-end amplifié + délai (= référence envoyée au SHF) | 0 |

> **Debug AEC or :** `AUDIO_MGR_OP_R 5 0` (ou `4 0` / `12 0`) route la **référence far-end**
> sur le canal droit → on enregistre ce que l'annuleur « voit » et on vérifie qu'elle existe
> pendant le TTS. `AUDIO_MGR_OP_L 3 0` = micro 0 amplifié à gauche. **Toujours ré-appliquer
> `[8,0]`/`[7,3]` après.**

### 2.7 Post-processing (AGC / NS / écho / limiteur) — resid 17

| Commande | resid, cmd | Type | R/W | Plage / défaut Seeed | Pertinence Aura |
|---|---|---|---|---|---|
| `PP_AGCONOFF` | 17, 10 | int32 0/1 | RW | déf on | Tester OFF (gain fixe) si niveaux STT/ECAPA instables |
| `PP_AGCMAXGAIN` | 17, 11 | float | RW | [1..1000] **déf 64** (XMOS 125) | Plafonner la remontée de bruit à distance |
| `PP_AGCDESIREDLEVEL` | 17, 12 | float | RW | [1e-8..1.0] déf 0.0045 (≈ −23,5 dBov) | Caler le niveau moyen envoyé à Deepgram |
| `PP_AGCGAIN` | 17, 13 | float | RW | [1..1000] **déf 2.0** (XMOS 32) | Point de départ du gain |
| `PP_AGCTIME` | 17, 14 | float s | RW | [0.5..4.0] déf 0.9 | Réactivité AGC |
| `PP_AGCFASTTIME` | 17, 15 | float s | RW | [0.05..4.0] déf 0.1 | Anti-saturation voix forte proche |
| `PP_AGCALPHA*` | 17, 16-18 | float | RW | — | Tuning fin |
| `PP_LIMITONOFF` | 17, 19 | int32 0/1 | RW | déf on | Anti-écrêtage ch0 Conference (laisser on) |
| `PP_LIMITPLIMIT` | 17, 20 | float | RW | [1e-8..1.0] déf 0.47 | Avec le limiteur |
| `PP_MIN_NS` | 17, 21 | float | RW | [0..1] déf **0.15** (≈16 dB max) | Bruit stationnaire (ch0 only) — baisser si ventilation |
| `PP_MIN_NN` | 17, 22 | float | RW | [0..1] déf **0.51** (≈6 dB) | Bruit non-stationnaire ; <0.5 = agressif, risque de distordre la voix |
| `PP_ECHOONOFF` | 17, 23 | int32 0/1 | RW | déf on | Complément AEC pendant TTS (lié à « Stop Aura ») |
| `PP_GAMMA_E` | 17, 24 | float | RW | [0..2] déf 1.0 (typ. 1.0–1.4) | Monter si le wake se re-déclenche sur notre propre TTS |
| `PP_GAMMA_ETAIL` | 17, 25 | float | RW | [0..2] déf 1.0 | Pièces réverbérantes |
| `PP_GAMMA_ENL` | 17, 26 | float | RW | [0..5] déf 1.1 | HP qui distord à fort volume |
| `PP_NLATTENONOFF` | 17, 27 | int32 0/1 | RW | déf 1 | Avec `PP_GAMMA_ENL` |
| `PP_NLAEC_MODE` | 17, 28 | int32 | RW | 0=normal,1=train,2=train2 | **Calibration only** (env. quasi-anéchoïque RT60 < 0.3 s) |
| `PP_MGSCALE` | 17, 29 | 3×float | RW | — | Tuning fin |
| `PP_FMIN_SPEINDEX` | 17, 30 | float Hz | RW | [0..7999] **déf 1300** (XMOS 593.75) | **Déjà tuné par Seeed — ne pas toucher sans mesure** |
| `PP_DTSENSITIVE` | 17, 31 | int32 | RW | **[0..5, 10..15]** déf 0 | **CLÉ barge-in** : valeur haute (ex. 2 ou 12) préserve « Stop Aura » en double-talk |
| `PP_ATTNS_MODE` | 17, 32 | int32 0/1 | RW | déf 0 | Réduit le bruit résiduel entre phrases → moins de faux wake |
| `PP_ATTNS_NOMINAL` | 17, 33 | float | RW | [0..1] déf 1.0 | Avec `ATTNS_MODE` |
| `PP_ATTNS_SLOPE` | 17, 34 | float | RW | [0..5] déf 1.0 | Avec `ATTNS_MODE` |

### 2.8 DoA, LED & GPIO (resid 20 — commandes **Seeed**, non documentées chez XMOS)

| Commande | resid, cmd | Type | R/W | Détail | Pertinence Aura |
|---|---|---|---|---|---|
| `DOA_VALUE` | 20, 18 | 2×uint16 | **RO** | `[0]`=angle **0–359°**, `[1]`=1 si parole (fw ≥ **2.0.6**) | **La plus utile** : DoA en degrés + VAD binaire en un read. ⚠️ **gèle si `LED_EFFECT≠4`** sur fw < 2.0.10 (bug #16) |
| `AEC_AZIMUTH_VALUES` | 33, 75 | 4×float rad | RO | (cf. §2.5) | DoA fin sans dépendance à l'effet LED |
| `LED_EFFECT` | 20, 12 | uint8 | RW | 0=off,1=breath,2=rainbow,3=couleur unie,**4=doa**,5=ring | États visuels d'Aura (écoute/parle/erreur) |
| `LED_BRIGHTNESS` | 20, 13 | uint8 | RW | 0–255 (breath/rainbow) | Baisser la nuit (chambre patient) |
| `LED_GAMMIFY` | 20, 14 | uint8 | RW | 0/1 | Couleurs plus fidèles |
| `LED_SPEED` | 20, 15 | uint8 | RW | breath/rainbow | Animation lente/rapide |
| `LED_COLOR` | 20, 16 | uint32 | RW | **`0xRRGGBB`** (ex. `0xFF0000` = rouge) | Code couleur des états Aura |
| `LED_DOA_COLOR` | 20, 17 | 2×uint32 | RW | couleur de base + couleur DoA | Thème de l'indicateur de direction |
| `LED_RING_COLOR` | 20, 19 | 12×uint32 | RW | une couleur par LED (fw ≥ **2.0.7**) | Animations custom (progression, pointeur locuteur) |
| `GPO_READ_VALUES` | 20, 0 | 5×uint8 | RO | ordre X0D11, **X0D30**, **X0D31**, **X0D33**, X0D39 | Vérifier l'état mute/ampli/LED avant d'agir |
| `GPO_WRITE_VALUE` | 20, 1 | 2×uint8 (pin, val) | **WO** | pin ∈ {11,30,31,33,39} | Mute matériel / coupure ampli / extinction LED (⚠️ bug #18) |
| `GPO_PORT_PIN_INDEX` / `GPO_PIN_VAL` / `GPO_PIN_ACTIVE_LEVEL` | 20, 2/3/4 | — | RW/WO | bas niveau | Préférer `GPO_WRITE_VALUE` |
| `GPI_READ_VALUES` | 20 (**binaire C only**) | 3×uint8 | RO | X1D09 (bouton mute, 1=relâché), X1D13, X1D34 | Lire le bouton mute physique |

**⚑ Divergence sur la lecture des GPI :** vérifié sur `xvf_host.py` (via `gh api`) → **aucune
commande GPI n'existe dans le dictionnaire Python** (le script ne peut PAS lire le bouton mute).
Trois désignations coexistent selon la source :
- `GPI_READ_VALUES`, **resid 20** — présente dans le **binaire C** `xvf_host` uniquement ;
- `GPI_VALUE_ALL`, **resid 36, cmd 6** — donnée par le **wiki Seeed** (page XIAO GPIO, contexte I2C).

→ Pour lire les GPI côté Python il faut **utiliser le binaire C** ou patcher `xvf_host.py`
(ajouter l'entrée resid 20). À valider sur le device en main.

**Table GPIO (référence physique) :**

| Pin | Sens | Rôle | État |
|---|---|---|---|
| `X1D09` | entrée | **Bouton mute** | HAUT = relâché |
| `X1D13`, `X1D34` | entrées | flottantes | — |
| `X0D11`, `X0D39` | sorties | flottantes | — |
| `X0D30` | sortie | **Mute micros (matériel) + LED rouge** | **HAUT = micros coupés** |
| `X0D31` | sortie | **Enable ampli audio** | **ACTIF BAS** (0 = ampli ON) |
| `X0D33` | sortie | **Alim WS2812** | HAUT = LED alimentées |

Exemples : `xvf_host GPO_WRITE_VALUE 30 1` = mute micros + LED rouge ; `GPO_WRITE_VALUE 33 0` = éteint l'anneau.

### 2.9 DFU (resid 0xF0 — commandes cachées du binaire)

`DFU_DETACH(0,WO)`, `DFU_DNLOAD(1,WO,130×uint8)`, `DFU_UPLOAD(2,RO,130)`, `DFU_GETSTATUS(3,RO,5)`,
`DFU_CLRSTATUS(4,WO)`, `DFU_GETSTATE(5,RO,1)`, `DFU_ABORT(6,WO)`, `DFU_SETALTERNATE(64,WO : 0=factory, 1=upgrade)`,
`DFU_TRANSFERBLOCK(65,RW,2)`, `DFU_GETVERSION(88,RO,3)`, `DFU_REBOOT(89,WO)`. En pratique on passe par
`dfu-util` (§3), pas par ces commandes brutes.

### 2.10 Tuning d'usine Seeed (déjà appliqué — points de référence)

| Paramètre | Défaut XMOS | **Réglé Seeed** |
|---|---|---|
| `AUDIO_MGR_MIC_GAIN` | 10 | **90** |
| `AUDIO_MGR_REF_GAIN` | 1.5 | **8.0** |
| `AUDIO_MGR_SYS_DELAY` | −32 | **12** |
| `PP_FMIN_SPEINDEX` | 593.75 | **1300.0** |
| `PP_AGCMAXGAIN` | 125.0 | **64.0** |
| `PP_AGCGAIN` | 32.0 | **2.0** |
| `AEC_ASROUTGAIN` | 1.0 | **1.0** |

> **⚑ Gap :** les défauts usine de la **majorité** des paramètres ne sont publiés nulle part
> (issue #15 ouverte : « Default values should be publicly available »). Seuls ces 7 réglages
> Seeed + les défauts de l'appendice XMOS sont connus. **Lire les valeurs sur le device
> (`--dump-params`) avant toute modification.**

---

## 3. Firmware & procédure DFU

### 3.1 Versions publiées (dépôt `xmos_firmwares/`, inventaire vérifié juillet 2026)

| Fichier | Taille | Notes / changelog (reconstitué depuis les commits — **pas de release notes officielles**) |
|---|---|---|
| `..._usb_dfu_firmware_v2.0.5.bin` | 929 792 o | Base |
| `..._usb_dfu_firmware_v2.0.6.bin` | 933 888 o | Fix couleurs WS2812 ; **DAC 0 → +6 dB** ; **ajout `DOA_VALUE`** |
| `..._usb_dfu_firmware_v2.0.7.bin` | 933 888 o | **Ajout `LED_RING_COLOR`** — **version d'usine probable de notre unité** |
| `..._usb_dfu_firmware_6chl_v2.0.8.bin` | 933 888 o | Variante **6 canaux** (mics bruts) — seule version 6ch publiée |
| `..._usb_dfu_firmware_v2.0.9.bin` | 933 888 o | **Mode `fixedbeam`** (PR #11) + échantillonnage 48 kHz |
| `..._usb_dfu_firmware_v2.0.9_48k.bin` | 933 888 o | Variante **48 kHz** stéréo (`ua-io48-sqr`) |
| `..._usb_dfu_firmware_v2.0.10.bin` | 933 888 o | **Découplage état DoA / effet LED** (PR #23, corrige #16). ⚑ r2 mentionne aussi « restauration des descripteurs/buffers sur bus reset » ; **r5 (terrain 2026-07-05) confirme que le bug bInterval/warm-reboot n'est PAS annoncé corrigé** — traiter v2.0.10 comme corrigeant **uniquement** #16 |
| `..._i2s_dfu_firmware_v1.0.4 / v1.0.7.bin` | 888 832 o | I2S 2ch |
| `..._i2s_master_..._v1.0.5_48k / v1.0.7_48k_test5.bin` | — | Démo ESPHome / Home-Assistant (v1.0.7 améliore la détection wake word) |
| `recover/4mb_all_ff.bin` | 4 194 304 o | Image 4 Mo de `0xFF` pour effacer la flash (récupération de brick) |

**Version recommandée pour Aura : `v2.0.10`** (dernière ; corrige le gel du DoA sous LED
custom). Elle **ne corrige pas** (documenté) le bug bInterval (§4.1) ni le brick
`save_configuration` (§4.2) → garder les workarounds.

### 3.2 Procédure DFU pas-à-pas (Linux / Raspberry Pi)

```bash
# 1. Brancher le port USB-C XMOS = celui PROCHE du jack 3,5 mm.
#    (L'AUTRE port USB-C ne fonctionne PAS pour le DFU.)

# 2. Installer dfu-util
sudo apt install dfu-util

# 3. Vérifier la détection — doit lister DEUX alt-settings :
sudo dfu-util -l
#   [2886:001a] ... alt=1, name="reSpeaker DFU Upgrade"
#   [2886:001a] ... alt=0, name="reSpeaker DFU Factory"

# 4. Flasher la partition Upgrade (alt 1). alt 0 = Factory, JAMAIS touché = filet de secours.
sudo dfu-util -R -e -a 1 -D respeaker_xvf3800_usb_dfu_firmware_v2.0.10.bin
#   -a 1 = Upgrade ; -R = reboot auto ; -e = detach
#   transfert ~930 Ko par blocs de 256 o
#   « Invalid DFU suffix signature » = AVERTISSEMENT NORMAL

# 5. Vérifier
sudo dfu-util -l
python xvf_host.py VERSION      # doit renvoyer [2, 0, 10]
```

- **⛔ NE JAMAIS télécharger les `.bin` via « save as » sur GitHub** (les fichiers se corrompent — avertissement wiki officiel). **Cloner le dépôt** ou « Download ZIP ». Recouper la **taille** (933 888 o pour les USB ≥ 2.0.6).
- **Firmware USB → DFU USB uniquement** ; **firmware I2S → DFU I2C uniquement** ; **Safe Mode supporte les deux**.
- Le flash runtime n'écrit **que** l'alt 1 « Upgrade » ; l'alt 0 « Factory » reste le filet de secours même si l'upgrade échoue.
- DFU par I2C sur le Pi : outil `xvf_i2c_dfu` (`host_control/rpi_64bit/`).

### 3.3 Safe Mode

Firmware de secours qui expose le DFU quel que soit l'état du firmware principal :

1. Couper l'alimentation.
2. **Maintenir le bouton Mute enfoncé** et rebrancher en le maintenant.
3. La **LED rouge clignote** = Safe Mode actif. On peut reflasher n'importe quel firmware (USB DFU **et** I2C).

En Safe Mode, `dfu-util -l` expose alt 0 (Factory), alt 1 (Upgrade) et **alt 2 (DataPartition)**.

### 3.4 Récupération d'un device briqué (bug #8)

```bash
# En Safe Mode (Mute maintenu au branchement) :
dfu-util -e -a 1 -D 4mb_all_ff.bin
#   → s'ARRÊTE à ~96 % avec « dfuERROR status(8) = address out of range »
#     C'EST NORMAL ET ATTENDU.
# Puis reflasher le firmware normal, puis rebooter.
```

Un **simple reflash firmware ne suffit PAS** : la partition de config corrompue survit au reflash et fait crasher le firmware avant l'init USB. Il faut d'abord effacer avec `4mb_all_ff.bin`.

### 3.5 Règle udev (Pi)

`/etc/udev/rules.d/99-respeaker.rules` :

```udev
# Accès non-root à l'interface de contrôle
SUBSYSTEM=="usb", ATTRS{idVendor}=="2886", ATTRS{idProduct}=="001a", MODE="0666", GROUP="plugdev"
# Anti-autosuspend (mitigation coupures capture isochrone — cf. §4.1)
SUBSYSTEM=="usb", ATTR{idVendor}=="2886", ATTR{idProduct}=="001a", ATTR{power/control}="on"
```

```bash
sudo udevadm control --reload-rules && sudo udevadm trigger
```

---

## 4. ⚠️ Bugs connus & contournements (CRITIQUE)

> **RÈGLE D'OR (rappel) : sur firmware < 2.0.10, `xvf_host` en LECTURE SEULE — aucune
> écriture, jamais `save_configuration`.** Les écritures ne sont ouvertes qu'après MAJ
> firmware validée (§3), sur une **unité de test**, jamais directement sur l'enceinte de
> production.

### 4.1 🔴 Bug warm-reboot — issue #20 (OUVERTE, fw 2.0.6/2.0.7, hosts aarch64 dont Pi 5)

**Symptôme :** après un **reboot soft du host**, le XVF3800 ré-énumère normalement
(`lsusb`, ALSA OK) mais **la capture est un bourdonnement continu inintelligible**.
Diagnostic device : `AEC_SPENERGY_VALUES = 0 0 0 0` en parlant, `AEC_AECCONVERGED = 0`,
`AEC_RT60 = 1.401298e-45`.

**Ce bug a DEUX causes racines distinctes — les deux workarounds doivent être combinés dans la séquence de boot :**

**Cause A — AEC non recalibrée.** Le reboot soft ré-énumère les lignes data mais **VBUS reste haut** → le DSP XMOS ne fait jamais de power-on-reset → l'AEC reprend dans un état non convergé.
- **Fix : `xvf_host REBOOT 1`** — re-exécute la calibration AEC **sans toucher VBUS**, équivalent au replug physique. **Idempotent** → l'exécuter **inconditionnellement à chaque boot** (systemd oneshot) plutôt que détecter l'état cassé. Le device quitte le bus ~0,5 s et **ré-énumère à une nouvelle adresse USB** → re-résoudre par **VID:PID `2886:001a`** (pas par `/dev/bus/usb/X/Y`), attendre **5–8 s** avant d'ouvrir la capture, **ne jamais l'émettre en pleine capture**.
- C'est le **workaround officiel Seeed** ET la **recommandation Pollen Robotics** (Reachy Mini embarque un XVF3800) : « run `xvf_host REBOOT 1` every time the robot is plugged in or the host reboots ».

**Cause B — descripteur d'endpoint cassé.** Selon le timing d'énumération, l'EP capture `0x81 IN` sort en `wMaxPacketSize=64 / bInterval=1` (8000 paquets/s) au lieu de `32 / bInterval=3` (2000 p/s) → le xhci Linux ne suit pas → **flood `xhci-hcd WARN: buffer overrun event` (~8000/s, COMP_ISOC_BUFFER_OVERRUN 0x25)** qui sature `journald` et **tue `arecord`**. (XMOS Release Notes v3.2.1, Known Issue #3.)
- **Détection :** `lsusb -v -d 2886:001a` → `bInterval` de l'EP 1 IN doit valoir **3**.
- **Fix : `xvf_host USB_BIT_DEPTH 16 16`** — reboote la puce et **recharge les descripteurs par défaut** (retour à l'état sain). **Mais** efface tous les paramètres DSP réglés à chaud → **les ré-appliquer APRÈS** (`sleep 6`).

**Ce qui NE corrige PAS :** unbind/bind sysfs + restart PulseAudio, `usbreset` Linux. **Piège de validation :** un `dfu-util -R` (soft-reset) tombe par hasard sur l'état sain → **seul un vrai power-cycle (ou `USB_BIT_DEPTH`) reproduit le bug de façon fiable.** Non corrigé en ≤ 2.0.10.

**⚠️ Caveat sonde santé :** dans une pièce **silencieuse sans signal far-end**, `SPENERGY`
et `AECCONVERGED` peuvent être à 0 sur un device **sain** → ne pas s'en servir seuls comme
détecteur de panne. Coupler à un court enregistrement test, ou **appliquer le `REBOOT 1`
inconditionnel** au boot sans détection.

### 4.2 🔴 Brick par `save_configuration` — issue #8 (OUVERTE, 3 repros cross-OS, v2.0.6 ET 6chl v2.0.8)

**`./xvf_host save_configuration 1` peut corrompre la DataPartition** → le device
**n'énumère PLUS DU TOUT** en mode normal (LEDs allumées, aucun `2886:001a`). Seul le
**Safe Mode** fonctionne. **Reflasher le firmware ne suffit PAS** (la config corrompue
survit et crashe le firmware avant l'init USB) → récupération obligatoire via
`4mb_all_ff.bin` (§3.4). Seeed suggère que v2.0.9 « may resolve » — **NON confirmé**, une
repro existe sur v2.0.8.

> **`SAVE_CONFIGURATION` / `CLEAR_CONFIGURATION` sont des ajouts Seeed** (absents de la doc
> XMOS ; la persistance officielle XMOS passe par la recompilation du firmware, impossible
> pour nous car firmware fermé). **La seule persistance fiable est côté hôte.**
>
> **🚫 RÈGLE PRODUIT AURA : ne JAMAIS utiliser `save_configuration`, quelle que soit la
> version.** Persister via un **service systemd qui ré-applique tous les réglages à chaque
> boot** (après le `REBOOT 1`).

### 4.3 🔴 Combos LED/GPO → micros muets / états cassés — issue #18 (OUVERTE, aucune réponse mainteneur)

Après une séquence `LED_EFFECT` + `led_color`/`led_speed`/`led_brightness` +
`GPO_WRITE_VALUE` + `CLEAR/SAVE_CONFIGURATION`, un device (fw 2.0.5/2.0.6/2.0.7) est resté
**durablement cassé** : micros totalement muets, LED mute anormale (flash bref au lieu de
rouge fixe), **DoA gelée** (`AEC_AZIMUTH_VALUES` → « Read attempt exceeds 100 times » /
« Check the audio loop is active »), lecture audio instable. **Ces dégâts SURVIVENT à
plusieurs reflashes DFU et à `CLEAR_CONFIGURATION`** (le reflash DFU ne réinitialise pas la
config du codec TLV320AIC3104).

> **🚫 RÈGLE :** bannir `GPO_WRITE_VALUE` **combiné** aux commandes de persistance. S'en
> tenir à `LED_*` + `REBOOT`. **Toute expérimentation LED/GPO se fait sur une unité
> sacrifiable, jamais sur l'enceinte de prod.**

### 4.4 🟠 DoA figée hors mode LED doa — issue #16 (fw ≤ 2.0.9)

`DOA_VALUE` ne se met à jour **que si `LED_EFFECT=4`** (mode doa). Avec tout autre effet, la
lecture renvoie **indéfiniment la dernière valeur figée**. **Corrigé par v2.0.10** (PR #23,
découplage DoA/effet). Absent sur XVF3000 v3.
→ Si Aura pilote des LED custom **et** lit le DoA : **rester en v2.0.10**, ou utiliser
`AEC_AZIMUTH_VALUES` (resid 33, indépendante de l'effet LED).

### 4.5 🟠 Erreurs `xmos_write_bytes` — issue #12

`xmos_write_bytes resid=20 cmd=18 error=2` = **firmware USB flashé alors qu'un mode I2S/I2C
est attendu** (contexte ESPHome/XIAO). → Flasher le **firmware i2s**. Sans objet pour Aura
tant qu'on reste en USB, mais utile à reconnaître si on hérite d'une unité mal flashée.

### 4.6 🟡 Autres comportements rapportés

- **Rainbow de boot non désactivable** (issue #5, ouverte) : ~2 s de rainbow à chaque boot avant de charger la config, **non désactivable**.
- **6ch : canaux bruts muets** (issue #22) : après flash `6chl`, ALSA expose 6 canaux mais **ch2–ch5 restent à ZÉRO** tant que les switches de capture ALSA ne sont pas activés → `amixer -c <card> cset numid=8 on,on,on,on,on,on` + volumes `cset numid=10 60,…` puis `sudo alsactl store <card>`. Non documenté au wiki.
- **HP externe inutile** (issue #19) : « external speakers won't work because there's no reference signal » — l'AEC ne marche **que** si la lecture passe par la sortie de l'array. **Ne jamais déporter le HP sur le jack du Pi.**
- **Pi Zero 2 W** : corruption du flux au **cold power-up** si le device est déjà branché au démarrage ; audio propre si on le rebranche une fois le Pi démarré (même famille que #20, dans l'autre sens).
- **Contrôleur USB** : échecs `Input/output error` + `retire_capture_urb … callbacks suppressed` sur certains roots **EHCI/non-xHCI** ; **parfait sur xHCI** (les 4 ports USB-A du **Pi 4** sont derrière le xHCI VL805 → OK).

### 4.7 🟡 Alimentation (aucune spec publiée)

Consommation board-level **jamais publiée** (ni Seeed, ni datasheet XVF3800 qui renvoie au
datasheet XU316). Un HP 5 W sur le JST peut tirer **~1 A à 5 V** via le bus USB du Pi 4
(budget total ~1,2 A) → à fort volume, **risque de sous-tension silencieux** (le Pi n'alerte
qu'en dessous de 4,63 V, zone où l'USB peut déjà dysfonctionner). **Alim officielle
5,1 V/3 A + câble court** ; hub alimenté si HP 5 W poussé fort. **À mesurer au wattmètre USB**
en pire cas (LED blanches 100 % + TTS fort).

---

## 5. Réglages recommandés Aura

> **⚠️ Tout ce qui suit en ÉCRITURE n'est autorisé qu'APRÈS une MAJ firmware validée (v2.0.10)
> et uniquement sur une unité de test.** Sur l'unité d'usine actuelle (probablement 2.0.7) :
> **LECTURE SEULE.**

### 5.1 À LIRE (sûr, même en firmware d'usine)

| Commande | Usage Aura |
|---|---|
| `VERSION`, `BLD_MSG` | Healthcheck au boot + télémétrie flotte (quel firmware/variante) |
| `DOA_VALUE` (ou `AEC_AZIMUTH_VALUES`) | Orientation du locuteur + VAD (télémétrie wake, croisement ECAPA) |
| `AEC_SPENERGY_VALUES` | VAD matériel — gate avant openWakeWord/Deepgram |
| `AEC_AECCONVERGED`, `AEC_RT60` | **Debug AEC** : détecter l'état warm-reboot cassé (§4.1) |
| `AEC_AECPATHCHANGE` | Détecter que l'enceinte a été déplacée |
| `GPO_READ_VALUES` | État mute/ampli/LED avant capture |

### 5.2 À NE PAS toucher

- **`SAVE_CONFIGURATION` / `CLEAR_CONFIGURATION`** → 🚫 (bug #8). Persistance côté Pi uniquement.
- **`GPO_WRITE_VALUE` combiné à la persistance** → 🚫 (bug #18).
- `PP_FMIN_SPEINDEX` (déjà tuné Seeed à 1300), les 7 gains d'usine (§2.10) sans mesure préalable.
- `USB_BIT_DEPTH` en prod (reboot + reset), `SHF_BYPASS` (debug), `TEST_CORE_BURN` / `TEST_AEC_DISABLE_CONTROL` (jamais).

### 5.3 Stratégie LED

**Laisser le défaut (rainbow au boot → doa après 2 s) tant que le bug #18 n'est pas résolu.**
Le pilotage LED custom (`LED_EFFECT`/`LED_COLOR`/`LED_RING_COLOR`) est séduisant pour les
états d'Aura (écoute/réflexion/parle, visibles pour l'entourage d'un patient malvoyant),
**mais** : (a) hors `LED_EFFECT=4` la DoA gèle sur fw < 2.0.10 (bug #16) ; (b) un device a été
durablement cassé après combos LED/GPO + persistance (#18). → **N'activer le pilotage LED
qu'en v2.0.10, sur unité de test, sans jamais y associer `save_configuration`.**

### 5.4 Séquence de boot fiable (squelette systemd)

```ini
# /etc/systemd/system/xvf3800-dsp-reset.service
[Unit]
Description=Reset DSP XVF3800 + ré-application des réglages (workaround #20)
Before=aura-orchestrator.service      # avant le pipeline audio
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/opt/xvf3800/host/xvf_host REBOOT 1     # Cause A du bug #20
ExecStartPost=/bin/sleep 6
# Puis (script) : lire bInterval ; si != 3 -> USB_BIT_DEPTH 16 16 + sleep 6 (Cause B)
#                 ré-appliquer les paramètres DSP tunés (AEC_ASROUTGAIN, etc.)
[Install]
WantedBy=multi-user.target
```

Ordre : **reset DSP → sleep → sonde `bInterval` → (si besoin) `USB_BIT_DEPTH` → ré-application
des paramètres → démarrage openWakeWord/Deepgram**. Re-résoudre le device par VID:PID après
chaque REBOOT.

### 5.5 Leviers de tuning pertinents (après validation firmware, sur unité de test)

| Objectif | Levier | Détail |
|---|---|---|
| Niveau STT trop faible/fort | `AEC_ASROUTGAIN` | Agit **seulement** sur ch1 ASR, sans toucher au Conference |
| **Barge-in « Stop Aura » pendant le TTS** | `PP_DTSENSITIVE` (2 ou 12) ; `PP_GAMMA_E`/`ENL` vers 1.4 | Compromis suppression écho vs double-talk |
| AEC décroche avec HP sur jack | `AUDIO_MGR_SYS_DELAY` (déf 12) | Aligne la référence sur le playback réel |
| Rumble basse fréquence | `AEC_HPFONOFF` (3 ou 4 = 150/180 Hz) | Avant wake word |
| Bruit remonté entre phrases | `PP_ATTNS_MODE 1` | Moins de faux wake |

> **Note ECAPA :** l'AGC embarquée (`PP_AGCMAXGAIN=64`) modifie dynamiquement les niveaux →
> peut perturber les embeddings locuteur. Le ch1 ASR **n'a pas d'AGC**, donc les niveaux vus
> par openWakeWord/ECAPA dépendent **uniquement** de `AUDIO_MGR_MIC_GAIN` et `AEC_ASROUTGAIN`
> — c'est là qu'il faut agir, pas sur l'AGC.

---

## 6. Opportunités d'intégration

### 6.1 DoA / VAD lisibles par le device Python

Intégrer un **module `pyusb`** dans `rasberry/` (plutôt qu'un sous-process `xvf_host`) qui lit
`DOA_VALUE` (angle 0–359° + VAD binaire en un read) et `AEC_SPENERGY_VALUES` :
- **Télémétrie wake** : logger l'angle du locuteur avec chaque wake-event.
- **Gate VAD matériel** : ne lancer l'inférence openWakeWord / n'ouvrir Deepgram que si `SPENERGY > 0` → économie CPU/API.
- **Croisement ECAPA** : rejeter une « voix » venant de la direction du HP (anti-écho directionnel).
- Compatible streaming : l'interface de contrôle (n°3) coexiste avec les interfaces audio UAC.
- **⚑ Gap : l'orientation physique du 0° du DoA n'est documentée nulle part → à calibrer empiriquement** sur l'enceinte.

### 6.2 Firmware 6ch pour ECAPA sur micros bruts

Le firmware **`6chl_v2.0.8`** expose ch2–ch5 = **micros bruts** → utile pour faire tourner
ECAPA (ou une diarisation) sur du signal non-beamformé/non-débruité.
- **Trade-off :** bloqué en **v2.0.8** (pas de v2.0.9/2.0.10 en 6ch → pas le fix DoA/LED #16). Nécessite d'activer les switches ALSA (`amixer`, bug #22).
- **Alternative sans changer de firmware :** `AEC_ASROUTONOFF=0` fait sortir les **résidus AEC par micro** (1 canal/micro) sur la catégorie mux 7 = du quasi-brut, sans flasher le 6ch.

### 6.3 Bouton mute matériel (X1D09) vs mute logiciel Aura

- **Mute matériel certifiable** (confidentialité cabinet médical) : `GPO_WRITE_VALUE 30 1` coupe les micros **au niveau du circuit** + allume la LED rouge. `X0D31` coupe l'ampli.
- **Lecture du bouton physique** : `X1D09` (1 = relâché) — mais ⚠️ **absent de `xvf_host.py`** (voir §2.8 ⚑) : utiliser le binaire C ou patcher le script.
- **Attention Aura :** le mute matériel rend l'audio **silence plat** → l'orchestrateur le voit comme un « micro mort » (détection après `MIC_DEAD_S`). À gérer explicitement.
- **⚠️** Prudence bug #18 : ne pas scripter de combos GPO + persistance.

### 6.4 Beams fixes (installation en position connue)

`AEC_FIXEDBEAMSONOFF=1` (fw ≥ 2.0.9) + `AEC_FIXEDBEAMSAZIMUTH_VALUES` : **figer l'écoute vers
la position habituelle du patient** (ex. lit médicalisé) au lieu du scan automatique →
fiabilise wake word et STT, ignore le reste de la pièce.

---

## 7. Dépannage — arbre de décision

| Symptôme | Diagnostic → Action |
|---|---|
| **`arecord` → `Input/output error` immédiat (44 octets)** | 1) **Replug physique** du device (issue #20). 2) Si ça revient au reboot → installer le `REBOOT 1` systemd (§5.4). 3) Vérifier le firmware (`VERSION`) → passer en **v2.0.10** (§3). 4) Vérifier le contrôleur : **xHCI** (Pi 4 OK) ; règle udev **anti-autosuspend**. 5) Vérifier `bInterval` (§4.1 cause B). |
| **Bourdonnement / capture inintelligible après un reboot** | **Bug warm-reboot #20.** `xvf_host REBOOT 1` (pas besoin de replug physique). Confirmer : `AEC_RT60` → `1.4e-45` = cassé. |
| **Flood `xhci-hcd WARN: buffer overrun` / journald saturé** | Descripteur cassé (bInterval=1). `lsusb -v -d 2886:001a` → si `bInterval ≠ 3` : `xvf_host USB_BIT_DEPTH 16 16` + `sleep 6` + ré-appliquer les paramètres. |
| **Device disparu (LEDs allumées, aucun `2886:001a`)** | Probable **brick `save_configuration` (#8)**. → **Safe Mode** (Mute maintenu au branchement) → `4mb_all_ff.bin` (échec ~96 % normal) → reflash firmware → reboot (§3.4). |
| **DoA figée / ne bouge plus** | **#16** : si `LED_EFFECT ≠ 4`, la DoA gèle sur fw < 2.0.10. → Flasher **v2.0.10**, ou repasser `LED_EFFECT 4`, ou lire `AEC_AZIMUTH_VALUES`. |
| **Micros muets + LED mute anormale après manips LED/GPO** | **#18** : dégâts possiblement **irrécupérables** (survivent au reflash). Remplacer l'unité. Ne plus scripter GPO + persistance. |
| **Wake word rate quand la musique/TV joue fort** | Éloigner l'enceinte des sources audio externes ; l'AEC **ne marche que via la sortie de l'array**. Alimenter openWakeWord depuis **ch1 (ASR)** (AEC linéaire plus légère, meilleur pendant la musique). |
| **`xmos_write_bytes resid=20 cmd=18 error=2`** | **#12** : firmware USB sur une carte attendue en I2S. → Flasher le **firmware i2s** (hors périmètre USB Aura). |

---

## 8. Sources

**Dépôt GitHub `respeaker/reSpeaker_XVF3800_USB_4MIC_ARRAY`** (branche master, lu via `gh api`) :
- `host_control/README.md`, `python_control/xvf_host.py`, `python_control/readme.md`, `python_control/respeaker_get_doa.py`
- `xmos_firmwares/dfu_guide.md`, `xmos_firmwares/` (inventaire), `dfu_cmds.yaml`, `transport_config.yaml`
- Issues : [#4](https://github.com/respeaker/reSpeaker_XVF3800_USB_4MIC_ARRAY/issues/4) (ordre octets LED), [#5](https://github.com/respeaker/reSpeaker_XVF3800_USB_4MIC_ARRAY/issues/5) (rainbow boot), [#8](https://github.com/respeaker/reSpeaker_XVF3800_USB_4MIC_ARRAY/issues/8) (brick save_configuration), [#12](https://github.com/respeaker/reSpeaker_XVF3800_USB_4MIC_ARRAY/issues/12) (xmos_write_bytes), [#15](https://github.com/respeaker/reSpeaker_XVF3800_USB_4MIC_ARRAY/issues/15) (défauts non publiés), [#16](https://github.com/respeaker/reSpeaker_XVF3800_USB_4MIC_ARRAY/issues/16) (DoA figée), [#18](https://github.com/respeaker/reSpeaker_XVF3800_USB_4MIC_ARRAY/issues/18) (LED/GPO cassé), [#19](https://github.com/respeaker/reSpeaker_XVF3800_USB_4MIC_ARRAY/issues/19) (AEC/HP externe), [#20](https://github.com/respeaker/reSpeaker_XVF3800_USB_4MIC_ARRAY/issues/20) (warm-reboot), [#22](https://github.com/respeaker/reSpeaker_XVF3800_USB_4MIC_ARRAY/issues/22) (6ch muets), PR #11/#13/#23

**Wiki Seeed Studio** : [`respeaker_xvf3800_introduction`](https://wiki.seeedstudio.com/respeaker_xvf3800_introduction/), `_python_sdk`, `_xiao_gpio`, `_xiao_i2s`, `_xiao_rgb`, `_xiao_volume`, `_ros2_voice_pipeline`, `_picovoice`

**Documentation officielle XMOS** : User Guide XVF3800 v3.2.1 (**XM-014888-PC**, 2024-10-29) — appendice « Control Commands » (`AA_control_command_appendix.html`), « Using the host application » (Table 26 du mux, `03_using_the_host_application.html`), « Tuning » (`04_tuning_the_application.html`), « ASR » (`08_automatic_speech_recognition.html`), Datasheet (`03_audio_pipeline.html`, `06_device_operation.html`)

**Retours terrain** : [Pollen Robotics / Reachy Mini #389](https://github.com/pollen-robotics/reachy_mini/issues/389), [forum.seeedstudio.com t/294782](https://forum.seeedstudio.com/t/respeaker-xvf3800-hangs-after-computer-reboot/294782), [community.home-assistant.io t/927241](https://community.home-assistant.io/t/respeaker-xmos-xvf3800-esphome-integration/927241), [github.com/formatBCE/Respeaker-XVF3800-ESPHome-integration](https://github.com/formatBCE/Respeaker-XVF3800-ESPHome-integration), smarthomecircle.com, forums.raspberrypi.com t/319755, forum.core-electronics.com.au t/24324

---

### Gaps connus (à lever device en main)

- Défauts usine de la majorité des paramètres non publiés (lire `--dump-params`).
- Orientation physique du 0° DoA à calibrer empiriquement.
- Consommation électrique board-level jamais publiée (mesurer au wattmètre).
- Latence précise de la voie ASR (ch1) inconnue (~50 ms typ. pour le pipeline complet).
- Correction du bug bInterval en v2.0.10 **non confirmée** ; correction du brick `save_configuration` en v2.0.9+ **non confirmée** → garder les workarounds quelle que soit la version.
- Lecture des GPI en Python : commande absente de `xvf_host.py` (⚑ resid 20 binaire C vs resid 36 wiki) — à trancher sur le device.
- Comportement de `REBOOT` validé terrain uniquement sur fw 2.0.6/2.0.7 → re-tester après flash v2.0.10.
