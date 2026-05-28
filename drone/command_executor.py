"""
########################################################################
#  drone/command_executor.py  –  Esecuzione comandi sul drone Tello
#
#  Classe CommandExecutor:
#    run(command, argument)  – esegue il comando dalla tabella e ritorna
#                              (success: bool, message: str)
#    available_commands()    – ritorna la lista dei comandi supportati
#
#  TABELLA COMANDI  (COMMAND_TABLE)
#  ─────────────────────────────────────────────────────────────────────
#  Ogni voce mappa un nome-comando → dizionario con:
#    "fn"      : lambda che riceve (tello, argument) e chiama l'SDK
#    "default" : argomento di default se non fornito (None = ignorato)
#    "unit"    : unità dell'argomento per i log ("cm", "°", "cm/s", …)
#    "desc"    : descrizione leggibile per --help / debug
#
#  Comandi di movimento: argomento = distanza in cm (20-500, SDK Tello)
#  Comandi di rotazione: argomento = angolo in gradi (1-360)
#  Comandi di velocità : argomento = cm/s (10-100)
#
#  Dipendenze: djitellopy (esposto da DroneReader.get_tello())
########################################################################
"""

import os
import re
import threading
import time
import json
from typing import Optional, Tuple

from drone.frame_reader import DroneReader
from drone.media_capture import DroneMediaCapture


def _send_keepalive_without_return(tello, _):
    """Invia keepalive senza attendere una risposta dal drone."""
    sender = getattr(tello, "send_command_without_return", None)
    if sender is None:
        raise RuntimeError("SDK djitellopy non espone send_command_without_return")
    sender("keepalive")
    return None


########################################################################
#  COSTANTI E CARICAMENTO MAPPA COMANDI
########################################################################

# Limiti SDK djitellopy per move_* (cm) e rotate_* (°)
_MOVE_MIN:  int = 20
_MOVE_MAX:  int = 500
_ANGLE_MIN: int = 1
_ANGLE_MAX: int = 360
_SPEED_MIN: int = 10
_SPEED_MAX: int = 100

# Priorita' coda comandi (numero minore = precedenza maggiore)
# Nuovo sistema a 3 livelli: 2=emergency(max), 1=normali, 0=keepalive(min)
COMMAND_PRIORITY_EMERGENCY: int = 0
COMMAND_PRIORITY_NORMAL: int = 1  
COMMAND_PRIORITY_KEEPALIVE: int = 2


_FUNCTIONS_MAP = {
    "takeoff": lambda t, _: t.takeoff(),
    "land": lambda t, _: t.land(),
    "emergency": lambda t, _: t.emergency(),
    "move_forward": lambda t, arg: t.move_forward(arg),
    "move_back": lambda t, arg: t.move_back(arg),
    "move_left": lambda t, arg: t.move_left(arg),
    "move_right": lambda t, arg: t.move_right(arg),
    "move_up": lambda t, arg: t.move_up(arg),
    "move_down": lambda t, arg: t.move_down(arg),
    "rotate_cw": lambda t, arg: t.rotate_clockwise(arg),
    "rotate_ccw": lambda t, arg: t.rotate_counter_clockwise(arg),
    "flip_forward": lambda t, _: t.flip_forward(),
    "flip_back": lambda t, _: t.flip_back(),
    "flip_left": lambda t, _: t.flip_left(),
    "flip_right": lambda t, _: t.flip_right(),
    "set_speed": lambda t, arg: t.set_speed(arg),
    "send_keepalive": lambda t, _: t.send_keepalive(),
    "send_keepalive_no_response": _send_keepalive_without_return,
}

COMMAND_TABLE: dict = {}
_ALIASES: dict = {}

_json_path = os.path.join(os.path.dirname(__file__), "commands.json")
try:
    with open(_json_path, "r", encoding="utf-8") as f:
        _commands_data = json.load(f)
        
    for cmd_id, info in _commands_data.items():
        if cmd_id in _FUNCTIONS_MAP:
            COMMAND_TABLE[cmd_id] = {
                "fn": _FUNCTIONS_MAP[cmd_id],
                "default": info.get("default"),
                "unit": info.get("unit", ""),
                "desc": info.get("desc", "")
            }
            for alias in info.get("aliases", []):
                _ALIASES[alias] = cmd_id
