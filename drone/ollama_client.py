"""
########################################################################
#  drone/ollama_client.py  -  Client Ollama riusabile via .env
#
#  Espone funzioni per:
#    - inviare testo OCR a Ollama
#    - pingare l'API Ollama
#    - pingare specificamente il modello FunctionGemma
#    - parser output FunctionGemma per estrarre comandi
########################################################################
"""

from __future__ import annotations

import os
import re
from typing import Optional

import requests
from dotenv import load_dotenv

# Carica variabili da .env (idempotente)
load_dotenv()


def _env_str(name: str, default: str) -> str:
    return str(os.getenv(name, default)).strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def get_ollama_config() -> dict:
    """Ritorna la configurazione Ollama letta da .env con fallback robusti."""
    return {
        "url": _env_str("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/"),
        "model": _env_str("OLLAMA_MODEL", "functiongemma_tello_current"),
        "functiongemma_model": _env_str("OLLAMA_FUNCTIONGEMMA_MODEL", _env_str("OLLAMA_MODEL", "functiongemma_tello_current")),
        "timeout": _env_int("OLLAMA_TIMEOUT", 30),
        "temperature": float(os.getenv("OLLAMA_TEMPERATURE", "0")),
    }


def call_ollama_chat(user_text: str, model: Optional[str] = None) -> tuple[bool, str]:
    """
    Invia un messaggio a /api/chat e ritorna (ok, output|errore).
    """
    cfg = get_ollama_config()
    endpoint = f"{cfg['url']}/api/chat"

    payload = {
        "model": model or cfg["model"],
        "messages": [{"role": "user", "content": user_text}],
        "stream": False,
        "options": {"temperature": cfg["temperature"]},
    }

    try:
        response = requests.post(endpoint, json=payload, timeout=cfg["timeout"])
        response.raise_for_status()
        data = response.json()
        content = data.get("message", {}).get("content", "")
        if not content.strip():
            return False, "Risposta Ollama vuota"
        return True, content.strip()
    except Exception as exc:
        return False, str(exc)


def call_ollama_from_ocr_words(ocr_words: list[str]) -> tuple[bool, str, str]:
    """
    Concatena parole OCR con spazio, invia a Ollama e ritorna:
    (ok, output_ollama|errore, input_concatenato).
    """
    ocr_input = " ".join(word.strip() for word in ocr_words if str(word).strip()).strip()
    if not ocr_input:
        return False, "Input OCR vuoto", ""

    ok, result = call_ollama_chat(ocr_input)
    return ok, result, ocr_input


def call_functiongemma_from_text(user_text: str) -> tuple[bool, str]:
    """
    Invia testo libero al modello FunctionGemma configurato via .env.
    """
    cfg = get_ollama_config()
    return call_ollama_chat(user_text, model=cfg["functiongemma_model"])


def ping_ollama_server() -> tuple[bool, str]:
    """
    Ping base del server Ollama via /api/tags.
    """
    cfg = get_ollama_config()
    endpoint = f"{cfg['url']}/api/tags"

    try:
        response = requests.get(endpoint, timeout=cfg["timeout"])
        response.raise_for_status()
        data = response.json()
        models = data.get("models", [])
        return True, f"Server OK, modelli trovati: {len(models)}"
    except Exception as exc:
        return False, str(exc)


def ping_function_gemma() -> tuple[bool, str]:
    """
    Ping funzionale del modello FunctionGemma con prompt minimo.
    """
    cfg = get_ollama_config()
    test_prompt = _env_str("OLLAMA_PING_TEXT", "ping")
    return call_ollama_chat(test_prompt, model=cfg["functiongemma_model"])


# ================================================================
#  Parsing FunctionGemma output
# ================================================================

