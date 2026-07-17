# Wake word v3 — ré-entraînement adapté au ReSpeaker XVF3800

Objectif : remplacer `Aura_test.onnx` (v2, entraîné 100 % synthétique sur l'acoustique
de l'ancien micro) par un modèle v3 qui score 0.8+ sur le canal ASR du XVF3800.

Base : le notebook existant `dis_aura_v2_20k_50k.ipynb` (Colab, openWakeWord).
Trois corrections + deux ajouts. **Étape 0 obligatoire : la collecte sur le Pi.**

---

## Étape 0 — Collecter des échantillons RÉELS sur le Pi (30 min)

Le levier n°1 : des positifs enregistrés **à travers le pipeline réel** (canal ASR
+ gain ×6 + beamforming/denoise de la puce) — exactement ce que le modèle verra.

```bash
cd ~/aura/raspberry && source device/venv/bin/activate

# 0. Purger une éventuelle collecte ratée :
rm -rf ~/wake_samples/positive_real

# 1. ~60 captures DÉCLENCHÉES par le wake actuel (mode flexible : laisse tourner,
#    dis « Dis Aura » librement — 1/3/5 m, voix variées, plusieurs locuteurs,
#    dos tourné, en marchant. Chaque détection ≥ 0.20 sauvegarde 3 s d'audio.)
python -m device.tools.collect_wake_samples --wake-triggered 60
#    Le score pic est dans le nom (wake_0007_s042.wav = 0.42) : à la fin,
#    réécouter les < 030 (aplay) et supprimer les faux positifs.

# 2. 30 min d'ambiance de bureau SANS wake word (fonds négatifs réalistes)
python -m device.tools.collect_wake_samples --ambient-minutes 30

# 3. Rapatrier sur le poste :
scp -r pi@<ip-du-pi>:~/wake_samples ./wake_samples_xvf3800
```

Chaque capture affiche son score pic ; les essais FAIBLES (0.20-0.35) sont précieux — ce sont exactement ceux que la v2 rate et que la v3 doit apprendre.

---

## Étape 1 — 🔴 BUG à corriger dans le notebook : AudioSet était VIDE

Dans la run v2, la cellule 2B a affiché `tar: This does not look like a tar archive`
→ `audioset_16k/` a été créé **vide** et le check « ✓ » ne vérifie que l'existence du
dossier. Conséquence : **le modèle v2 n'a eu que de la musique (FMA) comme fond
sonore** — aucune voix/bruit de vie réels dans l'augmentation.

Remplacer le bloc AudioSet de la cellule 2B par (téléchargement via huggingface_hub,
pas de wget/tar fragile) :

```python
# ── AudioSet (FIX v3 : hf_hub_download au lieu de wget+tar silencieusement cassé) ──
import tarfile
from huggingface_hub import hf_hub_download
if not os.path.exists("audioset_16k") or len(os.listdir("audioset_16k")) < 100:
    os.makedirs("audioset", exist_ok=True)
    tar_path = hf_hub_download(repo_id="agkphysics/AudioSet", repo_type="dataset",
                               filename="data/bal_train09.tar")
    with tarfile.open(tar_path) as t:
        t.extractall("audioset")
    os.makedirs("audioset_16k", exist_ok=True)
    audioset_dataset = datasets.Dataset.from_dict(
        {"audio": [str(i) for i in Path("audioset/audio").glob("**/*.flac")]}
    ).cast_column("audio", datasets.Audio(sampling_rate=16000))
    for row in tqdm(audioset_dataset, desc="AudioSet"):
        name = row['audio']['path'].split('/')[-1].replace(".flac", ".wav")
        scipy.io.wavfile.write(f"./audioset_16k/{name}", 16000,
                               (row['audio']['array']*32767).astype(np.int16))
assert len(os.listdir("audioset_16k")) > 100, "AudioSet toujours vide !"
print(f"✓ AudioSet : {len(os.listdir('audioset_16k'))} fichiers")
```

---

## Étape 2 — Ajouter les échantillons réels (nouvelle cellule, avant la cellule 3)

Uploader `wake_samples_xvf3800/` dans Colab (ou via Google Drive), puis :

```python
# ── v3 : injection des échantillons RÉELS captés par le XVF3800 ──
import shutil, os
from pathlib import Path

REAL = "./wake_samples_xvf3800"          # dossier uploadé
model_dir = "./my_custom_model/dis_aura"

# 1. Positifs réels → positive_train (dupliqués ×8 : ~60 prises réelles doivent
#    peser face à 20 000 synthétiques ; la duplication est ensuite diversifiée
#    par l'augmentation RIR/bruit).
os.makedirs(f"{model_dir}/positive_train", exist_ok=True)
n = 0
for wav in Path(f"{REAL}/positive_real").glob("*.wav"):
    for k in range(8):
        shutil.copy(wav, f"{model_dir}/positive_train/real{k}_{wav.name}")
        n += 1
print(f"✓ {n} positifs réels injectés (×8)")

# 2. Ambiance réelle du bureau → fond sonore d'augmentation (le modèle apprend
#    LE bruit de TA pièce à travers TON micro).
os.makedirs("./background_real", exist_ok=True)
for wav in Path(f"{REAL}/background_real").glob("*.wav"):
    shutil.copy(wav, f"./background_real/{wav.name}")
print(f"✓ {len(os.listdir('./background_real'))} tranches d'ambiance réelle")
```

Et dans la **cellule 3**, ajouter le fond réel (avec un poids fort) :

```python
config["background_paths"] = ["./audioset_16k", "./fma", "./background_real"]
config["background_paths_duplication_rate"] = [1, 1, 3]   # l'ambiance réelle pèse ×3
```

---

## Étape 2b — Positifs ElevenLabs : voix réalistes + PROSODIE variée (nouvelle cellule)

Vulnérabilité identifiée sur la v2 : le modèle n'accroche qu'une seule « façon de dire »
(un seul souffle, pas de pause entre les mots). Correctif : générer les positifs avec
des **variantes de pause** (la ponctuation pilote la pause chez les TTS) × des **vitesses**
× des **dizaines de voix ElevenLabs** (accents France/Canada/Belgique/Afrique selon les
voix du compte). La clé API : via un secret Colab, JAMAIS en dur dans le notebook.

```python
# ── v3 : positifs ElevenLabs (voix réalistes, prosodie variée) ──
import os, requests, subprocess, itertools, random, shutil
from pathlib import Path
from getpass import getpass

ELEVEN_KEY = os.environ.get("ELEVENLABS_API_KEY") or getpass("Clé ElevenLabs : ").strip()
HDRS = {"xi-api-key": ELEVEN_KEY}

# 1. Voix du compte (ajouter des voix FR variées depuis la Voice Library au préalable)
voices = requests.get("https://api.elevenlabs.io/v1/voices", headers=HDRS).json()["voices"]
VOICE_IDS = [v["voice_id"] for v in voices]
print(f"{len(voices)} voix :", [v["name"] for v in voices])

# 2. Variantes prosodiques — la ponctuation contrôle L'ESPACE ENTRE LES MOTS
TEXTS = ["dis aura", "dis aura.", "Dis Aura !", "dis, aura", "dis… aura", "dis. aura"]
SPEEDS = [0.8, 0.95, 1.1]          # lent / normal / rapide

OUT = Path("./eleven_positive"); OUT.mkdir(exist_ok=True)
N_TARGET = 1500                     # ~12k caractères — négligeable sur le quota
combos = list(itertools.product(VOICE_IDS, TEXTS, SPEEDS)) * 50
random.shuffle(combos)
made = len(list(OUT.glob("*.wav")))
for voice_id, text, speed in combos:
    if made >= N_TARGET: break
    body = {"text": text, "model_id": "eleven_multilingual_v2",
            "voice_settings": {"stability": random.uniform(0.3, 0.7),
                               "similarity_boost": 0.8,
                               "style": random.uniform(0.0, 0.4),
                               "speed": speed}}
    r = requests.post(f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
                      headers={**HDRS, "Content-Type": "application/json"},
                      json=body, timeout=60)
    if r.status_code == 422:        # modèle/voix sans réglage speed → retente sans
        body["voice_settings"].pop("speed", None)
        r = requests.post(f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
                          headers={**HDRS, "Content-Type": "application/json"},
                          json=body, timeout=60)
    if r.status_code != 200:
        print("skip", voice_id, r.status_code); continue
    mp3 = OUT / f"e_{made:05d}.mp3"; wav = OUT / f"e_{made:05d}.wav"
    mp3.write_bytes(r.content)
    subprocess.run(["ffmpeg", "-y", "-i", str(mp3), "-ar", "16000", "-ac", "1", str(wav)],
                   capture_output=True)
    mp3.unlink(); made += 1
    if made % 100 == 0: print(f"{made}/{N_TARGET}")
print(f"✓ {made} clips ElevenLabs")

# 3. → dans positive_train (avec les synthétiques Piper/edge et les réels)
mdir = "./my_custom_model/dis_aura/positive_train"
os.makedirs(mdir, exist_ok=True)
for w in OUT.glob("*.wav"): shutil.copy(w, f"{mdir}/{w.name}")
print("✓ injectés dans positive_train")
```

Et pour que **Piper/edge-tts varient aussi la prosodie**, dans la cellule 3 :

```python
config["target_phrase"] = ["dis aura", "dis, aura", "dis… aura"]   # pause courte/moyenne/longue
```

⚠️ Garder les pauses ≤ ~0,8 s (des clips trop longs feraient gonfler la fenêtre
d'analyse `total_length` du modèle — elle se calcule sur la durée médiane des clips).

---

## Étape 3 — Négatifs difficiles (cellule 3, avant l'écriture du YAML)

Le terrain a montré les confusions réelles (« Dis au rat test » a été transcrit tel
quel par Deepgram) :

```python
config["custom_negative_phrases"] = [
    "au rat", "au rat test", "dis au revoir", "aura", "d'or à",
    "il aura", "elle aura", "on aura", "aurait", "docteur ah",
    "dis-moi", "dis donc", "et alors", "tiens ça alors",
]
```

---

## Étape 4 — Entraîner (cellule 3 inchangée pour le reste)

Mêmes paramètres que la v2 (20 000 exemples, 50 000 steps, pénalité 1500).
Le nom du modèle reste `dis_aura` → renommer l'ONNX exporté en `Aura_test_v3.onnx`.

## Étape 5 — Déployer et A/B tester sur le Pi

```bash
# copier le modèle sur le Pi puis :
scp Aura_test_v3.onnx pi@<ip>:~/aura/raspberry/openwake/
# ~/.aura/env :  ACTIVATE_MODEL=Aura_test_v3.onnx
```

Validation : refaire la série 5 × « Dis Aura » à 1/3/5 m → la télémétrie `wake_events`
compare v2/v3 sur les mêmes conditions (scores trigger + near-miss). Critère de
bascule : moyenne ≥ 0.75 ET plus aucun vrai essai < 0.4. Ensuite seulement,
envisager de remonter `ACTIVATE_THRESHOLD` 0.35 → 0.5 (moins de faux positifs).

## Pourquoi PAS d'autres changements

- Le **gain runtime ×6** (AUDIO_INPUT_GAIN) normalise déjà le niveau — pas besoin
  d'augmentation de niveau spécifique.
- Le notebook garde ses 7 voix TTS : elles restent la masse d'apprentissage ; les
  échantillons réels font l'adaptation de domaine (micro + pièce + tes locuteurs).
- Ne PAS toucher `stop_aura.onnx` pour l'instant : la télémétrie 16/07 le montre
  excellent sur le nouveau micro (0.88-0.98 sur les vrais stops).
