"""
########################################################################
#  TelloAI - Flask Application
#  Entry point: routes, state management, video streaming
########################################################################
"""
import os
import datetime
import threading
import time
import queue

import cv2
import numpy as np
from flask import Flask, Response, jsonify, render_template, request
from dotenv import load_dotenv

# ── Drone sub-modules ──────────────────────────────────────────────────
from drone import wifi
from drone.frame_reader import DroneReader
from drone.frame_processor import FrameProcessor
from drone.ocr_sender import OCRSender
from drone.ollama_client import (
    call_ollama_from_ocr_words,
    call_functiongemma_from_text,
    parse_model_output_to_executor_commands,
)
from drone.command_executor import CommandExecutor

# ── Load environment variables from .env ──────────────────────────────
load_dotenv()

app = Flask(__name__)

########################################################################
#  CONFIGURATION  (values come from .env, with sensible defaults)
########################################################################

app.secret_key = os.getenv("SECRET_KEY", "dev-secret-key-change-in-prod")

FLASK_HOST  = os.getenv("FLASK_HOST", "0.0.0.0")
FLASK_PORT  = int(os.getenv("FLASK_PORT", 5000))
FLASK_DEBUG = os.getenv("FLASK_DEBUG", "True") == "True"

DRONE_IP    = os.getenv("DRONE_IP",    "192.168.10.1")
DRONE_PORT  = int(os.getenv("DRONE_PORT",  8889))
VIDEO_PORT  = int(os.getenv("VIDEO_PORT",  11111))

LOG_MAX_ENTRIES = int(os.getenv("LOG_MAX_ENTRIES", 100))

# ── WiFi / streaming ──────────────────────────────────────────────────
DRONE_WIFI_SSID       = os.getenv("DRONE_WIFI_SSID",         "TELLO-XXXXXX")
WIFI_TIMEOUT          = int(os.getenv("WIFI_CONNECT_TIMEOUT", 15))

# ── Frame processing ──────────────────────────────────────────────────
FRAME_WIDTH           = int(os.getenv("FRAME_WIDTH",          960))
FRAME_HEIGHT          = int(os.getenv("FRAME_HEIGHT",         720))
JPEG_QUALITY          = int(os.getenv("JPEG_QUALITY",         80))
FRAME_ENABLE_CONTRAST = os.getenv("ENABLE_CONTRAST",          "True") == "True"
FRAME_CONTRAST_ALPHA  = float(os.getenv("CONTRAST_ALPHA",     1.05))
FRAME_CONTRAST_BETA   = int(os.getenv("CONTRAST_BETA",        2))
TARGET_FPS            = float(os.getenv("TARGET_FPS",         30.0))

# ── OCR remoto ────────────────────────────────────────────────────────
# La configurazione dettagliata (URL, intervallo, qualità, soglia) viene
# letta direttamente da .env all'interno di OCRSender tramite load_dotenv.
OCR_ENABLED = os.getenv("OCR_ENABLED", "True") == "True"

# ── Buffer comandi da FunctionGemma ───────────────────────────────────
COMMAND_BUFFER_DELAY_SECONDS = float(os.getenv("COMMAND_BUFFER_DELAY_SECONDS", "2.0"))
COMMAND_BUFFER_MAX_SIZE = int(os.getenv("COMMAND_BUFFER_MAX_SIZE", "50"))

# ── Colori terminale (ANSI) ───────────────────────────────────────────
ANSI_RESET  = "\033[0m"
ANSI_CHAT   = "\033[96m"
ANSI_MODEL  = "\033[95m"
ANSI_BUFFER = "\033[93m"


########################################################################
#  APPLICATION STATE  (in-memory; protected by a threading lock)
########################################################################

_state_lock = threading.Lock()

app_state: dict = {
    "wifi_connected": False,
    "stream_active":  False,
    "battery":        0,       # 0-100 %
    "logs":           [],
    "fps":            0.0,     # FPS in tempo reale
}


########################################################################
#  HELPERS  – logging utilities
########################################################################

def add_log(message: str, level: str = "info") -> None:
    """
    Append a timestamped entry to the in-memory log list.
    level can be: info | success | warning | error | system | user
    Only the last LOG_MAX_ENTRIES entries are kept.
    """
    timestamp = datetime.datetime.now().strftime("%H:%M:%S")
    entry = {"time": timestamp, "level": level, "message": message}

    with _state_lock:
        app_state["logs"].append(entry)
        if len(app_state["logs"]) > LOG_MAX_ENTRIES:
            app_state["logs"] = app_state["logs"][-LOG_MAX_ENTRIES:]


