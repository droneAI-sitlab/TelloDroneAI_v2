# TelloAI – Drone Control Dashboard

Applicazione Flask per il controllo e il monitoraggio del drone DJI Tello tramite interfaccia web. Integra acquisizione video in tempo reale, elaborazione frame remota via OCR, e esecuzione automatica di comandi tramite modelli AI (Ollama).

---

## Panoramica

**TelloDroneAI_v2** è un sistema intelligente che permette di gestire un drone DJI Tello tramite una comoda dashboard web, combinando il volo manuale con comandi vocali in italiano offline. Grazie all'integrazione di Intelligenza Artificiale e computer vision, il drone non solo trasmette video in tempo reale e permette di scattare foto o registrare video, ma è anche in grado di analizzare l'ambiente circostante, leggere testi (OCR) e sfruttare modelli linguistici (Ollama) per "comprendere" e descrivere ciò che vede.

---

## Setup

### 1. Prerequisiti

**Sistema operativo:** Windows (per il modulo `wifi.py` che usa `netsh`)

**Linguaggio:** Python 3.11.8

**Hardware:** 
- PC con WiFi adapter
- Drone DJI Tello con batteria carica
- (Opzionale) Server OCR remoto o Ollama server locale

### 2. Installazione dipendenze

```bash
# Clona o scarica il progetto
cd TelloDroneAI_v2

# Crea ambiente virtuale Python
python -m venv venv

# Attiva ambiente
.\venv\Scripts\activate

# Installa dipendenze
pip install -r requirements.txt
```

### 3. Configurazione `.env`

Crea un file `.env` nella radice del progetto con i seguenti parametri:

```ini
# ─────────────────────────────────────────────────────────────────────
# FLASK - Server web
# ─────────────────────────────────────────────────────────────────────
FLASK_HOST=0.0.0.0
FLASK_PORT=5000
FLASK_DEBUG=True
SECRET_KEY=dev-secret-key-change-in-prod

# ─────────────────────────────────────────────────────────────────────
# DRONE - Connessione Tello
# ─────────────────────────────────────────────────────────────────────
DRONE_IP=192.168.10.1
DRONE_PORT=8889
VIDEO_PORT=11111
DRONE_WIFI_SSID=TELLO-XXXXXX        # Sostituisci con SSID del tuo drone
WIFI_CONNECT_TIMEOUT=15             # secondi

# ─────────────────────────────────────────────────────────────────────
# FRAME PROCESSING - Elaborazione video
# ─────────────────────────────────────────────────────────────────────
FRAME_WIDTH=960
FRAME_HEIGHT=720
JPEG_QUALITY=80
ENABLE_CONTRAST=True
CONTRAST_ALPHA=1.05
CONTRAST_BETA=2
TARGET_FPS=30.0

# ─────────────────────────────────────────────────────────────────────
# OCR - Server remoto (opzionale per test senza OCR)
# ─────────────────────────────────────────────────────────────────────
OCR_ENABLED=True
OCR_SERVER_URL=http://192.168.1.100:8000    # IP server OCR
OCR_TIMEOUT=30                              # secondi per risposta
OCR_INTERVAL_SECONDS=5                      # min intervallo tra invii
OCR_JPEG_QUALITY=85
OCR_MIN_CONFIDENCE=0.0

# ─────────────────────────────────────────────────────────────────────
# VOICE - Filtro volume microfono per Vosk
# ─────────────────────────────────────────────────────────────────────
VOICE_MIN_INPUT_RMS=0.02            # soglia minima del volume prima di inviare audio a Vosk
VOICE_MIN_FINAL_CHARS=4             # lunghezza minima del testo finale riconosciuto
VOICE_MIN_AVG_CONFIDENCE=0.82       # confidenza media minima per accettare la trascrizione
VOICE_DEDUPE_WINDOW_SECONDS=2.2     # finestra anti-duplicato per la stessa frase

# ─────────────────────────────────────────────────────────────────────
# OLLAMA - Modello AI per processamento OCR (opzionale)
# ─────────────────────────────────────────────────────────────────────
OLLAMA_URL=http://127.0.0.1:11434
OLLAMA_MODEL=functiongemma_tello_current
OLLAMA_FUNCTIONGEMMA_MODEL=functiongemma_tello_current
OLLAMA_TIMEOUT=30                   # secondi
OLLAMA_TEMPERATURE=0                # 0=deterministico, 1=creativo

# ─────────────────────────────────────────────────────────────────────
# COMMAND BUFFER - Esecuzione comandi da AI
# ─────────────────────────────────────────────────────────────────────
COMMAND_BUFFER_DELAY_SECONDS=2.0
COMMAND_BUFFER_MAX_SIZE=50
KEEPALIVE_COOLDOWN_SECONDS=5.0            # keepalive quando buffer e' vuoto
KEEPALIVE_USE_NO_RESPONSE=False           # usa send_command_without_return("keepalive")

# ─────────────────────────────────────────────────────────────────────
# LOGGING - Log in memoria
# ─────────────────────────────────────────────────────────────────────
LOG_MAX_ENTRIES=100
```

