import json
import re
from vosk import Model, KaldiRecognizer
from flask import Flask, send_file
from flask_sock import Sock
from djitellopy import Tello

app = Flask(__name__)
sock = Sock(app)

# ── Vosk ──────────────────────────────────────────────────────────────────
print("Caricamento modello italiano...")
try:
    model = Model("model-it")
    print("SUCCESS: Vosk pronto.")
except Exception as e:
    print(f"Errore: {e}")
    exit(1)

# ── Tello ─────────────────────────────────────────────────────────────────
print("Connessione Tello...")
try:
    tello = Tello()
    tello.connect()
    print(f"Tello connesso! Batteria: {tello.get_battery()}%")
except Exception as e:
    print(f"Errore connessione Tello: {e}")
    tello = None

# ── Interprete comandi ────────────────────────────────────────────────────
DIREZIONI = {
    "sinistra": "left",
    "destra":   "right",
    "avanti":   "forward",
    "indietro": "back",
    "su":       "up",
    "giù":      "down",
    "giu":      "down",
    "sopra":    "up",
    "sotto":    "down",
}

def estrai_distanza(testo, default=50):
    numero = re.search(r"\d+", testo)
    distanza = int(numero.group()) if numero else default
    return max(20, min(500, distanza))

def esegui_comando_tello(testo):
    if tello is None:
        print("[Tello] Drone non connesso, comando ignorato.")
        return

    testo = testo.lower().strip()

    try:
        if any(p in testo for p in ["decolla", "decollo", "vola", "alzati"]):
            tello.takeoff()
            return

        if any(p in testo for p in ["atterra", "atterraggio", "scendi"]):
            tello.land()
            return

        if any(p in testo for p in ["stop", "ferma", "emergenza", "basta"]):
            tello.emergency()
            return

        if "flip" in testo or "capriola" in testo:
            if "sinistra" in testo:
                tello.flip_left()
            elif "destra" in testo:
                tello.flip_right()
            elif "indietro" in testo:
                tello.flip_back()
            else:
                tello.flip_forward()
            return

        if any(p in testo for p in ["ruota", "gira"]):
            gradi = estrai_distanza(testo, default=90)
            if "sinistra" in testo or "antiorario" in testo:
                tello.rotate_counter_clockwise(gradi)
            else:
                tello.rotate_clockwise(gradi)
            return

        for parola, comando in DIREZIONI.items():
            if parola in testo:
                distanza = estrai_distanza(testo)
                if comando == "left":
                    tello.move_left(distanza)
                elif comando == "right":
                    tello.move_right(distanza)
                elif comando == "forward":
                    tello.move_forward(distanza)
                elif comando == "back":
                    tello.move_back(distanza)
                elif comando == "up":
                    tello.move_up(distanza)
                elif comando == "down":
                    tello.move_down(distanza)
                return

    except Exception as e:
        print(f"[Tello] Errore esecuzione comando: {e}")

# ── Routes ────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_file('index.html')

@sock.route("/ws")
def voice_websocket(ws):
    rec = KaldiRecognizer(model, 16000)
    conn_id = id(ws)
    print(f"[WebSocket] Nuova connessione: {conn_id}")

    try:
        while True:
            audio_data = ws.receive()
            if audio_data is None:
                break

            if isinstance(audio_data, str):
                continue

            if rec.AcceptWaveform(audio_data):
                result = json.loads(rec.Result())
                text = result.get('text', '').strip()
                if text:
                    print(f"[FINAL] {text}")
                    ws.send(json.dumps({"text": text, "is_final": True}))
                    esegui_comando_tello(text)
            else:
                partial = json.loads(rec.PartialResult())
                text = partial.get('partial', '').strip()
                if text:
                    ws.send(json.dumps({"text": text, "is_final": False}))

    except Exception as e:
        print(f"[WebSocket] Connessione chiusa ({conn_id}): {e}")
    finally:
        print(f"[WebSocket] Disconnesso: {conn_id}")

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=False, use_reloader=False)