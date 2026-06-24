#!/usr/bin/env python3
"""Test standalone de la LED RGB KY-016 sur le GPIO du Raspberry Pi.

Câblage (BCM) — ÉDITE ces 3 lignes si tu as branché sur d'autres broches :
    R  -> GPIO13 (broche physique 33)
    G  -> GPIO19 (broche physique 35)
    B  -> GPIO26 (broche physique 37)
    GND-> GND    (broche physique 39)

Prérequis :  pip install gpiozero lgpio
Lancer    :  python device/test_led.py
Le KY-016 a des résistances intégrées (151 = 150Ω) → branchement direct, OK.
"""
from time import sleep

RED_PIN = 13
GREEN_PIN = 19
BLUE_PIN = 26

try:
    from gpiozero import RGBLED
except ImportError:
    raise SystemExit("gpiozero manquant → pip install gpiozero lgpio")

# active_high=True : KY-016 = cathode commune (broche GND). Si la LED est allumée
# quand elle devrait être éteinte (logique inversée), mets active_high=False.
led = RGBLED(red=RED_PIN, green=GREEN_PIN, blue=BLUE_PIN, active_high=True)

COLORS = [
    ("Rouge",  (1, 0, 0)),
    ("Vert",   (0, 1, 0)),
    ("Bleu",   (0, 0, 1)),
    ("Blanc",  (1, 1, 1)),
    ("Cyan",   (0, 1, 1)),
    ("Violet", (1, 0, 1)),
    ("Orange", (1, 0.4, 0)),
]

print("Test LED — regarde la couleur annoncée vs la couleur réelle.")
print("(Ctrl+C pour arrêter)\n")
try:
    for name, rgb in COLORS:
        print(f"  → {name}")
        led.color = rgb
        sleep(1.2)
    print("\nFondu...")
    for _ in range(2):
        for b in list(range(0, 11)) + list(range(10, -1, -1)):
            led.color = (0, 0, b / 10)
            sleep(0.05)
finally:
    led.off()
    print("\nFini.")
    print("• Si AUCUNE lumière → vérifie le câblage (R/G/B/GND) et les broches ci-dessus.")
    print("• Si les couleurs sont MÉLANGÉES (ex: 'Rouge' allume du vert) → échange les fils R/G/B")
    print("  (ou ajuste RED_PIN/GREEN_PIN/BLUE_PIN dans ce fichier).")
