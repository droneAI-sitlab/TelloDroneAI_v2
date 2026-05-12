"""
########################################################################
#  TelloAI - Flask Application
#  Entry point: routes, state management, video streaming
########################################################################
"""
import os
import atexit
import sys
import datetime
import threading
import time
import queue
import re
import json
import itertools
import zipfile
import importlib.util
from pathlib import Path

import cv2
import numpy as np
from flask import Flask, Response, jsonify, render_template, request
from dotenv import load_dotenv

# ── Carica voice_module dalla cartella "vosk-voice" ───────────────────
_VOICE_MODULE_FILE = os.path.join(os.path.dirname(__file__), "vosk-voice", "voice_module.py")
_voice_spec = importlib.util.spec_from_file_location("voice_module", _VOICE_MODULE_FILE)
if _voice_spec is None or _voice_spec.loader is None:
    raise ImportError(f"Impossibile caricare voice_module da: {_VOICE_MODULE_FILE}")
_voice_module = importlib.util.module_from_spec(_voice_spec)
_voice_spec.loader.exec_module(_voice_module)

init_voice_module = _voice_module.init_voice_module
voice_bp = _voice_module.voice_bp

# ── Drone sub-modules ──────────────────────────────────────────────────
from drone import wifi
from drone.frame_reader import DroneReader
from drone.frame_processor import FrameProcessor
from drone.ocr_sender import OCRSender
from drone.media_capture import DroneMediaCapture
from drone.ollama_client import (
    call_ollama_from_ocr_words,
    call_functiongemma_from_text,
    parse_model_output_to_executor_commands,
    get_ollama_config,
)
from drone.command_executor import CommandExecutor

# ── Load environment variables from .env ──────────────────────────────
# override=True evita che vecchie variabili di ambiente del processo
# mantengano valori stale (es. FUNCTIONGEMMA_ENABLED=false).
load_dotenv(override=True)

app = Flask(__name__)
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.register_blueprint(voice_bp, url_prefix="/voice")


def _env_bool(name: str, default: bool) -> bool:
    """Parsa bool da .env in modo case-insensitive e con fallback sicuro."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int) -> int:
    """Parsa int da .env con fallback sicuro."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    """Parsa float da .env con fallback sicuro."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return default

########################################################################
#  CONFIGURATION  (values come from .env, with sensible defaults)
########################################################################

app.secret_key = os.getenv("SECRET_KEY", "dev-secret-key-change-in-prod")

FLASK_HOST  = os.getenv("FLASK_HOST", "0.0.0.0")
FLASK_PORT  = int(os.getenv("FLASK_PORT", 5000))
FLASK_DEBUG = _env_bool("FLASK_DEBUG", True)

DRONE_IP    = os.getenv("DRONE_IP",    "192.168.10.1")
DRONE_PORT  = int(os.getenv("DRONE_PORT",  8889))
VIDEO_PORT  = int(os.getenv("VIDEO_PORT",  11111))
DRONE_RC_SPEED = int(os.getenv("DRONE_RC_SPEED", 70))

LOG_MAX_ENTRIES = int(os.getenv("LOG_MAX_ENTRIES", 100))

# ── WiFi / streaming ──────────────────────────────────────────────────
DRONE_WIFI_SSID       = os.getenv("DRONE_WIFI_SSID",         "TELLO-XXXXXX")
WIFI_TIMEOUT          = int(os.getenv("WIFI_CONNECT_TIMEOUT", 15))

# ── Frame processing ──────────────────────────────────────────────────
FRAME_WIDTH           = int(os.getenv("FRAME_WIDTH",          960))
FRAME_HEIGHT          = int(os.getenv("FRAME_HEIGHT",         720))
JPEG_QUALITY          = int(os.getenv("JPEG_QUALITY",         80))
FRAME_ENABLE_CONTRAST = _env_bool("ENABLE_CONTRAST", True)
FRAME_CONTRAST_ALPHA  = float(os.getenv("CONTRAST_ALPHA",     1.05))
FRAME_CONTRAST_BETA   = int(os.getenv("CONTRAST_BETA",        2))
TARGET_FPS            = float(os.getenv("TARGET_FPS",         30.0))

# ── OCR remoto ────────────────────────────────────────────────────────
# La configurazione dettagliata (URL, intervallo, qualità, soglia) viene
# letta direttamente da .env all'interno di OCRSender tramite load_dotenv.
OCR_ENABLED = _env_bool("OCR_ENABLED", True)

# ── FunctionGemma (interpretazione comandi chat) ───────────────────────
# Se disabilitato, i comandi dalla chat devono essere scritti esattamente
# (case-insensitive: "takeoff", "move_forward 50", "avanti 30")
FUNCTIONGEMMA_ENABLED = _env_bool("FUNCTIONGEMMA_ENABLED", True)

# ── Buffer comandi da FunctionGemma ───────────────────────────────────
COMMAND_BUFFER_DELAY_SECONDS = float(os.getenv("COMMAND_BUFFER_DELAY_SECONDS", "2.0"))
COMMAND_BUFFER_MAX_SIZE = int(os.getenv("COMMAND_BUFFER_MAX_SIZE", "50"))
# Keepalive cooldown timer (nuovo sistema a buffer singolo)
KEEPALIVE_COOLDOWN_SECONDS = float(os.getenv("KEEPALIVE_COOLDOWN_SECONDS", "5.0"))
KEEPALIVE_USE_NO_RESPONSE = _env_bool("KEEPALIVE_USE_NO_RESPONSE", False)
COMMAND_DEDUP_WINDOW_SECONDS = float(os.getenv("COMMAND_DEDUP_WINDOW_SECONDS", "1.2"))
CHAT_MESSAGE_DEDUP_WINDOW_SECONDS = float(os.getenv("CHAT_MESSAGE_DEDUP_WINDOW_SECONDS", "1.2"))
MEDIA_OUTPUT_DIR = os.getenv("MEDIA_OUTPUT_DIR", "captures")
MEDIA_VIDEO_FPS = float(os.getenv("MEDIA_VIDEO_FPS", "20.0"))
MEDIA_VIDEO_CODEC = os.getenv("MEDIA_VIDEO_CODEC", "mp4v")