########################################################################
#  DRONE SINGLETONS  – istanze globali condivise tra tutte le request
########################################################################

# DroneReader incapsula djitellopy; una sola istanza per tutta la vita dell'app
drone_reader = DroneReader(host=DRONE_IP, video_port=VIDEO_PORT)

# FrameProcessor gestisce resize, contrasto e hook AI per ogni frame
frame_processor = FrameProcessor(
    width           = FRAME_WIDTH,
    height          = FRAME_HEIGHT,
    jpeg_quality    = JPEG_QUALITY,
    enable_contrast = FRAME_ENABLE_CONTRAST,
    contrast_alpha  = FRAME_CONTRAST_ALPHA,
    contrast_beta   = FRAME_CONTRAST_BETA,
)

# OCRSender invia i frame al server OCR remoto a intervalli regolari
# (la config completa è in .env, letta internamente da OCRSender)
ocr_sender = OCRSender()

# CommandExecutor traduce nomi-comando → chiamate SDK djitellopy
command_executor = CommandExecutor(drone_reader)


# Coda FIFO: i comandi vengono eseguiti in sequenza con delay tra uno e il successivo
command_buffer: queue.Queue[tuple[str, int | None]] = queue.Queue(maxsize=COMMAND_BUFFER_MAX_SIZE)


def enqueue_executor_commands(commands: list[tuple[str, int | None]], source: str = "ocr") -> int:
    """
    Inserisce in coda tutti i comandi estratti dal modello, preservando l'ordine.
    """
    enqueued = 0
    for command_name, argument in commands:
        try:
            command_buffer.put_nowait((command_name, argument))
            enqueued += 1
            msg = f"Buffered command ({source}): {command_name} {argument if argument is not None else ''}".rstrip()
            print(f"{ANSI_BUFFER}[app] {msg}{ANSI_RESET}")
            add_log(msg, "system")
        except queue.Full:
            print(f"{ANSI_BUFFER}[app] Buffer comandi pieno: comando scartato{ANSI_RESET}")
            add_log("Buffer comandi pieno: comando scartato", "warning")

    return enqueued


def _command_buffer_worker() -> None:
    """
    Worker dedicato: esegue comandi dalla coda con una pausa configurabile tra esecuzioni.
    """
    while True:
        command_name, argument = command_buffer.get()
        try:
            ok_cmd, msg_cmd = command_executor.run(command_name, argument)
            if ok_cmd:
                print(f"[app] Comando eseguito da buffer: {msg_cmd}")
                add_log(f"Eseguito: {msg_cmd}", "success")
            else:
                print(f"[app] Errore esecuzione comando da buffer: {msg_cmd}")
                add_log(f"Errore esecuzione: {msg_cmd}", "error")
        except Exception as exc:
            print(f"[app] Eccezione worker buffer comandi: {exc}")
            add_log(f"Eccezione worker buffer: {exc}", "error")
        finally:
            command_buffer.task_done()

        # Delay tra un comando e il successivo
        if COMMAND_BUFFER_DELAY_SECONDS > 0:
            time.sleep(COMMAND_BUFFER_DELAY_SECONDS)


threading.Thread(
    target=_command_buffer_worker,
    daemon=True,
    name="command-buffer-worker",
).start()


########################################################################
#  BATTERY POLLING  – thread daemon: aggiorna batteria ogni 10 s
########################################################################

def _battery_poll_loop() -> None:
    """Interroga la batteria del drone ogni 10 s mentre lo stream è attivo."""
    while True:
        time.sleep(10)
        with _state_lock:
            stream_on = app_state["stream_active"]
        if stream_on:
            level = drone_reader.get_battery()
            with _state_lock:
                app_state["battery"] = level


threading.Thread(
    target=_battery_poll_loop,
    daemon=True,
    name="battery-poll",
).start()


########################################################################
#  ROUTE – Main page
########################################################################

@app.route("/")
def index():
    """Return the main dashboard page."""
    return render_template("index.html")


########################################################################
#  ROUTES – Drone connection controls  (POST, toggle-style)
########################################################################

