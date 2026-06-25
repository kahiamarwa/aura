# Prompts vocaux d'enrôlement

Fichiers MP3 joués LOCALEMENT par l'enceinte pendant l'enrôlement vocal guidé
(`device/enroll.py`) : « Parle maintenant », « Continue… », « C'est bon », etc.

## Génération (une fois, sur le serveur où ElevenLabs est configuré)

```bash
cd backend && python gen_enroll_prompts.py
```

Cela écrit ici : `enroll_intro.mp3`, `enroll_speak.mp3`, `enroll_continue.mp3`,
`enroll_almost.mp3`, `enroll_done.mp3`, `enroll_fail.mp3`. **Commit-les** pour les
embarquer sur l'enceinte.

Si un fichier manque, le device retombe gracieusement sur un simple bip (pas de blocage).
Aucune clé n'est requise à l'exécution sur l'appareil : les MP3 sont pré-générés.