# ── Colori terminale (ANSI) ───────────────────────────────────────────
ANSI_RESET  = "\033[0m"
ANSI_CHAT   = "\033[96m"
ANSI_MODEL  = "\033[95m"
ANSI_BUFFER = "\033[93m"
ANSI_FUNC   = "\033[92m"


########################################################################
#  APPLICATION STATE  (in-memory; protected by a threading lock)
########################################################################

_state_lock = threading.Lock()

app_state: dict = {
    "wifi_connected": False,
    "stream_active":  False,
    "keyboard_mode":  False,
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


def echo_functiongemma_terminal(title: str, payload: dict | None = None) -> None:
    """Stampa debug FunctionGemma in colore dedicato con payload formattato."""
    print(f"{ANSI_FUNC}[functiongemma] {title}{ANSI_RESET}")
    if payload is not None:
        try:
            pretty = json.dumps(payload, ensure_ascii=False, indent=2)
        except Exception:
            pretty = str(payload)
        print(f"{ANSI_FUNC}{pretty}{ANSI_RESET}")


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

# DroneMediaCapture gestisce foto e registrazione video da stream drone
media_capture = DroneMediaCapture(drone_reader)

# CommandExecutor traduce nomi-comando → chiamate SDK djitellopy
command_executor = CommandExecutor(
    drone_reader,
    media_capture=media_capture,
    dedupe_window_seconds=COMMAND_DEDUP_WINDOW_SECONDS,
)


# Coda prioritaria: emergency (prio 0) > comandi normali (prio 1) > keepalive (prio 2)
_command_sequence = itertools.count()
PRIORITY_EMERGENCY = command_executor.emergency_priority_value()
PRIORITY_NORMAL = command_executor.normal_priority_value()
PRIORITY_KEEPALIVE = command_executor.keepalive_priority_value()

command_buffer: queue.PriorityQueue[tuple[int, int, str, int | None]] = queue.PriorityQueue(
    maxsize=COMMAND_BUFFER_MAX_SIZE
)

_chat_dedupe_lock = threading.Lock()
_last_chat_signature = ""
_last_chat_ts = 0.0
_teardown_lock = threading.Lock()
_shutdown_lock = threading.Lock()
_shutdown_done = False

_RESTART_REQUIRED_CONFIG_KEYS = {
    "DRONE_IP",
    "DRONE_PORT",
    "VIDEO_PORT",
    "COMMAND_BUFFER_MAX_SIZE",
}


def _has_pending_emergency() -> bool:
    """Controlla se il prossimo comando in coda ha priorita' emergency."""
    with command_buffer.mutex:
        if not command_buffer.queue:
            return False
        return command_buffer.queue[0][0] == PRIORITY_EMERGENCY


def _normalize_command_key(command_name: str) -> str:
    """Normalizza un nome comando per confronti di deduplica."""
    return re.sub(r"[\s\-]+", "_", str(command_name or "").strip().lower())


def _is_command_already_buffered(command_name: str, argument: int | None) -> bool:
    """True se nella coda esiste gia' lo stesso comando+argomento."""
    target_name = _normalize_command_key(command_name)
    with command_buffer.mutex:
        for _, _, queued_name, queued_argument in command_buffer.queue:
            if _normalize_command_key(queued_name) == target_name and queued_argument == argument:
                return True
    return False


def _is_duplicate_chat_message(message: str) -> bool:
    """Deduplica messaggi chat identici in una breve finestra temporale."""
    if CHAT_MESSAGE_DEDUP_WINDOW_SECONDS <= 0:
        return False

    normalized = re.sub(r"\s+", " ", str(message or "").strip().lower())
    if not normalized:
        return False

    now = time.monotonic()

    global _last_chat_signature, _last_chat_ts
    with _chat_dedupe_lock:
        is_duplicate = (
            normalized == _last_chat_signature
            and (now - _last_chat_ts) < CHAT_MESSAGE_DEDUP_WINDOW_SECONDS
        )
        _last_chat_signature = normalized
        _last_chat_ts = now

    return is_duplicate


def _reload_runtime_config(changed_keys: set[str] | None = None) -> tuple[list[str], list[str]]:
    """
    Ricarica .env e applica a caldo le opzioni runtime compatibili.

    Returns:
        (applied_keys, restart_required_keys)
    """
    load_dotenv(override=True)

    global LOG_MAX_ENTRIES
    global DRONE_WIFI_SSID, WIFI_TIMEOUT
    global FRAME_WIDTH, FRAME_HEIGHT, JPEG_QUALITY
    global FRAME_ENABLE_CONTRAST, FRAME_CONTRAST_ALPHA, FRAME_CONTRAST_BETA
    global TARGET_FPS
    global OCR_ENABLED, FUNCTIONGEMMA_ENABLED
    global COMMAND_BUFFER_DELAY_SECONDS, KEEPALIVE_COOLDOWN_SECONDS, KEEPALIVE_USE_NO_RESPONSE
    global COMMAND_DEDUP_WINDOW_SECONDS, CHAT_MESSAGE_DEDUP_WINDOW_SECONDS
    global DRONE_RC_SPEED

    changed = changed_keys or set()

    LOG_MAX_ENTRIES = _env_int("LOG_MAX_ENTRIES", LOG_MAX_ENTRIES)
    DRONE_RC_SPEED = _env_int("DRONE_RC_SPEED", DRONE_RC_SPEED)
    # Aggiorna anche velocità di the flight defaults
    import drone.command_executor
    drone.command_executor._DEFAULT_SPEED = _env_int("DRONE_SPEED", drone.command_executor._DEFAULT_SPEED)

    DRONE_WIFI_SSID = os.getenv("DRONE_WIFI_SSID", DRONE_WIFI_SSID)
    WIFI_TIMEOUT = _env_int("WIFI_CONNECT_TIMEOUT", WIFI_TIMEOUT)

    FRAME_WIDTH = _env_int("FRAME_WIDTH", FRAME_WIDTH)
    FRAME_HEIGHT = _env_int("FRAME_HEIGHT", FRAME_HEIGHT)
    JPEG_QUALITY = _env_int("JPEG_QUALITY", JPEG_QUALITY)
    FRAME_ENABLE_CONTRAST = _env_bool("ENABLE_CONTRAST", FRAME_ENABLE_CONTRAST)
    FRAME_CONTRAST_ALPHA = _env_float("CONTRAST_ALPHA", FRAME_CONTRAST_ALPHA)
    FRAME_CONTRAST_BETA = _env_int("CONTRAST_BETA", FRAME_CONTRAST_BETA)
    TARGET_FPS = _env_float("TARGET_FPS", TARGET_FPS)

    OCR_ENABLED = _env_bool("OCR_ENABLED", OCR_ENABLED)
    FUNCTIONGEMMA_ENABLED = _env_bool("FUNCTIONGEMMA_ENABLED", FUNCTIONGEMMA_ENABLED)

    COMMAND_BUFFER_DELAY_SECONDS = _env_float(
        "COMMAND_BUFFER_DELAY_SECONDS",
        COMMAND_BUFFER_DELAY_SECONDS,
    )
    KEEPALIVE_COOLDOWN_SECONDS = _env_float(
        "KEEPALIVE_COOLDOWN_SECONDS",
        KEEPALIVE_COOLDOWN_SECONDS,
    )
    KEEPALIVE_USE_NO_RESPONSE = _env_bool(
        "KEEPALIVE_USE_NO_RESPONSE",
        KEEPALIVE_USE_NO_RESPONSE,
    )
    COMMAND_DEDUP_WINDOW_SECONDS = _env_float(
        "COMMAND_DEDUP_WINDOW_SECONDS",
        COMMAND_DEDUP_WINDOW_SECONDS,
    )
    CHAT_MESSAGE_DEDUP_WINDOW_SECONDS = _env_float(
        "CHAT_MESSAGE_DEDUP_WINDOW_SECONDS",
        CHAT_MESSAGE_DEDUP_WINDOW_SECONDS,
    )

    frame_processor.update_config(
        width=FRAME_WIDTH,
        height=FRAME_HEIGHT,
        jpeg_quality=JPEG_QUALITY,
        enable_contrast=FRAME_ENABLE_CONTRAST,
        contrast_alpha=FRAME_CONTRAST_ALPHA,
        contrast_beta=FRAME_CONTRAST_BETA,
    )
    ocr_sender.reload_from_env()
    media_capture.reload_from_env()
    command_executor.set_dedupe_window_seconds(COMMAND_DEDUP_WINDOW_SECONDS)

    runtime_keys = {
        "LOG_MAX_ENTRIES",
        "DRONE_WIFI_SSID",
        "WIFI_CONNECT_TIMEOUT",
        "FRAME_WIDTH",
        "FRAME_HEIGHT",
        "JPEG_QUALITY",
        "ENABLE_CONTRAST",
        "CONTRAST_ALPHA",
        "CONTRAST_BETA",
        "TARGET_FPS",
        "OCR_ENABLED",
        "FUNCTIONGEMMA_ENABLED",
        "OCR_SERVER_URL",
        "OCR_TIMEOUT",
        "OCR_INTERVAL_SECONDS",
        "OCR_JPEG_QUALITY",
        "OCR_MIN_CONFIDENCE",
        "COMMAND_BUFFER_DELAY_SECONDS",
        "KEEPALIVE_COOLDOWN_SECONDS",
        "KEEPALIVE_USE_NO_RESPONSE",
        "COMMAND_DEDUP_WINDOW_SECONDS",
        "CHAT_MESSAGE_DEDUP_WINDOW_SECONDS",
        "MEDIA_OUTPUT_DIR",
        "MEDIA_VIDEO_FPS",
        "MEDIA_VIDEO_CODEC",
        "OLLAMA_URL",
        "OLLAMA_MODEL",
        "OLLAMA_FUNCTIONGEMMA_MODEL",
        "OLLAMA_TIMEOUT",
        "OLLAMA_TEMPERATURE",
    }

    applied_keys = sorted(changed.intersection(runtime_keys))
    restart_required = sorted(changed.intersection(_RESTART_REQUIRED_CONFIG_KEYS))
    return applied_keys, restart_required


def _drain_command_buffer() -> int:
    """Svuota la coda comandi e ritorna quanti elementi sono stati rimossi."""
    drained = 0
    while True:
        try:
            command_buffer.get_nowait()
        except queue.Empty:
            break
        else:
            command_buffer.task_done()
            drained += 1
    return drained


def _hard_disconnect_drone(
    reason: str,
    *,
    disconnect_wifi: bool = False,
    force_wifi_state: bool | None = None,
    log_level: str = "warning",
) -> bool:
    """
    Cleanup centralizzato e aggressivo di tutte le risorse drone.

    - stop stream/video recording
    - chiusura connessione SDK
    - reset stato executor
    - svuotamento coda comandi
    - reset keepalive timer
    - opzionale disconnessione WiFi OS-level
    """
    global _last_keepalive_ts

    with _teardown_lock:
        media_capture.stop_video_recording_silent()
        drone_reader.cleanup_connection()
        command_executor.reset_runtime_state()
        drained = _drain_command_buffer()

        with _keepalive_lock:
            _last_keepalive_ts = 0.0

        wifi_ok = True
        if disconnect_wifi:
            wifi_ok = wifi.disconnect(target_ssid=DRONE_WIFI_SSID)

        with _state_lock:
            app_state["stream_active"] = False
            app_state["keyboard_mode"] = False
            app_state["battery"] = 0
            app_state["fps"] = 0.0
            if force_wifi_state is not None:
                app_state["wifi_connected"] = force_wifi_state
            elif disconnect_wifi:
                app_state["wifi_connected"] = False

    add_log(f"{reason} (cleanup completo, coda svuotata: {drained})", log_level)
    return wifi_ok


def _shutdown_cleanup(reason: str = "Chiusura applicazione") -> None:
    """Esegue cleanup una sola volta quando il processo termina."""
    global _shutdown_done

    with _shutdown_lock:
        if _shutdown_done:
            return
        _shutdown_done = True

    try:
        _hard_disconnect_drone(
            reason,
            disconnect_wifi=True,
            force_wifi_state=False,
            log_level="system",
        )
    except Exception as exc:
        print(f"[app] Errore cleanup shutdown: {exc}")


def enqueue_executor_commands(commands: list[tuple[str, int | None]], source: str = "ocr") -> int:
    """
    Inserisce in coda tutti i comandi estratti dal modello, preservando l'ordine.
    """
    enqueued = 0
    batch_seen: set[tuple[str, int | None]] = set()

    for command_name, argument in commands:
        normalized = _normalize_command_key(command_name)
        dedupe_key = (normalized, argument)

        if dedupe_key in batch_seen:
            msg = (
                f"Duplicato batch ignorato ({source}): {normalized} "
                f"{argument if argument is not None else ''}"
            ).rstrip()
            print(f"{ANSI_BUFFER}[app] {msg}{ANSI_RESET}")
            add_log(msg, "warning")
            continue

        if _is_command_already_buffered(command_name, argument):
            msg = (
                f"Duplicato coda ignorato ({source}): {normalized} "
                f"{argument if argument is not None else ''}"
            ).rstrip()
            print(f"{ANSI_BUFFER}[app] {msg}{ANSI_RESET}")
            add_log(msg, "warning")
            continue

        batch_seen.add(dedupe_key)

        try:
            priority = command_executor.get_command_priority(command_name)
            sequence = next(_command_sequence)
            command_buffer.put_nowait((priority, sequence, command_name, argument))
            enqueued += 1
            msg = (
                f"Buffered command ({source}): {command_name} "
                f"{argument if argument is not None else ''} [p={priority}]"
            ).rstrip()
            print(f"{ANSI_BUFFER}[app] {msg}{ANSI_RESET}")
            add_log(msg, "system")
        except queue.Full:
            print(f"{ANSI_BUFFER}[app] Buffer comandi pieno: comando scartato{ANSI_RESET}")
            add_log("Buffer comandi pieno: comando scartato", "warning")

    return enqueued


# Timestamp ultimo keepalive eseguito (per cooldown)
_last_keepalive_ts = 0.0
_keepalive_lock = threading.Lock()


def _command_buffer_worker() -> None:
    """
    Worker dedicato: esegue comandi dalla coda con keepalive dinamico.
    
    Nuova logica:
    - Un solo buffer con priorità 3 livelli (0=emergency, 1=normali, 2=keepalive)
    - Keepalive viene eseguito solo quando buffer vuoto e cooldown scaduto
    - Durante cooldown, se arrivano comandi, il timer si resetta
    """
    global _last_keepalive_ts
    
    while True:
        # Controlla se ci sono comandi reali nella coda
        has_real_commands = not command_buffer.empty()
        
        if has_real_commands:
            # Estrai ed esegui comando dalla coda
            _, _, command_name, argument = command_buffer.get()
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
            
            # Reset del timer keepalive quando eseguiamo un comando reale
            with _keepalive_lock:
                _last_keepalive_ts = 0.0
            
            # Delay tra comandi
            if COMMAND_BUFFER_DELAY_SECONDS > 0:
                waited = 0.0
                step = 0.05
                while waited < COMMAND_BUFFER_DELAY_SECONDS:
                    if _has_pending_emergency():
                        break
                    chunk = min(step, COMMAND_BUFFER_DELAY_SECONDS - waited)
                    time.sleep(chunk)
                    waited += chunk
        else:
            # Buffer vuoto - controlla se serve keepalive            
            if drone_reader.get_tello() is None:
                # Nessun keepalive se non connesso SDK
                time.sleep(0.1)
                continue
            
            # Controlla cooldown keepalive
            now = time.monotonic()
            with _keepalive_lock:
                elapsed = now - _last_keepalive_ts
            
            if elapsed >= KEEPALIVE_COOLDOWN_SECONDS:
                # Esegui keepalive
                try:
                    keepalive_command = (
                        "send_keepalive_no_response"
                        if KEEPALIVE_USE_NO_RESPONSE
                        else "send_keepalive"
                    )
                    ok_cmd, msg_cmd = command_executor.run(keepalive_command, None)
                    if ok_cmd:
                        print(f"[app] Keepalive eseguito: {msg_cmd}")
                        add_log("Keepalive eseguito", "system")
                    else:
                        print(f"[app] Keepalive fallito: {msg_cmd}")
                        add_log(f"Keepalive fallito: {msg_cmd}", "warning")
                        # Fallback get_battery
                        ok_fb, msg_fb = command_executor.run("get_battery", None)
                        if ok_fb:
                            battery_level = drone_reader.get_battery()
                            with _state_lock:
                                app_state["battery"] = battery_level
                            add_log("Keepalive fallback get_battery ok", "system")
                except Exception as exc:
                    print(f"[app] Eccezione keepalive: {exc}")
                    add_log(f"Eccezione keepalive: {exc}", "error")
                finally:
                    with _keepalive_lock:
                        _last_keepalive_ts = time.monotonic()
                
                # Attendi il resto del cooldown prima di ricontrollare
                time.sleep(max(0.0, KEEPALIVE_COOLDOWN_SECONDS - 0.1))
            else:
                # In cooldown, aspetta un po'
                time.sleep(0.1)


threading.Thread(
    target=_command_buffer_worker,
    daemon=True,
    name="command-buffer-worker",
).start()


########################################################################
#  BATTERY POLLING  – thread daemon: aggiorna batteria ogni 10 s
########################################################################

def _battery_poll_loop() -> None:
    """Interroga la batteria del drone ogni 10 s se connesso (anche senza stream)."""
    while True:
        time.sleep(10)
        if drone_reader.get_tello() is not None:
            level = drone_reader.get_battery()
            with _state_lock:
                app_state["battery"] = level


threading.Thread(
    target=_battery_poll_loop,
    daemon=True,
    name="battery-poll",
).start()


def _wifi_watchdog_loop() -> None:
    """
    Verifica periodicamente che il PC sia ancora sulla rete del drone.
    Se il WiFi cade, forza teardown per evitare sessioni stream corrotte.
    """
    while True:
        time.sleep(2)

        with _state_lock:
            wifi_expected = app_state["wifi_connected"]
            stream_on = app_state["stream_active"]

        if not wifi_expected and not stream_on:
            continue

        if wifi.is_connected_to(DRONE_WIFI_SSID):
            continue

        _hard_disconnect_drone(
            "WiFi drone perso: connessioni e stream liberati",
            disconnect_wifi=False,
            force_wifi_state=False,
            log_level="warning",
        )


threading.Thread(
    target=_wifi_watchdog_loop,
    daemon=True,
    name="wifi-watchdog",
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
        wifi_ok = _hard_disconnect_drone(
            "Disconnessione WiFi richiesta",
            disconnect_wifi=True,
            force_wifi_state=False,
            log_level="warning",
        )
        msg = "WiFi disconnesso e comunicazioni drone liberate"
        if not wifi_ok:
            msg += " (disconnessione WiFi non confermata)"
        return jsonify({
            "success": True,
            "connected": False,
            "message": msg,
            "wifi_disconnected": wifi_ok,
        })

    _hard_disconnect_drone(
        "Pre-cleanup prima della riconnessione WiFi",
        disconnect_wifi=False,
        force_wifi_state=False,
        log_level="system",
    )

    # ── Connetti WiFi via netsh ────────────────────────────────────────
    add_log(f"Connessione WiFi → {DRONE_WIFI_SSID} …", "info")
    ok = wifi.connect(DRONE_WIFI_SSID, timeout=WIFI_TIMEOUT)
    if not ok:
        msg = f"Connessione a {DRONE_WIFI_SSID} fallita – verifica SSID in .env"
        add_log(msg, "error")
        return jsonify({"success": False, "connected": False, "message": msg})

    add_log(f"WiFi connesso a {DRONE_WIFI_SSID}", "success")
    
    # Inizializza subito la connessione ai comandi del drone (SDK)
    ok_drone = drone_reader.connect_drone()
    if not ok_drone:
        msg = "WiFi connesso, ma fallita comunicazione con l'SDK del drone"
        add_log(msg, "warning")
        return jsonify({"success": True, "connected": True, "message": msg})

    with _state_lock:
        app_state["wifi_connected"] = True

    return jsonify({"success": True, "connected": True, "message": f"WiFi connesso a {DRONE_WIFI_SSID} e drone pronto"})


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

    if not wifi.is_connected_to(DRONE_WIFI_SSID):
        _hard_disconnect_drone(
            "Stream non avviato: WiFi drone non disponibile",
            disconnect_wifi=False,
            force_wifi_state=False,
            log_level="warning",
        )
        return jsonify(
            {
                "success": False,
                "active": False,
                "message": "WiFi drone non disponibile: riconnetti prima il WiFi",
            }
        )

    if currently_on:
        drone_reader.stop_stream()
        with _state_lock:
            app_state["stream_active"] = False
        add_log("Stream fermato su richiesta utente (SDK connesso)", "warning")
        return jsonify({"success": True, "active": False, "message": "Stream fermato"})

    # ── Connetti al drone e avvia stream in un’unica operazione ──────────
    add_log("Avvio stream video …", "info")
    ok = drone_reader.start_stream()
    with _state_lock:
        app_state["stream_active"] = ok
        if ok:
            app_state["battery"] = drone_reader.get_battery()
            app_state["fps"] = 0.0

    if not ok:
        _hard_disconnect_drone(
            "Avvio stream fallito: risorse drone rilasciate",
            disconnect_wifi=False,
            force_wifi_state=wifi.is_connected_to(DRONE_WIFI_SSID),
            log_level="error",
        )

    msg = "Stream avviato" if ok else "Avvio stream fallito – verifica connessione WiFi"
    add_log(msg, "success" if ok else "error")
    return jsonify({"success": ok, "active": ok, "message": msg})


@app.route("/api/toggle_keyboard", methods=["POST"])
def toggle_keyboard():
    """
    Abilita o disabilita la modalità RC da tastiera lato frontend.

    L'endpoint esiste per allineare lo stato server-side al toggle UI.
    """
    data = request.get_json(silent=True) or {}
    active = bool(data.get("active", False))

    with _state_lock:
        app_state["keyboard_mode"] = active

    add_log(
        "Modalità tastiera RC attivata" if active else "Modalità tastiera RC disattivata",
        "system",
    )
    return jsonify({"success": True, "active": active, "message": "ok"})


@app.route("/api/rc", methods=["POST"])
def rc_control():
    """
    Invia un comando RC continuo a Tello.

    Body JSON: {lr, fb, ud, yaw}
    Valori attesi: interi nell'intervallo [-100, 100].
    """
    data = request.get_json(silent=True) or {}

    if not wifi.is_connected_to(DRONE_WIFI_SSID):
        return jsonify({"success": False, "message": "WiFi del drone non connesso"}), 409

    tello = drone_reader.get_tello()
    if tello is None:
        return jsonify({"success": False, "message": "Drone non pronto (SDK offline)"}), 409

    try:
        lr = max(-100, min(100, int(data.get("lr", 0))))
        fb = max(-100, min(100, int(data.get("fb", 0))))
        ud = max(-100, min(100, int(data.get("ud", 0))))
        yaw = max(-100, min(100, int(data.get("yaw", 0))))
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "Parametri RC non validi"}), 400

    try:
        tello.send_rc_control(lr, fb, ud, yaw)
        return jsonify({"success": True, "message": "ok", "lr": lr, "fb": fb, "ud": ud, "yaw": yaw})
    except Exception as exc:
        return jsonify({"success": False, "message": f"Errore RC: {exc}"}), 500


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