@app.route("/api/toggle_wifi", methods=["POST"])
def toggle_wifi():
    """
    Toggle WiFi connection to the drone.
    Returns JSON: {success, connected, message}
    """
    with _state_lock:
        currently_on = app_state["wifi_connected"]

    if currently_on:
        # ── Disconnetti: cleanup completo, poi WiFi ──────────────────
        drone_reader.cleanup_connection()
        with _state_lock:
            app_state["wifi_connected"] = False
            app_state["stream_active"]  = False
            app_state["battery"]        = 0
        add_log("WiFi disconnesso", "warning")
        return jsonify({"success": True, "connected": False, "message": "WiFi disconnesso"})

    # ── Connetti WiFi via netsh ────────────────────────────────────────
    add_log(f"Connessione WiFi → {DRONE_WIFI_SSID} …", "info")
    ok = wifi.connect(DRONE_WIFI_SSID, timeout=WIFI_TIMEOUT)
    if not ok:
        msg = f"Connessione a {DRONE_WIFI_SSID} fallita – verifica SSID in .env"
        add_log(msg, "error")
        return jsonify({"success": False, "connected": False, "message": msg})

    add_log(f"WiFi connesso a {DRONE_WIFI_SSID}", "success")

    with _state_lock:
        app_state["wifi_connected"] = True

    return jsonify({"success": True, "connected": True, "message": f"WiFi connesso a {DRONE_WIFI_SSID}"})


@app.route("/api/toggle_stream", methods=["POST"])
def toggle_stream():
    """
    Toggle the video stream.
    Il WiFi deve essere connesso prima.
    Returns JSON: {success, active, message}
    """
    with _state_lock:
        if not app_state["wifi_connected"]:
            return jsonify(
                {"success": False, "active": False,
                 "message": "Connetti prima il WiFi"}
            )
        currently_on = app_state["stream_active"]

    if currently_on:
        # ── Ferma stream con cleanup completo ──────────────────────────
        drone_reader.cleanup_connection()
        with _state_lock:
            app_state["stream_active"] = False
        add_log("Stream fermato e connessione liberata", "warning")
        return jsonify({"success": True, "active": False, "message": "Stream fermato"})

    # ── Connetti al drone e avvia stream in un’unica operazione ──────────
    add_log("Avvio stream video …", "info")
    ok = drone_reader.start_stream()
    with _state_lock:
        app_state["stream_active"] = ok
        if ok:
            app_state["battery"] = drone_reader.get_battery()

    msg = "Stream avviato" if ok else "Avvio stream fallito – verifica connessione WiFi"
    add_log(msg, "success" if ok else "error")
    return jsonify({"success": ok, "active": ok, "message": msg})


########################################################################
#  ROUTES – Drone commands
########################################################################

@app.route("/api/command", methods=["POST"])
def execute_command():
    """
    Esegue un comando sul drone tramite CommandExecutor.
    Body JSON: {command: str, argument: int (opzionale)}
    Returns JSON: {success, message}
    """
    data     = request.get_json(silent=True) or {}
    command  = str(data.get("command", "")).strip()
    argument = data.get("argument")  # può essere None

    if not command:
        return jsonify({"success": False, "message": "Campo 'command' mancante"})

    if argument is not None:
        try:
            argument = int(argument)
        except (ValueError, TypeError):
            return jsonify({"success": False, "message": "'argument' deve essere un intero"})

    add_log(f"Comando: {command}" + (f" {argument}" if argument is not None else ""), "user")
    ok, msg = command_executor.run(command, argument)
    add_log(msg, "success" if ok else "error")
    return jsonify({"success": ok, "message": msg})


@app.route("/api/commands", methods=["GET"])
def list_commands():
    """
    Ritorna la tabella completa dei comandi supportati.
    Returns JSON: {commands: [...]}
    """
    return jsonify({"commands": command_executor.available_commands()})


########################################################################
#  ROUTES – Messaging / commands
########################################################################