### 4. Server richiesti (opzionali ma consigliati)

#### Server OCR (per riconoscimento testo)

Per usare l'OCR, è necessario un server remoto che esponga l'endpoint `/predict` e accetti POST multipart con chiave `image` (JPEG binario). 

Esempio configurazione per **EasyOCR** in FastAPI:

```python
# ocr_server.py (esempio minimalista)
from fastapi import FastAPI, UploadFile
from fastapi.responses import JSONResponse
import io
import cv2
import numpy as np
import easyocr

app = FastAPI()
reader = easyocr.Reader(['it', 'en'])

@app.post("/predict")
async def predict(image: UploadFile):
    img_data = await image.read()
    img_array = np.frombuffer(img_data, dtype=np.uint8)
    img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
    
    results = reader.readtext(img)
    return {
        "results": [
            {"text": text, "confidence": conf}
            for _, text, conf in results
        ]
    }

# Avvia con: uvicorn ocr_server:app --host 0.0.0.0 --port 8000
```

#### Ollama (per AI e generazione comandi)

```bash
# Installa Ollama da https://ollama.ai/

# Scarica modello FunctionGemma
ollama pull functiongemma

# Avvia server (default porta 11434)
ollama serve
```

---

## Avvio

```bash
# Assicurati di avere l'ambiente attivato
.\venv\Scripts\activate

# Avvia l'app Flask
python app.py
```

Output atteso:
```
 * Running on http://0.0.0.0:5000
 * Press CTRL+C to quit
```

Apri il browser su `http://localhost:5000` per accedere alla dashboard.

---

## Utilizzo

### Dashboard web

La dashboard è divisa in due sezioni:

**Sinistra (75% larghezza):**
- Video stream in tempo reale dal drone
- Barra batteria animata (aggiornata ogni 10 secondi)

**Destra (25% larghezza):**
- **WiFi toggle** – Connette/disconnette il drone (netsh)
- **Stream toggle** – Avvia/ferma lo stream video (dipende da WiFi)
- **OCR toggle** – Attiva/disattiva invio frame al server OCR
- **Vosk Vocale** – Controllo vocale offline in italiano con libreria Vosk
- **Foto/Video** – Controlli per acquisire media in locale
- **Casella messaggi** – Invia comandi testuali al drone
- **Log in tempo reale** – Mostra debug e feedback delle azioni

### Flusso di utilizzo

1. **Connetti WiFi**
   - Premi il toggle "WiFi"
   - L'app esegue `netsh wlan connect` sul SSID configurato in `.env`

2. **Avvia stream video**
   - Premi il toggle "Stream"
   - Il drone invia i frame MJPEG su UDP porta 11111
   - La dashboard mostra il video in tempo reale

