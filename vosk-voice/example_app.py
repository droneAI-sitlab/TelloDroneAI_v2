"""
Esempio di applicazione Flask che integra il modulo vocale.
Questo file mostra come aggiungere il riconoscimento vocale
a una qualsiasi app Flask esistente in modo modulare.
"""

from flask import Flask, render_template_string
from voice_module import init_voice_module, voice_bp

app = Flask(__name__)

# ============================================================
# 1) CALLBACK PERSONALIZZATI (opzionali)
#    Qui puoi reagire al testo riconosciuto dal microfono
# ============================================================

def on_text_final(text, session_id):
    """Chiamato quando Vosk produce una trascrizione finale."""
    print(f"[APP] L'utente ha detto: '{text}' (sessione: {session_id})")
    
    # Esempi di cose che puoi fare qui:
    # - Salvare la trascrizione in un database
    # - Eseguire un comando vocale (es. "accendi luce")
    # - Inviare il testo a un'API di intelligenza artificiale
    # - Triggerare eventi nella tua applicazione
    
    text_lower = text.lower()
    if "ciao" in text_lower:
        print("[APP] Comando riconosciuto: saluto")
    elif "stop" in text_lower or "ferma" in text_lower:
        print("[APP] Comando riconosciuto: stop")


def on_text_partial(text, session_id):
    """Chiamato mentre l'utente sta ancora parlando."""
    print(f"[APP] Parziale: '{text}'")


# ============================================================
# 2) INIZIALIZZAZIONE DEL MODULO VOCALE
# ============================================================

init_voice_module(
    app,
    model_path="model-it",           # Cartella del modello Vosk
    on_transcription=on_text_final,   # Callback per testo finale
    on_partial=on_text_partial        # Callback per testo parziale
)

# Registra le rotte API del modulo (es. /voice/status)
app.register_blueprint(voice_bp, url_prefix="/voice")


# ============================================================
# 3) LE TUE ROTTE NORMALI (la tua app esistente)
# ============================================================

@app.route("/")
def index():
    """Pagina principale con interfaccia vocale integrata."""
    return render_template_string(TEMPLATE_HTML)

@app.route("/api/data")
def get_data():
    """Esempio di altra rotta della tua app."""
    return {"message": "La tua app funziona normalmente!"}


# ============================================================
# 4) TEMPLATE HTML CON COMPONENTE VOCALE INTEGRATO
# ============================================================