@app.route("/api/send_message", methods=["POST"])
def send_message():
    """
    Receive a text command or message from the UI.
    Body JSON: {message: str}
    Returns JSON: {success, message}
    """
    data = request.get_json(silent=True) or {}
    raw_message = data.get("message", "")
    message = str(raw_message)
    message_trimmed = message.strip()

    if not message_trimmed:
        return jsonify({"success": False, "message": "Messaggio vuoto"})

    # Stampa in terminale esattamente il testo ricevuto, senza alterarlo.
    print(f"{ANSI_CHAT}[chat] Messaggio ricevuto: {message}{ANSI_RESET}")
    add_log(f"Chat ricevuta: {message}", "user")

    ok_llm, llm_output = call_functiongemma_from_text(message)
    if not ok_llm:
        print(f"{ANSI_MODEL}[chat] Errore FunctionGemma: {llm_output}{ANSI_RESET}")
        add_log(f"Errore FunctionGemma: {llm_output}", "error")
        return jsonify({"success": False, "message": f"Errore FunctionGemma: {llm_output}"})

    print(f"{ANSI_MODEL}[chat] FunctionGemma output: {llm_output}{ANSI_RESET}")
    add_log("Output FunctionGemma ricevuto dalla chat", "system")

    parsed_commands = parse_model_output_to_executor_commands(llm_output)
    if not parsed_commands:
        add_log("Nessun comando valido estratto dalla risposta FunctionGemma", "warning")
        return jsonify({"success": False, "message": "Nessun comando estratto"})

    enqueued = enqueue_executor_commands(parsed_commands, source="chat")
    feedback = f"Comandi in buffer da chat: {enqueued}/{len(parsed_commands)}"
    add_log(feedback, "success" if enqueued > 0 else "warning")

    return jsonify({"success": enqueued > 0, "message": feedback})


########################################################################
#  ROUTES – Status / telemetry
########################################################################

@app.route("/api/status")
def get_status():
    """
    Return the current application state.
    Polled by the frontend every few seconds.
    Returns JSON: {wifi_connected, stream_active, battery}
    """
    with _state_lock:
        return jsonify({
            "wifi_connected": app_state["wifi_connected"],
            "stream_active":  app_state["stream_active"],
            "battery":        app_state["battery"],
        })


@app.route("/api/logs")
def get_logs():
    """
    Return all stored log entries.
    Returns JSON: {logs: [...]}
    """
    with _state_lock:
        return jsonify({"logs": list(app_state["logs"])})


@app.route("/api/battery")
def get_battery():
    """
    Return the current drone battery level.
    Returns JSON: {battery: int}
    """
    with _state_lock:
        return jsonify({"battery": app_state["battery"]})


@app.route("/api/fps")
def get_fps():
    """
    Return the current video stream FPS.
    Returns JSON: {fps: float}
    """
    with _state_lock:
        return jsonify({"fps": round(app_state["fps"], 1)})


########################################################################
#  ROUTE – MJPEG video stream
########################################################################

@app.route("/video_feed")
def video_feed():
    """
    Multipart MJPEG stream consumed by the <img> tag in the template.
    Falls through to a placeholder when the stream is inactive.
    """
    return Response(
        _frame_generator(),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )


def _frame_generator():
    """
    Generatore MJPEG: lettura frame → elaborazione → yield JPEG.
    Calcolo FPS basato SOLO sui frame effettivamente inviati al client.
    """
    min_interval = 1.0 / TARGET_FPS
    last_sent = time.perf_counter()
    last_ocr_result_id = 0
    frames_sent = 0
    last_fps_update = time.perf_counter()

    while True:
        try:
            with _state_lock:
                stream_on = app_state["stream_active"]

            if not stream_on:
                time.sleep(0.1)
                continue

            # ── Cattura frame dal drone ───────────────────────────────────
            frame = drone_reader.get_frame()
            if frame is None:
                time.sleep(0.01)
                continue

            # ── Elaborazione frame: resize, contrasto, AI ─────────────────
            processed = frame_processor.process_to_frame(frame)
            if processed is None:
                continue

            # ── Invio frame al server OCR (throttled da OCR_INTERVAL_SECONDS) ──
            if OCR_ENABLED:
                ocr_sender.send_frame(frame)

                # Invia a Ollama solo quando arriva un nuovo risultato OCR.
                current_result_id = ocr_sender.get_last_result_id()
                if current_result_id > last_ocr_result_id:
                    ocr_words = ocr_sender.get_last_words()
                    ok_llm, llm_output, ocr_input = call_ollama_from_ocr_words(ocr_words)
                    if ocr_input:
                        print(f"[app] OCR -> Ollama input: {ocr_input}")
                        if ok_llm:
                            print(f"[app] Ollama output: {llm_output}")

                            # Estrae TUTTI i comandi e li mette in buffer per esecuzione sequenziale.
                            parsed_commands = parse_model_output_to_executor_commands(llm_output)
                            if parsed_commands:
                                print(f"[app] Comandi estratti: {parsed_commands}")
                                enqueued = enqueue_executor_commands(parsed_commands, source="ocr")
                                add_log(f"Comandi in buffer da OCR: {enqueued}/{len(parsed_commands)}", "system")
                            else:
                                print("[app] Nessun comando estratto dall'output del modello")
                                add_log("Nessun comando estratto da OCR/FunctionGemma", "warning")
                        else:
                            print(f"[app] Errore chiamata Ollama: {llm_output}")
                            add_log(f"Errore chiamata Ollama da OCR: {llm_output}", "error")

                    last_ocr_result_id = current_result_id

            # ── Regolazione timing prima di encode/yield ─────────────────────
            now = time.perf_counter()
            delta = now - last_sent
            if delta < min_interval:
                time.sleep(min_interval - delta)
                now = time.perf_counter()
            
            last_sent = now

            # ── Encode JPEG e yield ───────────────────────────────────────
            ok, buffer = cv2.imencode(".jpg", processed,
                                      [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
            if not ok:
                continue

            # ── YIELD: invia frame al client ──────────────────────────────
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n"
                + buffer.tobytes() +
                b"\r\n"
            )
            
            # ── Conteggio FPS: solo frame effettivamente inviati ──────────
            frames_sent += 1
            time_elapsed = now - last_fps_update
            if time_elapsed >= 1.0:
                fps = frames_sent / time_elapsed
                frames_sent = 0
                last_fps_update = now
                # Aggiorna lo stato globale con l'FPS attuale
                with _state_lock:
                    app_state["fps"] = fps

        except Exception:
            # ── Fallback visivo + riavvio stream ──────────────────────────
            fallback = np.zeros((FRAME_HEIGHT, FRAME_WIDTH, 3), dtype=np.uint8)
            cv2.putText(fallback, "Stream non disponibile", (20, 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2, cv2.LINE_AA)
            ok, buffer = cv2.imencode(".jpg", fallback)
            if ok:
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n"
                    + buffer.tobytes() +
                    b"\r\n"
                )
            try:
                drone_reader.stop_stream()
                time.sleep(0.3)
                drone_reader.start_stream()
            except Exception:
                pass
            time.sleep(0.5)


########################################################################
#  ROUTES – Settings / Configuration  (GET/POST .env)
########################################################################

@app.route("/api/config", methods=["GET"])
def get_config():
    """
    Read current .env configuration and return filterable variables with descriptions.
    Returns JSON: {config: {...}, descriptions: {...}}
    """
    # Variables to exclude from UI
    EXCLUDED_VARS = {
        "FLASK_DEBUG",
        "FLASK_HOST",
        "FLASK_PORT",
        "ENABLE_CONTRAST",
        "FRAME_HEIGHT",
        "FRAME_WIDTH",
        "SECRET_KEY",
        "OLLAMA_PING_TEXT",
        "OLLAMA_TEMPERATURE",
        "OCR_MIN_CONFIDENCE",
    }
    
    # Descriptive text for each parameter
    DESCRIPTIONS = {
        "DRONE_IP": "Indirizzo IP del drone Tello sulla rete locale",
        "DRONE_PORT": "Porta comando del drone (default: 8889)",
        "VIDEO_PORT": "Porta per il flusso video del drone (default: 11111)",
        "LOG_MAX_ENTRIES": "Numero massimo di entry nel log (0-100)",
        "DRONE_WIFI_SSID": "Nome della rete WiFi del drone (es. TELLO-XXXXXX)",
        "WIFI_CONNECT_TIMEOUT": "Timeout connessione WiFi in secondi (15 = default)",
        "JPEG_QUALITY": "Qualità JPEG per i frame (0-100, default: 80)",
        "TARGET_FPS": "Frame rate target della dashboard (es. 30.0)",
        "CONTRAST_ALPHA": "Fattore contrasto immagine (default: 1.05)",
        "CONTRAST_BETA": "Offset contrasto immagine (default: 2)",
        "OCR_ENABLED": "Abilita invio frame a server OCR remoto",
        "OCR_SERVER_URL": "URL base del server RestOCR (es. https://ocr.sitai.duckdns.org)",
        "OCR_TIMEOUT": "Timeout richiesta OCR in secondi (default: 30)",
        "OCR_INTERVAL_SECONDS": "Intervallo minimo tra invii OCR in secondi (min: 1.0)",
        "OCR_JPEG_QUALITY": "Qualità JPEG per frame inviati a OCR (0-100)",
        "OLLAMA_URL": "Indirizzo del server Ollama (es. http://192.168.103.53:11434)",
        "OLLAMA_MODEL": "Nome del modello Ollama principale",
        "OLLAMA_FUNCTIONGEMMA_MODEL": "Nome del modello FunctionGemma in Ollama",
        "OLLAMA_TIMEOUT": "Timeout richieste Ollama in secondi (default: 30)",
        "COMMAND_BUFFER_DELAY_SECONDS": "Delay tra comandi eseguiti dal buffer (default: 2.0)",
        "COMMAND_BUFFER_MAX_SIZE": "Dimensione massima coda comandi (default: 50)",
    }
    
    config = {}
    env_file_path = ".env"
    
    # Read .env file and parse it
    if os.path.exists(env_file_path):
        with open(env_file_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                # Skip empty lines and comments
                if not line or line.startswith('#'):
                    continue
                # Parse KEY=VALUE
                if '=' in line:
                    key, value = line.split('=', 1)
                    key_clean = key.strip()
                    
                    # Only include non-excluded variables
                    if key_clean not in EXCLUDED_VARS:
                        config[key_clean] = value.strip()
    
    return jsonify({
        "config": config,
        "descriptions": DESCRIPTIONS
    })


@app.route("/api/config", methods=["POST"])
def save_config():
    """
    Save configuration changes back to .env file.
    Body JSON: {config: {KEY: VALUE, ...}}
    Returns JSON: {success, message}
    """
    data = request.get_json(silent=True) or {}
    new_config = data.get("config", {})
    
    if not isinstance(new_config, dict):
        return jsonify({"success": False, "message": "Campo 'config' deve essere un dizionario"})
    
    env_file_path = ".env"
    
    try:
        # Read existing .env file to preserve comments and order
        lines = []
        if os.path.exists(env_file_path):
            with open(env_file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
        
        # Create a new content preserving comments and structure
        new_lines = []
        processed_keys = set()
        
        for line in lines:
            stripped = line.strip()
            
            # Keep blank lines and comments as-is
            if not stripped or stripped.startswith('#'):
                new_lines.append(line)
            elif '=' in stripped:
                key = stripped.split('=', 1)[0].strip()
                if key in new_config:
                    # Replace with new value
                    new_lines.append(f"{key}={new_config[key]}\n")
                    processed_keys.add(key)
                else:
                    # Keep existing line
                    new_lines.append(line)
        
        # Add any new keys that were not in the original file
        for key, value in new_config.items():
            if key not in processed_keys:
                new_lines.append(f"{key}={value}\n")
        
        # Write back to .env
        with open(env_file_path, 'w', encoding='utf-8') as f:
            f.writelines(new_lines)
        
        add_log("Configurazione salvata in .env", "success")
        return jsonify({
            "success": True,
            "message": "Configurazione salvata. Nota: ricarica la pagina per applicare le modifiche."
        })
    
    except Exception as exc:
        error_msg = f"Errore salvataggio config: {str(exc)}"
        add_log(error_msg, "error")
        return jsonify({"success": False, "message": error_msg})


########################################################################
#  ROUTES – Emergency cleanup  (sicurezza connessioni dangling)
########################################################################

@app.route("/api/cleanup", methods=["POST"])
def cleanup():
    """
    Cleanup d'emergenza: chiude la connessione TCP al drone, fermando lo stream.
    Usato per liberare il drone da connessioni dangling.
    Chiamato all'avvio della pagina e quando si disattiva lo stream/WiFi.
    Returns JSON: {success, message}
    """
    try:
        drone_reader.cleanup_connection()
        with _state_lock:
            app_state["stream_active"] = False
            app_state["battery"]        = 0
        add_log("Cleanup connessioni drone completato", "system")
        return jsonify({"success": True, "message": "Drone disconnesso e liberato"})
    except Exception as exc:
        error_msg = f"Errore cleanup: {str(exc)}"
        add_log(error_msg, "error")
        return jsonify({"success": False, "message": error_msg})


########################################################################
#  ENTRY POINT
########################################################################

if __name__ == "__main__":
    add_log("TelloAI avviato", "system")
    app.run(
        host=FLASK_HOST,
        port=FLASK_PORT,
        debug=FLASK_DEBUG,
        threaded=True,
    )