@app.route("/api/media/photo", methods=["POST"])
def take_photo_route():
    """
    Scatta una foto dal feed corrente del drone.
    Returns JSON: {success, message, path}
    """
    try:
        path = media_capture.take_photo()
        msg = f"Foto salvata: {path}"
        add_log(msg, "success")
        return jsonify({"success": True, "message": msg, "path": path})
    except Exception as exc:
        msg = f"Errore foto: {exc}"
        add_log(msg, "error")
        return jsonify({"success": False, "message": msg})


@app.route("/api/media/video/start", methods=["POST"])
def start_video_route():
    """
    Avvia registrazione video dal feed del drone.
    Returns JSON: {success, message, path}
    """
    try:
        path = media_capture.start_video_recording()
        msg = f"Registrazione video avviata: {path}"
        add_log(msg, "success")
        return jsonify({"success": True, "message": msg, "path": path})
    except Exception as exc:
        msg = f"Errore avvio registrazione: {exc}"
        add_log(msg, "error")
        return jsonify({"success": False, "message": msg})


@app.route("/api/media/video/stop", methods=["POST"])
def stop_video_route():
    """
    Ferma registrazione video in corso.
    Returns JSON: {success, message, path}
    """
    try:
        path = media_capture.stop_video_recording()
        msg = f"Registrazione video fermata: {path}"
        add_log(msg, "success")
        return jsonify({"success": True, "message": msg, "path": path})
    except Exception as exc:
        msg = f"Errore stop registrazione: {exc}"
        add_log(msg, "error")
        return jsonify({"success": False, "message": msg})