3. **Invia comandi**
   - **Manualmente:** Scrivi nell'input testuale (es. "takeoff", "move_forward 50") e premi invio o clicca il bottone
  - **Vocale (Vosk):** Usa l'apposito modulo integrato offline per impartire comandi naturali in italiano; la sensibilità d'ingresso si regola con `VOICE_MIN_INPUT_RMS` nel `.env`
   - **Automatico:** Attiva "OCR" per inviare frame al server remoto e generare azioni tramite Ollama

4. **Monitora l'esecuzione**
   - La sezione "Log" mostra tutti gli eventi (connessioni, comandi, errori)

### Comandi supportati

La tabella completa è in [drone/command_executor.py](drone/command_executor.py#L50), ma i principali sono:

| Comando | Argomento | Descrizione |
|---------|-----------|-------------|
| `takeoff` | — | Decolla |
| `land` | — | Atterra |
| `emergency` | — | Arresto motori (emergenza) |
| `move_forward` | cm (20-500) | Avanza |
| `move_back` | cm (20-500) | Arretra |
| `move_left` | cm (20-500) | Trasla sinistra |
| `move_right` | cm (20-500) | Trasla destra |
| `move_up` | cm (20-500) | Sale |
| `move_down` | cm (20-500) | Scende |
| `rotate_clockwise` | ° (1-360) | Ruota oraria |
| `rotate_counter_clockwise` | ° (1-360) | Ruota antioraria |
| `set_speed` | cm/s (10-100) | Imposta velocità movimento |

---

## Logica generale

### Architettura alto livello

```
┌─────────────────────────────────────────────────────────────────┐
│                    BROWSER (Frontend)                            │
│  Dashboard HTML/CSS/JS con video stream + controlli              │
└──────────────────────┬──────────────────────────────────────────┘
                       │ HTTP + polling state/logs
┌──────────────────────▼──────────────────────────────────────────┐
│                  app.py (Flask Backend)                          │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │ Route Handler (POST /api/*)                               │ │
│  │  - toggle_wifi: gestisce connessione WiFi                 │ │
│  │  - toggle_stream: avvia/ferma acquisizione video         │ │
│  │  - send_message: accoda comandi da UI/vocale              │ │
│  │  - video_feed: stream MJPEG generato in tempo reale      │ │
│  └────────────────────────────────────────────────────────────┘ │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │ State Lock (thread-safe)                                  │ │
│  │  - wifi_connected, stream_active, battery, logs          │ │
│  └────────────────────────────────────────────────────────────┘ │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │ Daemon Threads                                            │ │
│  │  - Battery poll (ogni 10s)                               │ │
│  │  - Command buffer worker (esegue comandi in queue)       │ │
│  └────────────────────────────────────────────────────────────┘ │
└──────────┬──────────────────────────────┬──────────────────────┘
           │                              │
           │ WiFi (netsh)                 │ Socket UDP/TCP
           │                              │
    ┌──────▼──────┐              ┌────────▼─────────────────────┐
    │   Windows   │              │   Drone DJI Tello           │
    │  netsh wlan │◄─────────────│  Tello SDK (djitellopy)    │
    └─────────────┘              │                             │
                                 │  Stream MJPEG UDP :11111    │
                                 │  Commands TCP :8889         │
                                 │  Battery query              │
                                 └─────────────────────────────┘
```

### Flusso processamento frame

```
DroneReader
    │
    ├─ djitellopy.Tello.get_frame_read()
    │  │ (thread daemon interno)
    │  └─► frame_reader.py::get_frame() ──► numpy BGR array
    │
    │
FrameProcessor
    │
    ├─ Resize (960x720)
    ├─ Normalizzazione contrasto (OpenCV convertScaleAbs)
    ├─ Hook AI (placeholder per future espansioni)
    └─► cv2.imencode() ──► JPEG bytes
              │
              │
        app.py::video_feed generator
              │
              ├─► OCRSender (if OCR_ENABLED)
              │   │
              │   ├─ Timer check (intervallo OCR_INTERVAL_SECONDS)
              │   ├─ POST /predict al server remoto
              │   ├─ Parse JSON risposta
              │   └─► enqueue_executor_commands()
              │       │
              │       └─► CommandExecutor.run() ──► Drone SDK
              │
              └─► MJPEG boundary + headers
                  ──► client browser (video streaming)
```

### Flusso comandi

```
Utente (UI / Vocale Vosk / Automatico OCR)
    │
    ├─► OCRSender.send_frame() + ollama_client.call_ollama()
    │   ou
    ├─► app.py::send_message (input testuale)
    │   ou
    └─► Vosk Voice Module (vocale offline)
              │
              ▼
        enqueue_executor_commands(list[(name, arg)])
              │
              ▼
        command_buffer.Queue (Priority Queue)
              │
              ▼
        _command_buffer_worker() daemon
              │
              ├─ Get comando dalla queue
              ├─ CommandExecutor.run(name, arg)
              │  │
              │  └─ Lookup COMMAND_TABLE[name]
              │     │
              │     ├─ Validazione argomenti (min/max)
              │     └─ Call djitellopy.Tello SDK
              │
              ├─ COMMAND_BUFFER_DELAY_SECONDS (delay tra comandi)
              │
              └─► add_log("Eseguito: ...") + print
                  ──► UI log (polling ogni 1s)
```

### Moduli e responsabilità

| Modulo | Responsabilità | Note |
|--------|----------------|------|
| **app.py** | Entry point Flask, route handler, state management thread-safe | Core dell'app |
| **drone/wifi.py** | Connessione WiFi Windows via `netsh wlan` | Specifico del SO |
| **drone/frame_reader.py** | Wrapper djitellopy con acquisizione frame e stream control | Gestisce lifecycle drone |
| **drone/frame_processor.py** | Pipeline OpenCV: resize, contrasto, hook AI, encoding JPEG | Elaborazione frame |
| **drone/media_capture.py** | Gestione salvataggio foto e registrazione video su disco | Multimedia |
| **drone/ocr_sender.py** | Invio frame remoto con timer, parsing JSON, enqueue comandi | Integration point OCR |
| **drone/ollama_client.py** | Client HTTP verso Ollama, chat API, parse FunctionGemma output | Integration point AI |
| **drone/command_executor.py** | Tabella, mapper e coda con **priorità** (es. emergenza) per comandi al drone | Command dispatcher |
| **vosk-voice/** | Riconoscimento vocale offline in italiano con modello Vosk | Voice commands |
| **templates/index.html** | Dashboard HTML, layout, form input, video tag | UI |
| **static/css/style.css** | Tema verde, componenti, animazioni | Styling |
| **static/js/main.js** | Polling state, toggle handler, log rendering | Logica frontend |

---

## Dettaglio API REST

### GET `/`
Ritorna la dashboard HTML principale.

### GET `/video_feed`
Stream MJPEG continuo del drone (Content-Type: `multipart/x-mixed-replace`).

```bash
curl http://localhost:5000/video_feed --output video.mjpeg
```

### POST `/api/toggle_wifi`
Connette/disconnette WiFi al drone.

**Request:** `{}`  
**Response:**
```json
{
  "success": true,
  "connected": true,
  "message": "WiFi connesso a TELLO-XXXXXX"
}
```

### POST `/api/toggle_stream`
Avvia/ferma stream video.

**Request:** `{}`  
**Response:**
```json
{
  "success": true,
  "active": true,
  "message": "Stream avviato"
}
```

### POST `/api/send_message`
Invia comando testuale (es. "takeoff", "move_forward 50").

**Request:** `{ "message": "takeoff" }`  
**Response:**
```json
{
  "success": true,
  "message": "Comando accodato: takeoff"
}
```

### GET `/api/status`
Ritorna lo stato corrente (toggle + batteria).

**Response:**
```json
{
  "wifi_connected": true,
  "stream_active": true,
  "battery": 85
}
```

### GET `/api/logs`
Ritorna lista ultimi log.

**Response:**
```json
[
  {"time": "15:23:45", "level": "info", "message": "Connessione WiFi → TELLO-XXXXXX …"},
  {"time": "15:23:48", "level": "success", "message": "WiFi connesso a TELLO-XXXXXX"},
  ...
]
```

### GET `/api/battery`
Ritorna solo il livello batteria (0-100%).

**Response:**
```json
{
  "battery": 85
}
```

---

## Troubleshooting

| Problema | Soluzione |
|----------|-----------|
| **"WiFi non si connette"** | Verifica `DRONE_WIFI_SSID` in `.env` corrisponda all'SSID del drone. Controlla che il WiFi del PC sia attivo. |
| **"Stream fallisce (black video)"** | Assicurati di aver premuto WiFi toggle prima di Stream. Verifica che il drone sia acceso e a portata. |
| **"OCR non risponde"** | Controlla che il server OCR sia in ascolto su `OCR_SERVER_URL`. Verifica firewall. |
| **"Ollama non riconosce comandi"** | Assicurati che il modello `functiongemma` sia installato: `ollama pull functiongemma`. |
| **"Comandi non eseguiti"** | Controlla i log nella UI. La queue potrebbe essere piena (aumenta `COMMAND_BUFFER_MAX_SIZE`). |

---

## Struttura del progetto

```text
TelloDroneAI_v2/
├── app.py                          ← Entry point Flask
├── requirements.txt                ← Dipendenze Python
├── .env                            ← Configurazione
├── README.md                       ← Questa documentazione
│
├── drone/                          ← Package moduli drone
│   ├── __init__.py
│   ├── wifi.py                   ← Connessione WiFi
│   ├── frame_reader.py           ← Acquisizione frame
│   ├── frame_processor.py        ← Pipeline OpenCV
│   ├── media_capture.py          ← Foto e registrazione Video
│   ├── ocr_sender.py             ← Endpoint OCR
│   ├── ollama_client.py          ← Integrazione LLM
│   └── command_executor.py       ← Motore comandi e Priority Queue
│
├── vosk-voice/                     ← Riconoscimento vocale
│   ├── voice_module.py           ← Core Vosk
│   └── model-it/                 ← Modello acustico italiano
│
├── tests/                          ← Test unitari e integrazione
│
├── templates/                      ← Template HTML
│   └── index.html                
│
└── static/                         ← Asset frontend
    ├── css/
    │   └── style.css             
    └── js/
        └── main.js               
```

---

## Note di sviluppo

- **Thread-safety:** L'app usa `threading.Lock()` per proteggere `app_state`. Tutte le operazioni su batteria, flag WiFi/stream, log sono sincronizzate.
- **Configurazione:** Tutti i parametri leggibili sono in `.env`. Non mescolare hardcoding in Python.
- **Modularità:** Il package `drone/` è disaccoppiato da Flask. Ogni modulo ha una responsabilità singola e può essere testato isolatamente.
- **OCR opzionale:** L'OCR è completamente opzionale. Se `OCR_ENABLED=False` o il server non risponde, il drone funziona normalmente.

---

## Riferimenti

- **DJI Tello SDK:** [djitellopy v2.5.0](https://github.com/damiafuentes/DJITelloPy)
- **Flask:** [https://flask.palletsprojects.com/](https://flask.palletsprojects.com/)
- **OpenCV:** [https://opencv.org/](https://opencv.org/)
- **Ollama:** [https://ollama.ai/](https://ollama.ai/)
- **Vosk:** [https://alphacephei.com/vosk/](https://alphacephei.com/vosk/)

---

## Come avviare

```bash
# Attiva il virtualenv
.\venv\Scripts\python.exe app.py

# oppure, se la policy PowerShell lo consente
.\venv\Scripts\Activate.ps1
python app.py
```

L'app è disponibile su `http://localhost:5000` (porta configurabile in `.env`).

