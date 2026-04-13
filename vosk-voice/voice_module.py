"""
Voice Module - Modulo riutilizzabile per riconoscimento vocale con Vosk
Integrabile in qualsiasi applicazione Flask esistente.

Utilizzo:
    from voice_module import init_voice_module, voice_bp
    
    app = Flask(__name__)
    init_voice_module(app, model_path="model-it")
    app.register_blueprint(voice_bp, url_prefix="/voice")
"""

import json
from vosk import Model, KaldiRecognizer
from flask_sock import Sock
from flask import Blueprint, jsonify

# Blueprint per le rotte vocali
voice_bp = Blueprint('voice', __name__)

# Variabili globali del modulo
_vosk_model = None
_sock = None
_on_transcription_callback = None
_on_partial_callback = None


def init_voice_module(app, model_path="model-it", on_transcription=None, on_partial=None):
    """
    Inizializza il modulo vocale.
    
    Args:
        app: L'applicazione Flask
        model_path: Percorso alla cartella del modello Vosk
        on_transcription: Callback per trascrizioni finali (text, session_id) -> None
        on_partial: Callback per trascrizioni parziali (text, session_id) -> None
    
    Example:
        def handle_text(text, session_id):
            print(f"Utente {session_id} ha detto: {text}")
            # Qui puoi processare comandi, salvare in DB, etc.
        
        init_voice_module(app, "model-it", on_transcription=handle_text)
    """
    global _vosk_model, _sock, _on_transcription_callback, _on_partial_callback
    
    # Salva i callback
    _on_transcription_callback = on_transcription
    _on_partial_callback = on_partial
    
    # Carica il modello Vosk
    print(f"[VoiceModule] Caricamento modello da: {model_path}")
    try:
        _vosk_model = Model(model_path)
        print("[VoiceModule] Modello caricato con successo.")
    except Exception as e:
        print(f"[VoiceModule] Errore caricamento modello: {e}")
        raise
    
    # Inizializza WebSocket
    _sock = Sock(app)
    
    # Registra la rotta WebSocket
    @_sock.route("/ws")
    def _handle_voice_websocket(ws):
        return _voice_websocket_handler(ws)
    
    print("[VoiceModule] Inizializzato. Endpoint WebSocket: /ws")


def _voice_websocket_handler(ws):
    """Handler interno per le connessioni WebSocket vocali."""
    if _vosk_model is None:
        ws.send(json.dumps({"error": "Modello non inizializzato"}))
        ws.close()
        return
    
    # Crea un nuovo recognizer per questa sessione
    rec = KaldiRecognizer(_vosk_model, 16000)
    session_id = id(ws)
    print(f"[VoiceModule] Nuova sessione: {session_id}")
    
    try:
        while True:
            audio_data = ws.receive()
            if audio_data is None:
                break
            
            # Ignora messaggi di testo (es. ping/pong)
            if isinstance(audio_data, str):
                continue
            
            # Processa l'audio
            if rec.AcceptWaveform(audio_data):
                # Risultato finale
                result = json.loads(rec.Result())
                text = result.get('text', '').strip()
                if text:
                    print(f"[VoiceModule] Sessione {session_id} - FINALE: {text}")
                    
                    # Esegui callback se definito
                    if _on_transcription_callback:
                        try:
                            _on_transcription_callback(text, session_id)
                        except Exception as e:
                            print(f"[VoiceModule] Errore callback: {e}")
                    
                    # Invia risposta al client
                    ws.send(json.dumps({
                        "text": text,
                        "is_final": True,
                        "session_id": session_id
                    }))
            else:
                # Risultato parziale
                partial = json.loads(rec.PartialResult())
                text = partial.get('partial', '').strip()
                if text:
                    # Callback parziale
                    if _on_partial_callback:
                        try:
                            _on_partial_callback(text, session_id)
                        except Exception as e:
                            print(f"[VoiceModule] Errore callback parziale: {e}")
                    
                    ws.send(json.dumps({
                        "text": text,
                        "is_final": False,
                        "session_id": session_id
                    }))
    
    except Exception as e:
        print(f"[VoiceModule] Errore sessione {session_id}: {e}")
    finally:
        print(f"[VoiceModule] Sessione chiusa: {session_id}")


# ==================== API ROUTES (opzionali) ====================

@voice_bp.route('/status')
def get_status():
    """Verifica se il modulo vocale è attivo."""
    return jsonify({
        "active": _vosk_model is not None,
        "model_loaded": _vosk_model is not None
    })