def parse_function_calls(model_output: str) -> list[dict]:
    """
    Parsa l'output del modello nel formato FunctionGemma.
    Formato: <start_function_call>call:func_name{key:value,...}<end_function_call>
    
    Returns:
        Lista di dict {"function": str, "args": dict}
    """
    results = []
    call_pattern = r'call:(?P<name>\w+)\{(?P<args>.*?)\}'
    kv_pattern = r'(?P<key>\w+)\s*:\s*(?P<val>[^,}]+)'

    for part in model_output.split('<start_function_call>'):
        if not part.strip():
            continue
        match = re.search(call_pattern, part)
        if not match:
            continue

        func_name = match.group("name")
        args_str = match.group("args").strip()
        args = {}

        for kv in re.finditer(kv_pattern, args_str):
            key = kv.group("key")
            val = kv.group("val").strip()
            # Conversione di tipo automatica
            if val in ("None", "null"):
                val = None
            elif val.lower() == "true":
                val = True
            elif val.lower() == "false":
                val = False
            else:
                try:
                    val = float(val) if "." in val else int(val)
                except ValueError:
                    pass  # rimane stringa
            if val is not None:
                args[key] = val

        results.append({"function": func_name, "args": args})

    return results


def extract_first_command(model_output: str) -> Optional[tuple[str, dict]]:
    """
    Estrae il primo comando dal modello e ritorna (nome_funzione, args).
    """
    calls = parse_function_calls(model_output)
    if not calls:
        return None
    
    first = calls[0]
    return first["function"], first.get("args", {})


def extract_all_commands(model_output: str) -> list[tuple[str, dict]]:
    """
    Estrae tutti i comandi dal modello in ordine di apparizione.
    """
    calls = parse_function_calls(model_output)
    return [(call.get("function", ""), call.get("args", {})) for call in calls if call.get("function")]


# ================================================================
#  Mapping e Esecuzione Comandi
# ================================================================

def map_function_to_command(func_name: str) -> str:
    """
    Mappa i nomi di funzione FunctionGemma ai nomi canonici di CommandExecutor.
    
    FunctionGemma nomi tipici:
      - takeoff, land, emergency
      - move_forward, move_back, move_left, move_right, move_up, move_down
      - rotate_clockwise, rotate_counter_clockwise
      - flip_forward, flip_back, flip_left, flip_right
    
    CommandExecutor nomi tipici:
      - takeoff, land, emergency
      - move_forward, move_back, move_left, move_right, move_up, move_down
      - rotate_cw, rotate_ccw
      - flip_forward, flip_back, flip_left, flip_right
    """
    mapping = {
        "rotate_clockwise": "rotate_cw",
        "rotate_counter_clockwise": "rotate_ccw",
        # altri nomi potevano non corrispondenza vengono utilizzati come è
    }
    return mapping.get(func_name, func_name)


def execute_function_command(func_name: str, args: dict, command_executor) -> tuple[bool, str]:
    """
    Esegue il comando sul drone tramite CommandExecutor.
    
    Args:
        func_name: nome della funzione FunctionGemma
        args: dict con argomenti (es. {"cm": 50})
        command_executor: istanza di CommandExecutor
    
    Returns:
        (success: bool, message: str)
    """
    try:
        canonical_name = map_function_to_command(func_name)
        
        # Estrai l'argomento se presente (di solito è "cm" o "degrees")
        argument = None
        if args:
            # Prova chiavi comuni
            for key in ["cm", "degrees", "arg", "argument", "value"]:
                if key in args:
                    argument = args[key]
                    break
            # Se nulla matched, prendi il primo valore
            if argument is None and args:
                argument = list(args.values())[0]
        
        ok, msg = command_executor.run(canonical_name, argument)
        return ok, msg
    except Exception as exc:
        return False, str(exc)


def parse_model_output_to_executor_commands(model_output: str) -> list[tuple[str, Optional[int]]]:
    """
    Converte output FunctionGemma in comandi pronti per CommandExecutor.run.

    Returns:
        Lista di tuple (command_name, argument) in ordine.
    """
    commands: list[tuple[str, Optional[int]]] = []

    for func_name, args in extract_all_commands(model_output):
        canonical_name = map_function_to_command(func_name)

        argument = None
        if args:
            for key in ["cm", "degrees", "arg", "argument", "value"]:
                if key in args:
                    argument = args[key]
                    break
            if argument is None:
                argument = next(iter(args.values()))

        if argument is not None:
            try:
                argument = int(argument)
            except (TypeError, ValueError):
                # Lasciare come None evita errori su comandi senza parametri validi.
                argument = None

        commands.append((canonical_name, argument))

    return commands