@app.route("/api/media/status", methods=["GET"])
def media_status_route():
    """
    Stato attuale del recorder video.
    Returns JSON: {recording: bool, path: str | null}
    """
    return jsonify({
        "recording": media_capture.is_recording(),
        "path": media_capture.get_recording_path(),
    })


########################################################################
#  ROUTES – Messaging / commands
########################################################################

@app.route("/api/send_message", methods=["POST"])
def send_message():
    """
    Receive a text command or message from the UI.
    Body JSON: {message: str}
    Returns JSON: {success, message}
    
    Se FUNCTIONGEMMA_ENABLED=false: accetta solo comandi diretti (case-insensitive)
    Se FUNCTIONGEMMA_ENABLED=true: prova comando diretto, poi interpreta con FunctionGemma
    """
    data = request.get_json(silent=True) or {}
    raw_message = data.get("message", "")
    message = str(raw_message)
    message_trimmed = message.strip()

    if not message_trimmed:
        return jsonify({"success": False, "message": "Messaggio vuoto"})

    if _is_duplicate_chat_message(message_trimmed):
        msg = "Messaggio duplicato ravvicinato ignorato"
        add_log(f"{msg}: {message_trimmed}", "warning")
        return jsonify({"success": True, "message": msg, "deduplicated": True})

    # Stampa in terminale esattamente il testo ricevuto, senza alterarlo.
    print(f"{ANSI_CHAT}[chat] Messaggio ricevuto: {message}{ANSI_RESET}")
    add_log(f"Chat ricevuta: {message}", "user")
    echo_functiongemma_terminal(
        "Stato flag FunctionGemma per questa richiesta",
        {"enabled": FUNCTIONGEMMA_ENABLED},
    )

    # Parsing comando e argomento opzionale
    match = re.match(r"^(.*?)(?:\s+(-?\d+))?$", message_trimmed)
    if match:
        direct_command = match.group(1).strip()
        direct_arg_raw = match.group(2)
        direct_argument = int(direct_arg_raw) if direct_arg_raw is not None else None

        ok_direct, msg_direct, canonical_direct, effective_direct_arg = command_executor.validate(
            direct_command,
            direct_argument,
        )
        if ok_direct:
            queued_command = canonical_direct or direct_command
            enqueued = enqueue_executor_commands(
                [(queued_command, effective_direct_arg)],
                source="chat-direct",
            )
            if enqueued > 0:
                detail = (
                    f"{queued_command} {effective_direct_arg}"
                    if effective_direct_arg is not None
                    else queued_command
                )
                msg = f"Comando diretto accodato: {detail}"
                add_log(msg, "success")
                return jsonify({"success": True, "message": msg, "direct_command": True, "queued": True})

            msg = "Buffer comandi pieno: comando diretto non accodato"
            add_log(msg, "warning")
            return jsonify({"success": False, "message": msg, "direct_command": True, "queued": True})

        if "Comando sconosciuto" not in msg_direct:
            add_log(f"Errore comando diretto chat: {msg_direct}", "error")
            return jsonify({"success": False, "message": msg_direct, "direct_command": True})

        if not FUNCTIONGEMMA_ENABLED:
            add_log(f"Comando non riconosciuto e FunctionGemma disabilitato: {direct_command}", "warning")
            echo_functiongemma_terminal(
                "Tentativo chiamata NON eseguito (FunctionGemma disabilitato)",
                {
                    "enabled": FUNCTIONGEMMA_ENABLED,
                    "input": message,
                    "reason": "comando diretto non riconosciuto",
                },
            )
            return jsonify({
                "success": False,
                "message": f"Comando non riconosciuto: '{direct_command}'. FunctionGemma disabilitato - usa comandi esatti.",
                "direct_command": False,
                "functiongemma_disabled": True,
            })

        add_log("Comando diretto non riconosciuto - tentativo interpretazione FunctionGemma", "system")
        echo_functiongemma_terminal(
            "Tentativo chiamata dopo comando diretto non riconosciuto",
            {"enabled": FUNCTIONGEMMA_ENABLED, "input": message},
        )
    elif not FUNCTIONGEMMA_ENABLED:
        echo_functiongemma_terminal(
            "Tentativo chiamata NON eseguito (FunctionGemma disabilitato)",
            {
                "enabled": FUNCTIONGEMMA_ENABLED,
                "input": message,
                "reason": "formato comando diretto non valido",
            },
        )
        return jsonify({
            "success": False,
            "message": "Formato messaggio non valido. FunctionGemma disabilitato - usa: comando [argomento]",
            "functiongemma_disabled": True,
        })

    cfg = get_ollama_config()
    fg_payload = {
        "endpoint": f"{cfg['url']}/api/chat",
        "model": cfg["functiongemma_model"],
        "stream": False,
        "options": {"temperature": cfg["temperature"]},
        "messages": [{"role": "user", "content": message}],
    }
    echo_functiongemma_terminal("Invio richiesta FunctionGemma", fg_payload)

    ok_llm, llm_output = call_functiongemma_from_text(message)
    if not ok_llm:
        echo_functiongemma_terminal("Errore risposta FunctionGemma", {"error": llm_output})
        add_log(f"Errore FunctionGemma: {llm_output}", "error")
        return jsonify({"success": False, "message": f"Errore FunctionGemma: {llm_output}"})

    echo_functiongemma_terminal("Risposta FunctionGemma ricevuta", {"output": llm_output})
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
    Returns JSON: {wifi_connected, stream_active, battery, rc_speed}
    """
    with _state_lock:
        return jsonify({
            "wifi_connected": app_state["wifi_connected"],
            "stream_active":  app_state["stream_active"],
            "battery":        app_state["battery"],
            "rc_speed":       DRONE_RC_SPEED,
        })


@app.route("/api/logs", methods=["GET", "POST"])
def get_logs():
    """
    Return all stored log entries.
    Returns JSON: {logs: [...]}
    """
    if request.method == "POST":
        action = str(request.args.get("action", "")).strip().lower()
        if action == "clear":
            return clear_logs()
        return jsonify({"success": False, "message": "Azione non supportata"}), 400

    with _state_lock:
        return jsonify({"logs": list(app_state["logs"])})


@app.route("/api/logs/clear", methods=["POST", "DELETE"])
def clear_logs():
    """
    Svuota completamente il log in memoria lato server.
    Returns JSON: {success, message}
    """
    with _state_lock:
        cleared_count = len(app_state["logs"])
        app_state["logs"].clear()
    return jsonify({
        "success": True,
        "message": f"Log server svuotati ({cleared_count})",
        "cleared": cleared_count,
    })


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
    last_sent = time.perf_counter()
    last_ocr_result_id = 0
    frames_sent = 0
    last_fps_update = time.perf_counter()
    no_frame_since = 0.0

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
                if no_frame_since <= 0.0:
                    no_frame_since = time.monotonic()
                elif (time.monotonic() - no_frame_since) > 3.0:
                    _hard_disconnect_drone(
                        "Stream senza frame per oltre 3 secondi",
                        disconnect_wifi=False,
                        force_wifi_state=wifi.is_connected_to(DRONE_WIFI_SSID),
                        log_level="error",
                    )
                    no_frame_since = 0.0
                time.sleep(0.01)
                continue
            no_frame_since = 0.0

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
            min_interval = 1.0 / max(1.0, TARGET_FPS)
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

        except Exception as exc:
            # ── Fallback visivo + teardown hard ───────────────────────────
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

            _hard_disconnect_drone(
                f"Errore stream MJPEG: {exc}",
                disconnect_wifi=False,
                force_wifi_state=wifi.is_connected_to(DRONE_WIFI_SSID),
                log_level="error",
            )
            time.sleep(0.3)


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
        "DRONE_SPEED": "Velocità di volo di default in cm/s (10-100, default: 30)",
        "DRONE_RC_SPEED": "Velocità controllo RC da tastiera (0-100, dove 100 = velocità massima, default: 70)",
        "LOG_MAX_ENTRIES": "Numero massimo di entry nel log (0-100)",
        "DRONE_WIFI_SSID": "Nome della rete WiFi del drone (es. TELLO-XXXXXX)",
        "WIFI_CONNECT_TIMEOUT": "Timeout connessione WiFi in secondi (15 = default)",
        "JPEG_QUALITY": "Qualità JPEG per i frame (0-100, default: 80)",
        "TARGET_FPS": "Frame rate target della dashboard (es. 30.0)",
        "CONTRAST_ALPHA": "Fattore contrasto immagine (default: 1.05)",
        "CONTRAST_BETA": "Offset contrasto immagine (default: 2)",
        "OCR_ENABLED": "Abilita invio frame a server OCR remoto",
        "FUNCTIONGEMMA_ENABLED": "Abilita interpretazione comandi chat con FunctionGemma (se false, comandi esatti)",
        "COMMAND_DEDUP_WINDOW_SECONDS": "Finestra anti-duplicato per lo stesso comando eseguito (secondi, 0 = disabilitato)",
        "CHAT_MESSAGE_DEDUP_WINDOW_SECONDS": "Finestra anti-duplicato per messaggi chat identici consecutivi (secondi, 0 = disabilitato)",
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
        "KEEPALIVE_COOLDOWN_SECONDS": "Cooldown tra keepalive consecutivi quando buffer vuoto (default: 5.0)",
        "KEEPALIVE_USE_NO_RESPONSE": "Se true usa send_command_without_return('keepalive') invece di attendere risposta 'ok'",
        "MEDIA_OUTPUT_DIR": "Directory dove salvare foto/video (default: captures)",
        "MEDIA_VIDEO_FPS": "FPS di registrazione video (default: 20.0)",
        "MEDIA_VIDEO_CODEC": "Codec FourCC video (default: mp4v)",
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
        changed_keys: set[str] = set()
        
        for line in lines:
            stripped = line.strip()
            
            # Keep blank lines and comments as-is
            if not stripped or stripped.startswith('#'):
                new_lines.append(line)
            elif '=' in stripped:
                key = stripped.split('=', 1)[0].strip()
                if key in new_config:
                    # Replace with new value
                    incoming = str(new_config[key]).strip()
                    old_value = stripped.split('=', 1)[1].strip()
                    new_lines.append(f"{key}={incoming}\n")
                    processed_keys.add(key)
                    if incoming != old_value:
                        changed_keys.add(key)
                else:
                    # Keep existing line
                    new_lines.append(line)
        
        # Add any new keys that were not in the original file
        for key, value in new_config.items():
            if key not in processed_keys:
                incoming = str(value).strip()
                new_lines.append(f"{key}={incoming}\n")
                changed_keys.add(key)
        
        # Write back to .env
        with open(env_file_path, 'w', encoding='utf-8') as f:
            f.writelines(new_lines)

        applied_keys, restart_required = _reload_runtime_config(changed_keys)

        restart_msg = ""
        if restart_required:
            restart_msg = (
                " Riavvio applicazione richiesto per: "
                + ", ".join(restart_required)
            )
        
        add_log("Configurazione salvata in .env", "success")
        return jsonify({
            "success": True,
            "message": "Configurazione salvata e applicata a runtime." + restart_msg,
            "applied_keys": applied_keys,
            "restart_required_keys": restart_required,
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
        data = request.get_json(silent=True) or {}
        disconnect_wifi = bool(data.get("disconnect_wifi", False))

        wifi_ok = _hard_disconnect_drone(
            "Cleanup connessioni drone completato",
            disconnect_wifi=disconnect_wifi,
            force_wifi_state=False if disconnect_wifi else None,
            log_level="system",
        )

        message = "Drone disconnesso e liberato"
        if disconnect_wifi:
            message = (
                "Drone disconnesso e WiFi liberato"
                if wifi_ok
                else "Drone disconnesso (disconnessione WiFi non confermata)"
            )

        return jsonify({
            "success": True,
            "message": message,
            "wifi_disconnected": wifi_ok if disconnect_wifi else None,
        })
    except Exception as exc:
        error_msg = f"Errore cleanup: {str(exc)}"
        add_log(error_msg, "error")
        return jsonify({"success": False, "message": error_msg})


########################################################################
#  VOICE MODULE  –  Vosk speech-to-text integration
########################################################################

def _looks_like_vosk_model_dir(path: Path) -> bool:
    """Ritorna True se la cartella contiene una struttura modello Vosk valida."""
    return path.is_dir() and (path / "am" / "final.mdl").exists() and (path / "conf" / "mfcc.conf").exists()


def _resolve_vosk_model_path() -> str | None:
    """Trova il modello Vosk locale ed estrae automaticamente lo zip se necessario."""
    project_root = Path(__file__).resolve().parent
    vosk_dir = project_root / "vosk-voice"

    if not vosk_dir.exists():
        return None

    candidates: list[Path] = []

    env_model_path = os.getenv("VOSK_MODEL_PATH", "").strip()
    if env_model_path:
        explicit = Path(env_model_path)
        if not explicit.is_absolute():
            explicit = project_root / explicit
        candidates.append(explicit)

    model_it = vosk_dir / "model-it"
    candidates.append(model_it)
    candidates.extend(sorted(p for p in vosk_dir.glob("vosk-model-*") if p.is_dir()))

    for candidate in candidates:
        if _looks_like_vosk_model_dir(candidate):
            return str(candidate)

    for zip_path in sorted(vosk_dir.glob("vosk-model-*.zip")):
        try:
            with zipfile.ZipFile(zip_path, "r") as archive:
                archive.extractall(vosk_dir)
            print(f"[app] Modello Vosk estratto da zip: {zip_path}")
        except Exception as exc:
            print(f"[app] Errore estrazione zip modello Vosk ({zip_path}): {exc}")

    extracted_candidates = sorted(p for p in vosk_dir.glob("vosk-model-*") if p.is_dir())
    if not model_it.exists() and extracted_candidates:
        try:
            extracted_candidates[0].rename(model_it)
        except Exception:
            # Se la rename fallisce, useremo direttamente la cartella estratta.
            pass

    candidates = [model_it] + extracted_candidates
    for candidate in candidates:
        if _looks_like_vosk_model_dir(candidate):
            return str(candidate)

    return None


# Percorso al modello Vosk italiano (con fallback a zip nella cartella vosk-voice)
VOSK_MODEL_PATH = _resolve_vosk_model_path()


def _on_voice_transcription(text: str, session_id: int) -> None:
    """
    Callback chiamato quando Vosk produce una trascrizione finale.
    Il testo viene solo loggato: l'esecuzione del comando passa dal
    percorso chat (/api/send_message) per evitare duplicazioni.
    """
    print(f"{ANSI_CHAT}[voice] Trascrizione vocale: {text} (sessione: {session_id}){ANSI_RESET}")
    add_log(f"Vocale: {text}", "user")
    add_log("Trascrizione vocale inoltrata alla chat UI", "system")


def _on_voice_partial(text: str, session_id: int) -> None:
    """Callback per trascrizioni parziali (opzionale, solo log)."""
    print(f"[voice] Parziale: {text}")


# Inizializza il modulo vocale se il modello esiste
if VOSK_MODEL_PATH:
    try:
        init_voice_module(
            app,
            model_path=VOSK_MODEL_PATH,
            on_transcription=_on_voice_transcription,
            on_partial=_on_voice_partial,
        )
        print(f"[app] Voice module inizializzato con modello: {VOSK_MODEL_PATH}")
    except Exception as e:
        print(f"[app] Errore inizializzazione voice module: {e}")
else:
    print("[app] Modello Vosk non trovato in ./vosk-voice (atteso model-it o zip vosk-model-*.zip)")


def _register_shutdown_hooks() -> None:
    """Registra cleanup all'uscita evitando il processo parent del reloader Flask."""
    if FLASK_DEBUG and os.getenv("WERKZEUG_RUN_MAIN") != "true":
        return
    atexit.register(_shutdown_cleanup)


_register_shutdown_hooks()


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
        ssl_context='adhoc'
    )