TEMPLATE_HTML = """
<!DOCTYPE html>
<html lang="it">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>La Mia App + Voice</title>
    <style>
        :root {
            --bg: #0f0f0f;
            --card: #1a1a2e;
            --accent: #e94560;
            --green: #00e676;
            --text: #eaeaea;
        }
        body {
            font-family: 'Segoe UI', sans-serif;
            background: var(--bg);
            color: var(--text);
            display: flex;
            flex-direction: column;
            align-items: center;
            padding: 30px;
            margin: 0;
        }
        h1 { margin-bottom: 5px; }
        .subtitle { color: #888; margin-bottom: 30px; }

        /* === Componente Voice === */
        #voice-widget {
            background: var(--card);
            border-radius: 15px;
            padding: 25px;
            width: 100%;
            max-width: 700px;
            border: 1px solid #333;
        }
        #voice-status {
            display: flex;
            align-items: center;
            gap: 8px;
            margin-bottom: 15px;
            font-size: 0.9rem;
        }
        .dot {
            width: 10px; height: 10px;
            background: #555;
            border-radius: 50%;
            transition: all 0.3s;
        }
        .dot.active {
            background: var(--green);
            box-shadow: 0 0 10px var(--green);
        }
        #transcripts {
            height: 200px;
            overflow-y: auto;
            display: flex;
            flex-direction: column;
            gap: 8px;
            margin-bottom: 15px;
            padding-right: 5px;
        }
        .msg {
            padding: 10px 15px;
            background: #16213e;
            border-radius: 8px;
            animation: fadeIn 0.2s;
        }
        .msg.partial {
            color: #888;
            font-style: italic;
            border: 1px dashed #333;
            background: none;
        }
        .controls { display: flex; gap: 10px; }
        button {
            padding: 10px 25px;
            border-radius: 20px;
            border: none;
            font-weight: bold;
            cursor: pointer;
            font-size: 0.95rem;
            transition: 0.2s;
        }
        #startBtn { background: var(--green); color: #000; }
        #stopBtn  { background: #ff5252; color: #fff; display: none; }
        button:hover { transform: scale(1.05); }
        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(4px); }
            to   { opacity: 1; transform: translateY(0); }
        }
        ::-webkit-scrollbar { width: 5px; }
        ::-webkit-scrollbar-thumb { background: #333; border-radius: 5px; }

        /* === Il resto della tua app === */
        #app-content {
            margin-top: 30px;
            width: 100%;
            max-width: 700px;
            text-align: center;
            color: #666;
        }
    </style>
</head>
<body>
    <h1>La Mia App</h1>
    <p class="subtitle">con riconoscimento vocale integrato</p>

    <!-- Widget Vocale (copia questo blocco nella tua pagina) -->
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

    <!-- Il resto della tua applicazione -->
    <div id="app-content">
        <p>Qui continua il resto della tua applicazione...</p>
    </div>

    <!-- ============================================ -->
    <!-- JAVASCRIPT: Componente Voice (riutilizzabile) -->
    <!-- Copia questo script in qualsiasi pagina Flask -->
    <!-- ============================================ -->
    <script>
        (() => {
            // === CONFIGURAZIONE ===
            const WS_ENDPOINT = '/ws';  // Endpoint WebSocket del voice_module

            // === STATO ===
            let socket, audioContext, micStream, processor;

            // === DOM ===
            const startBtn   = document.getElementById('startBtn');
            const stopBtn    = document.getElementById('stopBtn');
            const history    = document.getElementById('history');
            const livePreview = document.getElementById('livePreview');
            const statusText = document.getElementById('statusText');
            const dot        = document.getElementById('dot');

            // === AUDIO: Cattura + conversione ===
            async function startAudio() {
                try {
                    audioContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
                    micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
                    const source = audioContext.createMediaStreamSource(micStream);

                    processor = audioContext.createScriptProcessor(4096, 1, 1);
                    processor.onaudioprocess = (e) => {
                        if (!socket || socket.readyState !== WebSocket.OPEN) return;
                        const input = e.inputBuffer.getChannelData(0);
                        const pcm = new Int16Array(input.length);
                        for (let i = 0; i < input.length; i++) {
                            pcm[i] = Math.max(-1, Math.min(1, input[i])) * 0x7FFF;
                        }
                        socket.send(pcm.buffer);
                    };

                    source.connect(processor);
                    processor.connect(audioContext.destination);
                } catch (err) {
                    console.error('Errore microfono:', err);
                    statusText.innerText = 'Errore Microfono';
                    stopSystem();
                }
            }

            // === CLEANUP ===
            function stopSystem() {
                if (socket) socket.close();
                if (micStream) micStream.getTracks().forEach(t => t.stop());
                if (processor) { processor.disconnect(); processor = null; }
                if (audioContext) { audioContext.close(); audioContext = null; }
                dot.classList.remove('active');
                statusText.innerText = 'Scollegato';
                startBtn.style.display = 'block';
                stopBtn.style.display = 'none';
                livePreview.style.display = 'none';
                livePreview.innerText = '';
            }

            // === START ===
            startBtn.onclick = () => {
                startBtn.style.display = 'none';
                stopBtn.style.display = 'block';
                statusText.innerText = 'Connessione...';

                const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
                socket = new WebSocket(`${protocol}//${location.host}${WS_ENDPOINT}`);

                socket.onopen = () => {
                    statusText.innerText = 'In ascolto';
                    dot.classList.add('active');
                    startAudio();
                };

                socket.onmessage = (event) => {
                    const data = JSON.parse(event.data);
                    if (data.error) {
                        statusText.innerText = 'Errore: ' + data.error;
                        stopSystem();
                        return;
                    }
                    if (data.is_final) {
                        livePreview.style.display = 'none';
                        livePreview.innerText = '';
                        const msg = document.createElement('div');
                        msg.className = 'msg';
                        msg.innerText = data.text;
                        history.appendChild(msg);
                        document.getElementById('transcripts').scrollTop = 99999;
                    } else {
                        livePreview.style.display = 'block';
                        livePreview.innerText = data.text + '...';
                    }
                };

                socket.onclose = () => stopSystem();
                socket.onerror = () => {
                    statusText.innerText = 'Errore WebSocket';
                    stopSystem();
                };
            };

            // === STOP ===
            stopBtn.onclick = () => stopSystem();
        })();
    </script>
</body>
</html>
"""


# ============================================================
# AVVIO
# ============================================================

if __name__ == "__main__":
    print("=" * 50)
    print("  App di esempio con Voice Module")
    print("  Apri: http://127.0.0.1:8000")
    print("=" * 50)
    app.run(host="127.0.0.1", port=8000, debug=False, use_reloader=False)