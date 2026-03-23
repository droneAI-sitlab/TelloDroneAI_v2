"""
########################################################################
#  Fast Dataset Interpreter Test
#  
#  Versione veloce del test con timeout per avoid blocchi.
#  Utile per verificare rapidamente lo stato del dataset.
########################################################################
"""

import json
import sys
import argparse
from pathlib import Path
from typing import Optional
import signal

from drone.ollama_client import (
    call_functiongemma_from_text,
    parse_model_output_to_executor_commands,
    get_ollama_config,
)

# Colori ANSI
ANSI_RESET  = "\033[0m"
ANSI_GREEN  = "\033[92m"
ANSI_RED    = "\033[91m"
ANSI_YELLOW = "\033[93m"
ANSI_CYAN   = "\033[96m"
ANSI_BOLD   = "\033[1m"


class TimeoutException(Exception):
    pass


def timeout_handler(signum, frame):
    raise TimeoutException("Timeout!")


def extract_user_message(messages: list) -> Optional[str]:
    """Estrae il messaggio dell'utente dall'array di messaggi."""
    for msg in messages:
        if isinstance(msg, dict) and msg.get("role") == "user":
            return msg.get("content", "").strip()
    return None


def test_dataset_entry_fast(idx: int, entry: dict, timeout_sec: int = 30) -> dict:
    """
    Testa una singola entry con timeout.
    """
    result = {
        "idx": idx,
        "user_message": None,
        "success": False,
        "parsed_commands": [],
        "error": None,
    }

    messages = entry.get("messages", [])
    user_msg = extract_user_message(messages)
    
    if not user_msg:
        result["error"] = "No user message"
        return result
    
    result["user_message"] = user_msg[:50]

    try:
        # Set timeout
        signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(timeout_sec)
        
        ok_llm, llm_output = call_functiongemma_from_text(user_msg)
        
        # Disable alarm
        signal.alarm(0)
        
        if not ok_llm:
            result["error"] = "API Error"
            return result
        
        parsed = parse_model_output_to_executor_commands(llm_output)
        result["parsed_commands"] = parsed
        result["success"] = True
        
    except TimeoutException:
        result["error"] = "Timeout"
    except Exception as exc:
        result["error"] = str(type(exc).__name__)
    
    return result


def main():
    parser = argparse.ArgumentParser(description="Fast dataset test")
    parser.add_argument("--limit", type=int, default=100, help="Entries to test")
    parser.add_argument("--timeout", type=int, default=30, help="Timeout per entry (seconds)")
    parser.add_argument("--dataset", type=str, default="tello_dataset_FINAL.jsonl", help="Dataset path")
    
    args = parser.parse_args()
    
    dataset_path = Path(args.dataset)
    if not dataset_path.exists():
        print(f"{ANSI_RED}[ERROR] Dataset not found: {dataset_path}{ANSI_RESET}")
        return 1
    
    print(f"{ANSI_BOLD}{ANSI_CYAN}Fast Dataset Test{ANSI_RESET}")
    print(f"Dataset: {dataset_path}")
    print(f"Limit: {args.limit}, Timeout: {args.timeout}s\n")
    
    cfg = get_ollama_config()
    print(f"Ollama URL: {cfg['url']}")
    print(f"Model: {cfg['functiongemma_model']}\n")
    
    results = []
    total = 0
    success = 0
    timeout_count = 0
    api_error = 0
    
    try:
        with open(dataset_path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                
                total += 1
                if total > args.limit:
                    total -= 1
                    break
                
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    print(f"{ANSI_RED}[FAIL]{ANSI_RESET}", end=" ", flush=True)
                    continue
                
                result = test_dataset_entry_fast(total, entry, args.timeout)
                results.append(result)
                
                if result["success"]:
                    success += 1
                    print(f"{ANSI_GREEN}[OK]{ANSI_RESET}", end=" ", flush=True)
                elif result["error"] == "Timeout":
                    timeout_count += 1
                    print(f"{ANSI_YELLOW}[T/O]{ANSI_RESET}", end=" ", flush=True)
                elif result["error"] == "API Error":
                    api_error += 1
                    print(f"{ANSI_RED}[API]{ANSI_RESET}", end=" ", flush=True)
                else:
                    print(f"{ANSI_RED}[ERR]{ANSI_RESET}", end=" ", flush=True)
                
                if total % 10 == 0:
                    print(f" {total}/{args.limit}")
        
        print("\n")
        
    except KeyboardInterrupt:
        print(f"\n{ANSI_YELLOW}[STOPPED] User interrupted{ANSI_RESET}")
    except Exception as exc:
        print(f"{ANSI_RED}[ERROR] {exc}{ANSI_RESET}")
        return 1
    
    # Report
    print(f"\n{ANSI_BOLD}{ANSI_CYAN}Report{ANSI_RESET}")
    print(f"Total:    {total}")
    print(f"Success:  {ANSI_GREEN}{success}{ANSI_RESET} ({100*success//total if total else 0}%)")
    print(f"Timeout:  {ANSI_YELLOW}{timeout_count}{ANSI_RESET}")
    print(f"API Err:  {ANSI_RED}{api_error}{ANSI_RESET}")
    
    print(f"\n{ANSI_BOLD}Result: {'PASS' if success == total else f'{total-success} FAILURES'}{ANSI_RESET}")
    
    return 0 if success == total else 1


if __name__ == "__main__":
    sys.exit(main())
