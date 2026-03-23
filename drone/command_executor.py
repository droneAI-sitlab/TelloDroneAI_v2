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

import re
from typing import Optional, Tuple

from drone.frame_reader import DroneReader


########################################################################
#  COSTANTI
########################################################################

# Limiti SDK djitellopy per move_* (cm) e rotate_* (°)
_MOVE_MIN:  int = 20
_MOVE_MAX:  int = 500
_ANGLE_MIN: int = 1
_ANGLE_MAX: int = 360
_SPEED_MIN: int = 10
_SPEED_MAX: int = 100

# Argomenti di default quando il chiamante non ne fornisce uno
_DEFAULT_MOVE:  int = 30   # cm
_DEFAULT_ANGLE: int = 90   # gradi
_DEFAULT_SPEED: int = 30   # cm/s


########################################################################
#  TABELLA COMANDI
#
#  Chiave  : nome canonico del comando (stringa, case-insensitive in run())
#  "fn"    : lambda(tello, arg) → chiamata SDK; arg è già validato
#  "default": valore usato quando argument=None
#  "unit"  : unità per i messaggi di log
#  "desc"  : descrizione breve
########################################################################

COMMAND_TABLE: dict = {
    # ── Decollo / atterraggio ──────────────────────────────────────────
    "takeoff": {
        "fn":      lambda t, _: t.takeoff(),
        "default": None,
        "unit":    "",
        "desc":    "Decolla",
    },
    "land": {
        "fn":      lambda t, _: t.land(),
        "default": None,
        "unit":    "",
        "desc":    "Atterra",
    },
    "emergency": {
        "fn":      lambda t, _: t.emergency(),
        "default": None,
        "unit":    "",
        "desc":    "Arresto motori immediato (emergenza)",
    },

    # ── Movimenti traslazionali ────────────────────────────────────────
    "move_forward": {
        "fn":      lambda t, arg: t.move_forward(arg),
        "default": _DEFAULT_MOVE,
        "unit":    "cm",
        "desc":    "Avanza",
    },
    "move_back": {
        "fn":      lambda t, arg: t.move_back(arg),
        "default": _DEFAULT_MOVE,
        "unit":    "cm",
        "desc":    "Arretra",
    },
    "move_left": {
        "fn":      lambda t, arg: t.move_left(arg),
        "default": _DEFAULT_MOVE,
        "unit":    "cm",
        "desc":    "Trasla a sinistra",
    },
    "move_right": {
        "fn":      lambda t, arg: t.move_right(arg),
        "default": _DEFAULT_MOVE,
        "unit":    "cm",
        "desc":    "Trasla a destra",
    },
    "move_up": {
        "fn":      lambda t, arg: t.move_up(arg),
        "default": _DEFAULT_MOVE,
        "unit":    "cm",
        "desc":    "Sali",
    },
    "move_down": {
        "fn":      lambda t, arg: t.move_down(arg),
        "default": _DEFAULT_MOVE,
        "unit":    "cm",
        "desc":    "Scendi",
    },

    # ── Rotazioni ─────────────────────────────────────────────────────
    "rotate_cw": {
        "fn":      lambda t, arg: t.rotate_clockwise(arg),
        "default": _DEFAULT_ANGLE,
        "unit":    "°",
        "desc":    "Ruota in senso orario",
    },
    "rotate_ccw": {
        "fn":      lambda t, arg: t.rotate_counter_clockwise(arg),
        "default": _DEFAULT_ANGLE,
        "unit":    "°",
        "desc":    "Ruota in senso antiorario",
    },

    # ── Flip ──────────────────────────────────────────────────────────
    "flip_forward": {
        "fn":      lambda t, _: t.flip_forward(),
        "default": None,
        "unit":    "",
        "desc":    "Capriola in avanti",
    },
    "flip_back": {
        "fn":      lambda t, _: t.flip_back(),
        "default": None,
        "unit":    "",
        "desc":    "Capriola indietro",
    },
    "flip_left": {
        "fn":      lambda t, _: t.flip_left(),
        "default": None,
        "unit":    "",
        "desc":    "Capriola a sinistra",
    },
    "flip_right": {
        "fn":      lambda t, _: t.flip_right(),
        "default": None,
        "unit":    "",
        "desc":    "Capriola a destra",
    },

    # ── Velocità ──────────────────────────────────────────────────────
    "set_speed": {
        "fn":      lambda t, arg: t.set_speed(arg),
        "default": _DEFAULT_SPEED,
        "unit":    "cm/s",
        "desc":    "Imposta velocità massima",
    },
}

# ── Alias: parole italiane / inglesi alternative → nome canonico ──────
_ALIASES: dict = {
    # italiano
    "decolla":        "takeoff",
    "atterra":        "land",
    "emergenza":      "emergency",
    "avanti":         "move_forward",
    "indietro":       "move_back",
    "dietro":         "move_back",
    "sinistra":       "move_left",
    "destra":         "move_right",
    "su":             "move_up",
    "sali":           "move_up",
    "giu":            "move_down",
    "scendi":         "move_down",
    "ruota_destra":   "rotate_cw",
    "ruota_sinistra": "rotate_ccw",
    "velocita":       "set_speed",
    # inglese alternativo
    "forward":        "move_forward",
    "back":           "move_back",
    "backward":       "move_back",
    "left":           "move_left",
    "right":          "move_right",
    "up":             "move_up",
    "down":           "move_down",
    "stop":           "emergency",
}


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

    def __init__(self, drone_reader: DroneReader) -> None:
        """
        Args:
            drone_reader: istanza DroneReader già avviata (stream attivo)
        """
        self._reader = drone_reader
        print("[command_executor] Inizializzato")

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

        # ── 2. Verifica connessione ────────────────────────────────────
        tello = self._reader.get_tello()
        if tello is None:
            return False, "Drone non connesso – avvia lo stream prima"

        # ── 3. Calcolo argomento effettivo ─────────────────────────────
        effective_arg = self._resolve_argument(canonical, argument, entry)
        if isinstance(effective_arg, str):
            # _resolve_argument ritorna una stringa solo in caso di errore
            return False, effective_arg

        # ── 4. Esecuzione lambda dalla tabella ─────────────────────────
        try:
            entry["fn"](tello, effective_arg)
        except Exception as exc:
            msg = f"Errore SDK su '{canonical}': {exc}"
            print(f"[command_executor] {msg}")
            return False, msg

        # ── 5. Log e risposta ──────────────────────────────────────────
        unit   = entry["unit"]
        detail = f"{effective_arg}{unit}" if effective_arg is not None else ""
        log    = f"[command_executor] {canonical} {detail}".strip()
        print(log)
        return True, f"ok – {canonical} {detail}".strip()

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
