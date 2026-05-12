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
import os
import re
import threading
import time
from vosk import Model, KaldiRecognizer
from flask_sock import Sock
from flask import Blueprint, jsonify
from dotenv import load_dotenv

# Blueprint per le rotte vocali
voice_bp = Blueprint('voice', __name__)

# Variabili globali del modulo
_vosk_model = None
_sock = None
_on_transcription_callback = None
_on_partial_callback = None
_min_final_chars = 4
_min_avg_confidence = 0.7
_dedupe_window_seconds = 2.0
_recent_finals = {}
_recent_lock = threading.Lock()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return default


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _compute_avg_confidence(result_payload: dict) -> float:
    words = result_payload.get("result")
    if not isinstance(words, list) or not words:
        # Se Vosk non fornisce confidenza, non blocchiamo il comando.
        return 1.0

    conf_values = []
    for item in words:
        if isinstance(item, dict):
            conf = item.get("conf")
            if isinstance(conf, (int, float)):
                conf_values.append(float(conf))

    if not conf_values:
        return 1.0
    return sum(conf_values) / len(conf_values)


def _is_duplicate_final(text: str, session_id: int) -> bool:
    if _dedupe_window_seconds <= 0:
        return False

    now = time.monotonic()
    key = (session_id, _normalize_text(text))
    with _recent_lock:
        last_ts = _recent_finals.get(key)
        if last_ts is not None and (now - last_ts) < _dedupe_window_seconds:
            return True

        _recent_finals[key] = now

        # Mantieni la struttura piccola eliminando vecchi record.
        cutoff = now - (_dedupe_window_seconds * 2)
        stale_keys = [k for k, ts in _recent_finals.items() if ts < cutoff]
        for stale_key in stale_keys:
            _recent_finals.pop(stale_key, None)

    return False


def _filter_final_text(text: str, result_payload: dict, session_id: int) -> tuple[bool, str, float]:
    candidate = _normalize_text(text)

    if len(candidate) < _min_final_chars:
        return False, "testo troppo corto", 0.0

    avg_conf = _compute_avg_confidence(result_payload)
    if avg_conf < _min_avg_confidence:
        return False, f"conf bassa ({avg_conf:.2f})", avg_conf

    if _is_duplicate_final(candidate, session_id):
        return False, "duplicato ravvicinato", avg_conf

    return True, candidate, avg_conf


def init_voice_module(
    app,
    model_path="model-it",
    on_transcription=None,
    on_partial=None,
    min_final_chars=None,
    min_avg_confidence=None,
    dedupe_window_seconds=None,
):
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
    global _min_final_chars, _min_avg_confidence, _dedupe_window_seconds

    # Carica .env se presente (utile anche in uso standalone del modulo)
    load_dotenv(override=False)

    env_min_final_chars = _env_int("VOICE_MIN_FINAL_CHARS", 4)
    env_min_avg_confidence = _env_float("VOICE_MIN_AVG_CONFIDENCE", 0.7)
    env_dedupe_window = _env_float("VOICE_DEDUPE_WINDOW_SECONDS", 2.0)
    
    # Salva i callback
    _on_transcription_callback = on_transcription
    _on_partial_callback = on_partial
    _min_final_chars = max(1, int(env_min_final_chars if min_final_chars is None else min_final_chars))
    _min_avg_confidence = max(
        0.0,
        min(1.0, float(env_min_avg_confidence if min_avg_confidence is None else min_avg_confidence)),
    )
    _dedupe_window_seconds = max(
        0.0,
        float(env_dedupe_window if dedupe_window_seconds is None else dedupe_window_seconds),
    )
    
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
    @_sock.route("/voice/ws")
    def _handle_voice_websocket(ws):
        return _voice_websocket_handler(ws)
    
    print("[VoiceModule] Inizializzato. Endpoint WebSocket: /voice/ws")
    print(
        "[VoiceModule] Filtri attivi: "
        f"min_chars={_min_final_chars}, "
        f"min_conf={_min_avg_confidence:.2f}, "
        f"dedupe={_dedupe_window_seconds:.2f}s"
    )


def _voice_websocket_handler(ws):
    """Handler interno per le connessioni WebSocket vocali."""
    if _vosk_model is None:
        ws.send(json.dumps({"error": "Modello non inizializzato"}))
        ws.close()
        return
    
    # Crea un nuovo recognizer per questa sessione
    rec = KaldiRecognizer(_vosk_model, 16000)
    rec.SetWords(True)
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
                    accepted, payload_or_reason, avg_conf = _filter_final_text(text, result, session_id)
                    if not accepted:
                        print(
                            f"[VoiceModule] Sessione {session_id} - FINALE scartato: "
                            f"{payload_or_reason} | testo='{text}'"
                        )
                        continue

                    text = payload_or_reason
                    print(
                        f"[VoiceModule] Sessione {session_id} - FINALE: {text} "
                        f"(conf={avg_conf:.2f})"
                    )
                    
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
                        "session_id": session_id,
                        "avg_confidence": avg_conf
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