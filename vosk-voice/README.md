# Vosk Voice Module

Modulo modulare per integrare il riconoscimento vocale offline (Vosk) in qualsiasi applicazione Flask.

---

## Struttura del Progetto

```
vosk-voice/
├── server.py           # Server originale (monolitico)
├── index.html           # Frontend originale
├── voice_module.py      # 📦 MODULO RIUTILIZZABILE
├── example_app.py       # 📝 Esempio di integrazione
├── requirements.txt     # Dipendenze
└── model-it/            # Modello Vosk italiano (da estrarre)
```

---

## Installazione

### 1. Installa le dipendenze
```bash
pip install flask flask-sock vosk
```

### 2. Scarica il modello Vosk
```bash
# Scarica il modello italiano small
wget https://alphacephei.com/vosk/models/vosk-model-small-it-0.22.zip

# Estrai e rinomina
unzip vosk-model-small-it-0.22.zip
mv vosk-model-small-it-0.22 model-it
```

---

## Come Integrare in una App Flask Esistente

### Passo 1: Copia il modulo
Copia `voice_module.py` nella cartella del tuo progetto.

### Passo 2: Modifica il tuo file principale

```python
from flask import Flask
from voice_module import init_voice_module, voice_bp

app = Flask(__name__)

# Inizializza il modulo vocale
init_voice_module(
    app,
    model_path="model-it",       # Percorso al modello
    on_transcription=my_callback # Callback opzionale
)

# Registra le rotte API (opzionale)
app.register_blueprint(voice_bp, url_prefix="/voice")

# Le tue rotte esistenti continuano a funzionare...
@app.route("/api/users")
def get_users():
    return {"users": []}

if __name__ == "__main__":
    app.run(port=8000)
```

### Passo 3: Aggiungi il widget HTML

Copia questo snippet nel tuo template:

```html
<!-- Widget Vocale -->
<div id="voice-widget">
    <div id="voice-status">
        <div class="dot" id="dot"></div>
        <span id="statusText">Scollegato</span>
    </div>
    <div id="transcripts">
        <div id="history"></div>
        <div id="livePreview" class="msg partial" style="display:none;"></div>
    </div>
    <div class="controls">
        <button id="startBtn">🎤 Avvia Ascolto</button>
        <button id="stopBtn">⏹ Interrompi</button>
    </div>
</div>

<script>
// Vedi example_app.py per il codice JavaScript completo
</script>
```

---

## API del Modulo

### `init_voice_module(app, model_path, on_transcription, on_partial)`

Inizializza il modulo vocale.

| Parametro | Tipo | Descrizione |
|-----------|------|-------------|
| `app` | Flask app | La tua applicazione Flask |
| `model_path` | str | Percorso alla cartella del modello Vosk |
| `on_transcription` | callable | Callback per testo finale: `func(text, session_id)` |
| `on_partial` | callable | Callback per testo parziale: `func(text, session_id)` |

### WebSocket Endpoint

Il modulo crea automaticamente l'endpoint `/ws` per le connessioni WebSocket.

**Messaggi ricevuti dal client:**
```json
{"text": "ciao come va", "is_final": true, "session_id": 12345}
{"text": "ciao come...", "is_final": false, "session_id": 12345}
```

### Blueprint Routes (opzionali)

| Rotta | Metodo | Descrizione |
|-------|--------|-------------|
| `/voice/status` | GET | Verifica se il modulo è attivo |

---

## Esempio: Comandi Vocali

```python
def handle_voice_command(text, session_id):
    """Gestisce comandi vocali riconosciuti."""
    text = text.lower()
    
    # Comandi personalizzati
    if "accendi luce" in text:
        # Accendi_luce()  # La tua funzione
        print("Luce accesa!")
    
    elif "spegni luce" in text:
        # spegni_luce()
        print("Luce spenta!")
    
    elif "cerca" in text:
        # Estrai il termine di ricerca
        query = text.replace("cerca", "").strip()
        # cerca_in_db(query)
        print(f"Cercando: {query}")

init_voice_module(app, "model-it", on_transcription=handle_voice_command)
```

---

## Esempio: Salvare in Database

```python
from models import db, Transcription

def save_transcription(text, session_id):
    """Salva ogni trascrizione nel database."""
    t = Transcription(
        session_id=session_id,
        text=text,
        user_id=current_user.id  # Se usi Flask-Login
    )
    db.session.add(t)
    db.session.commit()

init_voice_module(app, "model-it", on_transcription=save_transcription)
```

---

## Esempio: Integrare con AI (ChatGPT, ecc.)

```python
import openai

def process_with_ai(text, session_id):
    """Invia il testo a un'API AI per elaborazione."""
    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[
            {"role": "user", "content": text}
        ]
    )
    ai_reply = response.choices[0].message.content
    print(f"AI: {ai_reply}")

init_voice_module(app, "model-it", on_transcription=process_with_ai)
```

---

## Test

Esegui l'app di esempio:

```bash
python example_app.py
```

Apri http://127.0.0.1:8000 nel browser e clicca "Avvia Ascolto".

---

## File da Copiare

Per integrare in un altro progetto, copia solo:

1. ✅ `voice_module.py` - Il modulo Python
2. ✅ `model-it/` - La cartella del modello Vosk
3. ✅ Il codice HTML/JavaScript del widget (da `example_app.py`)

---

## Requisiti Audio

| Parametro | Valore |
|-----------|--------|
| Sample Rate | 16000 Hz |
| Formato | PCM Int16 |
| Canali | Mono |

Il client JavaScript converte automaticamente l'audio del microfono nel formato corretto.