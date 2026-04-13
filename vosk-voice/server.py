import json
from vosk import Model, KaldiRecognizer
from flask import Flask, send_file
from flask_sock import Sock

app = Flask(__name__)
sock = Sock(app)

# Caricamento Modello Vosk (Cartella: 'model-it')
print("Caricamento modello italiano...")
try:
    model = Model("model-it")
    print("SUCCESS: Vosk pronto.")
except Exception as e:
    print(f"Errore: {e}")
    exit(1)

@app.route("/")
def index():
    return send_file('index.html')

@sock.route("/ws")
def voice_websocket(ws):
    # Crea un nuovo recognizer per ogni connessione
    rec = KaldiRecognizer(model, 16000)
    conn_id = id(ws)
    print(f"[WebSocket] Nuova connessione: {conn_id}")

    try:
        while True:
            # Ricevi chunk audio PCM (Int16, 16kHz, mono)
            audio_data = ws.receive()
            if audio_data is None:
                break

            # Se arriva come stringa (testo), skippa
            if isinstance(audio_data, str):
                continue

            # Processa audio con Vosk
            if rec.AcceptWaveform(audio_data):
                result = json.loads(rec.Result())
                text = result.get('text', '').strip()
                if text:
                    print(f"[FINAL] {text}")
                    ws.send(json.dumps({"text": text, "is_final": True}))
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