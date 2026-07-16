# ReSpeaker XVF3800 USB — Runbook d'intégration (enceinte Aura)

> Micro array 4 capsules, puce XMOS XVF3800 : AEC matérielle, beamforming, suppression
> de bruit DNN, AGC 60 dB, portée 5 m à 360°. Remplace le micro USB 360° Amazon.
> **Aucun HAT GPIO** : tout passe par USB-C (les deux Pi grillés venaient du HAT).

## 0. Principe — POURQUOI ce câblage précis

L'AEC matérielle du XVF3800 n'annule que l'écho de **ce qui est joué À TRAVERS l'array**
(le flux de lecture USB sert de référence d'écho et sort par SA sortie haut-parleur).
Donc :

```
AVANT :  Pi ──jack (plughw:2)──▶ enceinte     +     micro USB Amazon ──▶ Pi
APRÈS :  Pi ──USB-C──▶ ReSpeaker XVF3800 ──jack 3,5mm / JST 5W──▶ enceinte
              (capture ET lecture passent par l'array → écho annulé en hardware)
```

Si le TTS reste sur le jack du Pi, l'AEC ne sert à rien : « Stop Aura » pendant
la lecture restera masqué par l'écho.

## 1. Branchement

1. **Haut-parleur** → sortie de l'array : jack 3,5 mm AUX (enceinte amplifiée)
   **ou** connecteur JST (haut-parleur passif, ampli 5 W intégré).
2. **Array** → port USB du Pi (câble USB-C fourni).
3. L'ancien micro USB : le laisser débranché (garde-le en secours de dev).
4. Alimentation du Pi : PSU officielle 5 V/3 A minimum (l'array consomme sur le bus USB).

## 2. Vérifications (avant de toucher à Aura)

```bash
# L'array est vu ?  → chercher « reSpeaker XVF3800 4-Mic Array »
arecord -l
aplay -l
# Noter le numéro de carte X (ex: card 3) — il sert partout ensuite.

# Test capture (5 s, stéréo 16 kHz — ch0=Conference, ch1=ASR) :
arecord -D plughw:X,0 -c 2 -r 16000 -f S16_LE -d 5 /tmp/test.wav
# Test lecture À TRAVERS L'ARRAY (le haut-parleur branché dessus doit jouer) :
aplay -D plughw:X,0 /tmp/test.wav
# Volume si besoin : alsamixer -c X (PCM), puis `sudo alsactl store`
```

**Test AEC (le juge de paix)** : jouer de la musique via `aplay -D plughw:X,0` en
continu, et en même temps enregistrer ; parler par-dessus. À la réécoute, la musique
doit être quasi absente de l'enregistrement, la voix claire.

## 3. Configuration Aura (Pi)

Dans `~/.aura/env` (ou `raspberry/device/.env`) — remplacer X par le numéro de carte :

```bash
# ── ReSpeaker XVF3800 ──────────────────────────────────────
AUDIO_INPUT_DEVICE=reSpeaker         # sélection par NOM (stable si la carte change de numéro)
AUDIO_INPUT_CHANNELS=2               # flux stéréo Conference/ASR
AUDIO_INPUT_CHANNEL=1                # ch1 = ASR (optimisé reconnaissance vocale)
AUDIO_OUTPUT_DEVICE=plughw:X         # lecture À TRAVERS l'array = référence AEC
AEC_ENABLED=0                        # l'AEC est dans le matériel — PAS de PipeWire
```

Supprimer/commenter l'ancien `AUDIO_OUTPUT_DEVICE=plughw:2` (jack Pi).

Puis :
```bash
cd ~/aura/raspberry && source device/venv/bin/activate
python -m device.orchestrator
```
Au démarrage, vérifier la ligne `[mic] capture démarrée (16000 Hz)` (ou 48000 → resample,
selon le firmware) et l'absence d'erreur sounddevice.

> Si sounddevice refuse le nom `reSpeaker` : lister les périphériques
> `python -c "import sounddevice as sd; print(sd.query_devices())"` et mettre
> l'index numérique à la place.

## 4. Tests fonctionnels (dans l'ordre)

1. **Wake** : « Dis Aura » à 1 m, 3 m, 5 m — comparer les scores télémétrie
   (`wake_events`) à l'historique (~0.33-0.86 avec l'ancien micro).
2. **Stop pendant lecture** (LE test que l'ancien setup ratait) : lancer une réponse
   longue, dire « Stop Aura » à voix normale → doit couper immédiatement, même de loin.
3. **Bruit** : YouTube fort à côté → wake + capture + « Stop Aura » de clôture.
4. **Transcription** : une phrase avec des noms propres → vérifier le transcript.
5. **Empreinte vocale** : la vérif ECAPA doit continuer d'accepter la voix enrôlée.
   ⚠️ Le beamforming/denoise change légèrement le timbre : si les scores ECAPA chutent
   (< 0.30), ré-enrôler la voix depuis Réglages → Voix & enceinte, AVEC le nouveau micro.

## 5. Recalibrage attendu (après une journée de télémétrie)

Le signal sera plus propre → les scores wake montent. À re-régler éventuellement :
- `ACTIVATE_THRESHOLD` : si les vrais wake scorent tous > 0.6, remonter 0.35 → 0.5
  (moins de faux positifs) ;
- `STOP_SPEAKING_THRESHOLD` (0.70) : avec l'AEC matérielle, l'écho TTS disparaît de la
  capture → on peut viser 0.5 pour un barge-in encore plus réactif ;
- le stop-guard ECAPA (`INTERRUPT_VERIFY_MIN`) devrait mieux marcher (voix non noyée).
**Ne rien changer avant d'avoir les chiffres `wake_events`.**

## 6. Outils avancés (optionnel)

- `xvf_host` (dépôt respeaker/reSpeaker_XVF3800_USB_4MIC_ARRAY, dossier
  `host_control/rpi_64bit/`) : version firmware, direction d'arrivée (DoA), LED ring,
  gains, `save_configuration 1` pour persister sur la puce.
- LED ring de l'array : `./xvf_host led_effect 0` pour l'éteindre (Aura a déjà sa LED
  d'état GPIO ; deux anneaux qui clignotent = confusion).
- Bouton mute matériel de l'array : coupe les micros dans la puce (LED rouge) —
  complémentaire du mute logiciel d'Aura, mais Aura ne le « voit » pas (l'audio devient
  silence plat → détection « micro mort » après MIC_DEAD_S).
- Firmware : `sudo apt install dfu-util && sudo dfu-util -l` (variante USB requise,
  PAS la variante I2S/Home-Assistant qui est en 48 kHz I2S).

## 7. Rollback (2 minutes)

Rebrancher l'ancien micro USB + enceinte sur le jack Pi, puis dans l'env :
```bash
AUDIO_INPUT_DEVICE=            # (vide → défaut)
AUDIO_INPUT_CHANNELS=1
AUDIO_INPUT_CHANNEL=0
AUDIO_OUTPUT_DEVICE=plughw:2
```
Relancer l'orchestrateur. (Les nouveaux paramètres par défaut = comportement historique.)