except Exception as e:
    print(f"[command_executor] Errore nel caricamento di {_json_path}: {e}")


########################################################################
#  COMMAND EXECUTOR
########################################################################

class CommandExecutor:
    """
    Esecutore di comandi Tello basato su tabella.

    Riceve un'istanza di DroneReader (già connessa) e vi accede
    tramite get_tello() per ottenere il client djitellopy sottostante.

    Uso tipico da app.py:
        executor = CommandExecutor(drone_reader)
        ok, msg  = executor.run("move_forward", 50)
        ok, msg  = executor.run("avanti")          # alias italiano
        ok, msg  = executor.run("move_forward")    # usa default 30 cm
    """

    def __init__(
        self,
        drone_reader: DroneReader,
        media_capture: Optional[DroneMediaCapture] = None,
        dedupe_window_seconds: Optional[float] = None,
    ) -> None:
        """
        Args:
            drone_reader: istanza DroneReader già avviata (stream attivo)
            media_capture: manager opzionale per foto/registrazione video
        """
        self._reader = drone_reader
        self._media_capture = media_capture
        self._state_lock = threading.Lock()
        self._is_flying = False
        self._dedupe_window_seconds = self._resolve_dedupe_window_seconds(dedupe_window_seconds)
        self._dedupe_lock = threading.Lock()
        self._last_success_signature: Optional[tuple[str, Optional[int]]] = None
        self._last_success_monotonic = 0.0
        self._inflight_signatures: set[tuple[str, Optional[int]]] = set()
        self._dedupe_exempt_commands = {
            "send_keepalive",
            "send_keepalive_no_response",
            "get_battery",
            "emergency",
        }
        print(
            "[command_executor] Inizializzato "
            f"(dedupe={self._dedupe_window_seconds:.2f}s)"
        )

    # ----------------------------------------------------------------
    #  API PUBBLICA
    # ----------------------------------------------------------------

    def run(
        self,
        command:  str,
        argument: Optional[int] = None,
    ) -> Tuple[bool, str]:
        """
        Esegue il comando indicato con l'argomento opzionale.

        Args:
            command:  Nome canonico o alias (case-insensitive).
                      Esempi: "move_forward", "avanti", "DECOLLA"
            argument: Valore numerico intero (cm, gradi o cm/s a seconda
                      del comando). Se None viene usato il default della
                      tabella.

        Returns:
            (True,  "ok – <dettaglio>")   – comando eseguito
            (False, "<motivo errore>")    – drone non connesso / errore SDK
        """
        # ── 1. Risoluzione alias → nome canonico ───────────────────────
        canonical = self._resolve(command)
        if canonical is None:
            return False, f"Comando sconosciuto: '{command}'"

        entry = COMMAND_TABLE[canonical]

        if canonical in {"take_photo", "start_video_recording", "stop_video_recording"}:
            return self._run_media_command(canonical)

        # ── 2. Verifica connessione ────────────────────────────────────
        tello = self._reader.get_tello()
        if tello is None:
            return False, "Drone non connesso (SDK) – controlla il WiFi"

        # ── 3. Calcolo argomento effettivo ─────────────────────────────
        effective_arg = self._resolve_argument(canonical, argument, entry)
        if isinstance(effective_arg, str):
            # _resolve_argument ritorna una stringa solo in caso di errore
            return False, effective_arg

        # ── 4. Esecuzione lambda dalla tabella ─────────────────────────
        signature = (canonical, effective_arg)
        skip_duplicate, skip_message, guard_active = self._begin_dedupe_guard(
            canonical=canonical,
            effective_arg=effective_arg,
            unit=entry["unit"],
            signature=signature,
        )
        if skip_duplicate:
            print(f"[command_executor] {skip_message}")
            return True, skip_message

        fn_result = None
        execution_ok = False
        try:
            fn_result = entry["fn"](tello, effective_arg)
            execution_ok = True
        except Exception as exc:
            msg = f"Errore SDK su '{canonical}': {exc}"
            print(f"[command_executor] {msg}")
            return False, msg
        finally:
            if guard_active:
                self._end_dedupe_guard(signature, success=execution_ok)

        if canonical == "takeoff":
            with self._state_lock:
                self._is_flying = True
        elif canonical in {"land", "emergency"}:
            with self._state_lock:
                self._is_flying = False

        # ── 5. Log e risposta ──────────────────────────────────────────
        unit   = entry["unit"]
        if effective_arg is not None:
            detail = f"{effective_arg}{unit}"
        elif fn_result is not None:
            detail = f"{fn_result}{unit}"
        else:
            detail = ""
        log    = f"[command_executor] {canonical} {detail}".strip()
        print(log)
        return True, f"ok – {canonical} {detail}".strip()

    def validate(
        self,
        command: str,
        argument: Optional[int] = None,
    ) -> tuple[bool, str, Optional[str], Optional[int]]:
        """
        Valida comando e argomento senza eseguirli.

        Returns:
            (True, "ok", canonical, effective_arg) se valido
            (False, "<errore>", None, None) se non valido
        """
        canonical = self._resolve(command)
        if canonical is None:
            return False, f"Comando sconosciuto: '{command}'", None, None

        entry = COMMAND_TABLE[canonical]
        effective_arg = self._resolve_argument(canonical, argument, entry)
        if isinstance(effective_arg, str):
            return False, effective_arg, None, None

        return True, "ok", canonical, effective_arg

    @staticmethod
    def _resolve_dedupe_window_seconds(explicit_value: Optional[float]) -> float:
        """Legge la finestra di deduplica da argomento o da variabile ambiente."""
        if explicit_value is not None:
            try:
                return max(0.0, float(explicit_value))
            except (TypeError, ValueError):
                return 0.0

        raw = os.getenv("COMMAND_DEDUP_WINDOW_SECONDS", "1.2")
        try:
            return max(0.0, float(raw))
        except (TypeError, ValueError):
            return 1.2

    def _begin_dedupe_guard(
        self,
        canonical: str,
        effective_arg: Optional[int],
        unit: str,
        signature: tuple[str, Optional[int]],
    ) -> tuple[bool, str, bool]:
        """
        Gestisce deduplica concorrente:
        - blocca duplicati gia' in esecuzione
        - blocca duplicati appena eseguiti nella finestra configurata

        Returns:
            (skip, message, guard_active)
        """
        if self._dedupe_window_seconds <= 0:
            return False, "", False

        if canonical in self._dedupe_exempt_commands:
            return False, "", False

        now = time.monotonic()
        detail = f"{effective_arg}{unit}" if effective_arg is not None else ""

        with self._dedupe_lock:
            if signature in self._inflight_signatures:
                return True, f"skip – duplicate in-flight: {canonical} {detail}".strip(), False

            if self._last_success_signature == signature:
                elapsed = now - self._last_success_monotonic
                if elapsed < self._dedupe_window_seconds:
                    elapsed_ms = int(elapsed * 1000)
                    return (
                        True,
                        f"skip – duplicate recente ({elapsed_ms}ms): {canonical} {detail}".strip(),
                        False,
                    )

            self._inflight_signatures.add(signature)

        return False, "", True

    def _end_dedupe_guard(self, signature: tuple[str, Optional[int]], success: bool) -> None:
        """Chiude la guardia dedupe e aggiorna lo storico in caso di successo."""
        now = time.monotonic()
        with self._dedupe_lock:
            self._inflight_signatures.discard(signature)
            if success:
                self._last_success_signature = signature
                self._last_success_monotonic = now

    def set_dedupe_window_seconds(self, value: float) -> float:
        """Aggiorna a caldo la finestra anti-duplicato dei comandi."""
        try:
            normalized = max(0.0, float(value))
        except (TypeError, ValueError):
            normalized = self._dedupe_window_seconds

        with self._dedupe_lock:
            self._dedupe_window_seconds = normalized
            if normalized <= 0:
                self._inflight_signatures.clear()
                self._last_success_signature = None
                self._last_success_monotonic = 0.0

        return self._dedupe_window_seconds

    def _run_media_command(self, canonical: str) -> Tuple[bool, str]:
        """Esegue i comandi media tramite DroneMediaCapture."""
        if self._media_capture is None:
            return False, "Media capture non configurato"

        try:
            if canonical == "take_photo":
                path = self._media_capture.take_photo()
                return True, f"ok – take_photo {path}"

            if canonical == "start_video_recording":
                path = self._media_capture.start_video_recording()
                return True, f"ok – start_video_recording {path}"

            path = self._media_capture.stop_video_recording()
            return True, f"ok – stop_video_recording {path}"

        except Exception as exc:
            return False, f"Errore media su '{canonical}': {exc}"

    def is_flying(self) -> bool:
        """
        Ritorna True se il drone e' effettivamente in volo.
        
        Verifica lo stato interno e, se possibile, interroga l'altezza reale
        dal drone per rilevare auto-landing non comandato.
        
        Returns:
            True se drone in volo, False se a terra o non connesso
        """
        with self._state_lock:
            internal_state = self._is_flying

        # Usa prima il flag SDK locale (djitellopy.Tello.is_flying),
        # aggiornato direttamente da takeoff()/land().
        tello = self._reader.get_tello()
        if tello is None:
            return False

        sdk_state = bool(getattr(tello, "is_flying", internal_state))
        if not sdk_state:
            with self._state_lock:
                self._is_flying = False
            return False

        # Se SDK dice in volo, prova a validare con altezza reale.
        # In caso di errore telemetria, mantieni lo stato SDK.
        confirmed_flying = True
        
        try:
            height = tello.get_height()
            if height is not None and height <= 10:
                confirmed_flying = False
        except Exception:
            pass

        with self._state_lock:
            self._is_flying = confirmed_flying

        return confirmed_flying

    def reset_flight_state(self) -> None:
        """Reset esplicito da usare dopo cleanup/disconnessioni."""
        with self._state_lock:
            self._is_flying = False

    def reset_runtime_state(self) -> None:
        """
        Reset completo dello stato runtime dopo disconnessioni hard.

        Azzera sia lo stato volo sia la cache anti-duplicato, cosi una nuova
        sessione non eredita segnature del ciclo precedente.
        """
        self.reset_flight_state()
        with self._dedupe_lock:
            self._inflight_signatures.clear()
            self._last_success_signature = None
            self._last_success_monotonic = 0.0

    def run_from_text(self, text: str) -> Tuple[bool, str]:
        """
        Estrae il comando da una stringa OCR libera e lo esegue.

        Usa nomi canonici e alias (_ALIASES), cercandoli come parole
        nel testo (case-insensitive). Se trova un numero intero nel testo
        lo usa come argomento; altrimenti usa i default della tabella.
        """
        raw_text = str(text or "").strip()
        print(f"[command_executor] OCR input concatenato: {raw_text}")

        if not raw_text:
            return False, "Input OCR vuoto"

        # Rimuove porzioni tipo "(95%)" per non confondere il parser numerico.
        cleaned = re.sub(r"\(\s*\d+\s*%\s*\)", " ", raw_text)
        normalized_text = re.sub(r"[_\-]+", " ", cleaned.lower())

        candidates = []
        for canonical in COMMAND_TABLE.keys():
            candidates.append((canonical.replace("_", " "), canonical))
        for alias, canonical in _ALIASES.items():
            candidates.append((alias.replace("_", " "), canonical))

        best_match = None
        best_start = None
        for phrase, canonical in candidates:
            pattern = rf"\b{re.escape(phrase)}\b"
            m = re.search(pattern, normalized_text)
            if not m:
                continue
            if best_start is None or m.start() < best_start:
                best_start = m.start()
                best_match = canonical

        if best_match is None:
            msg = "Nessun comando riconosciuto nel testo OCR"
            print(f"[command_executor] {msg}")
            return False, msg

        arg_match = re.search(r"\b(\d{1,3})\b", cleaned)
        parsed_argument = int(arg_match.group(1)) if arg_match else None

        print(
            f"[command_executor] OCR match -> comando={best_match} "
            f"argomento={parsed_argument}"
        )
        return self.run(best_match, parsed_argument)

    def available_commands(self) -> list:
        """
        Ritorna la lista di tutti i comandi supportati con descrizione.

        Returns:
            Lista di dict {command, aliases, default, unit, desc}
        """
        # Costruisci mappa inversa: canonico → lista alias
        inverse: dict = {}
        for alias, canon in _ALIASES.items():
            inverse.setdefault(canon, []).append(alias)

        result = []
        for name, entry in COMMAND_TABLE.items():
            result.append({
                "command": name,
                "aliases": inverse.get(name, []),
                "default": entry["default"],
                "unit":    entry["unit"],
                "desc":    entry["desc"],
            })
        return result

    @staticmethod
    def emergency_priority_value() -> int:
        """Valore numerico di priorita' per il comando emergency (nuovo sistema: 0)."""
        return COMMAND_PRIORITY_EMERGENCY

    @staticmethod
    def normal_priority_value() -> int:
        """Valore numerico di priorita' per comandi normali (nuovo sistema: 1)."""
        return COMMAND_PRIORITY_NORMAL

    @staticmethod
    def keepalive_priority_value() -> int:
        """Valore numerico di priorita' per keepalive (nuovo sistema: 2)."""
        return COMMAND_PRIORITY_KEEPALIVE

    def get_command_priority(
        self,
        command: str,
        keepalive_commands: Optional[set[str]] = None,
    ) -> int:
        """
        Ritorna la priorita' del comando per la coda (nuovo sistema 3 livelli):
        - emergency: priorita' 2 → valore coda 0 (massima priorita')
        - normali: priorita' 1 → valore coda 1
        - keepalive: priorita' 0 → valore coda 2 (minima priorita')
        """
        normalized = re.sub(r"[\s\-]+", "_", str(command or "").strip().lower())

        if keepalive_commands and normalized in keepalive_commands:
            return COMMAND_PRIORITY_KEEPALIVE

        canonical = self._resolve(normalized)
        if canonical == "emergency":
            return COMMAND_PRIORITY_EMERGENCY
        if canonical in {"send_keepalive", "send_keepalive_no_response"}:
            return COMMAND_PRIORITY_KEEPALIVE
        return COMMAND_PRIORITY_NORMAL

    # ----------------------------------------------------------------
    #  METODI INTERNI
    # ----------------------------------------------------------------

    def _resolve(self, command: str) -> Optional[str]:
        """
        Normalizza il nome del comando: rimuove spazi, converte in
        minuscolo, sostituisce spazi e trattini con underscore, poi
        cerca prima nella tabella principale poi negli alias.

        Returns:
            Nome canonico oppure None se non trovato
        """
        normalized = re.sub(r"[\s\-]+", "_", command.strip().lower())

        if normalized in COMMAND_TABLE:
            return normalized

        if normalized in _ALIASES:
            return _ALIASES[normalized]

        return None

    def _resolve_argument(
        self,
        canonical: str,
        argument:  Optional[int],
        entry:     dict,
    ):
        """
        Calcola l'argomento effettivo da passare alla lambda SDK,
        applicando il default e i limiti dell'SDK.

        Returns:
            int validato, None (per comandi senza argomento),
            oppure str con messaggio di errore
        """
        default = entry["default"]

        # Comandi senza argomento (takeoff, land, flip…)
        if default is None:
            return None

        # Usa il valore fornito oppure il default
        value = int(argument) if argument is not None else default

        # Applica i limiti SDK in base al tipo di comando
        if entry["unit"] == "cm":
            if not (_MOVE_MIN <= value <= _MOVE_MAX):
                return (
                    f"Argomento fuori range per '{canonical}': "
                    f"{value} cm (min {_MOVE_MIN}, max {_MOVE_MAX})"
                )
        elif entry["unit"] == "°":
            if not (_ANGLE_MIN <= value <= _ANGLE_MAX):
                return (
                    f"Argomento fuori range per '{canonical}': "
                    f"{value}° (min {_ANGLE_MIN}, max {_ANGLE_MAX})"
                )
        elif entry["unit"] == "cm/s":
            if not (_SPEED_MIN <= value <= _SPEED_MAX):
                return (
                    f"Argomento fuori range per '{canonical}': "
                    f"{value} cm/s (min {_SPEED_MIN}, max {_SPEED_MAX})"
                )

        return value
