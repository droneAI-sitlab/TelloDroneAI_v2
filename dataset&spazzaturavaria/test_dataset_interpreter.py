"""
########################################################################
#  Test Dataset Interpreter
#  
#  Script temporaneo per verificare la corretta interpretazione del 
#  dataset tello_dataset_FINAL.jsonl inviando le richieste a FunctionGemma
#  esattamente come fa l'app.py.
#
#  Uso:
#    python test_dataset_interpreter.py [--limit N] [--verbose]
########################################################################
"""

import json
import sys
import argparse
import os
from pathlib import Path
from typing import Optional

from drone.ollama_client import (
    call_functiongemma_from_text,
    parse_model_output_to_executor_commands,
)

# Abilita UTF-8 output su Windows
if sys.platform == "win32":
    os.environ["PYTHONIOENCODING"] = "utf-8"

# Colori ANSI per output
ANSI_RESET  = "\033[0m"
ANSI_GREEN  = "\033[92m"
ANSI_RED    = "\033[91m"
ANSI_YELLOW = "\033[93m"
ANSI_CYAN   = "\033[96m"
ANSI_BOLD   = "\033[1m"


def extract_user_message(messages: list) -> Optional[str]:
    """
    Estrae il messaggio dell'utente dall'array di messaggi.
    Ricerca il messaggiO con role="user" (tipicamente l'ultimo).
    """
    for msg in messages:
        if isinstance(msg, dict) and msg.get("role") == "user":
            return msg.get("content", "").strip()
    return None


def test_dataset_entry(idx: int, entry: dict, verbose: bool = False) -> dict:
    """
    Testa una singola entry del dataset inviandola a FunctionGemma.
    
    Returns:
        Dict con risultati del test: {
            "idx": int,
            "user_message": str,
            "success": bool,
            "ollama_output": str,
            "parsed_commands": list,
            "error": Optional[str]
        }
    """
    result = {
        "idx": idx,
        "user_message": None,
        "success": False,
        "ollama_output": None,
        "parsed_commands": [],
        "error": None,
    }

    # Estrai il messaggio dell'utente
    messages = entry.get("messages", [])
    user_msg = extract_user_message(messages)
    
    if not user_msg:
        result["error"] = "Nessun messaggio utente trovato nella entry"
        return result
    
    result["user_message"] = user_msg

    # Invia a FunctionGemma
    if verbose:
        print(f"  Input: {user_msg[:100]}{'...' if len(user_msg) > 100 else ''}")
    
    ok_llm, llm_output = call_functiongemma_from_text(user_msg)
    
    if not ok_llm:
        result["error"] = f"Errore FunctionGemma: {llm_output}"
        return result
    
    result["ollama_output"] = llm_output
    
    if verbose:
        print(f"  Output: {llm_output[:150]}{'...' if len(llm_output) > 150 else ''}")
    
    # Parsa i comandi
    try:
        parsed = parse_model_output_to_executor_commands(llm_output)
        result["parsed_commands"] = parsed
        result["success"] = True
        
        if verbose:
            if parsed:
                print(f"  Comandi estratti: {len(parsed)}")
                for cmd_name, arg in parsed:
                    arg_str = f" {arg}" if arg is not None else ""
                    print(f"    - {cmd_name}{arg_str}")
            else:
                print(f"  Nessun comando estratto")
    except Exception as exc:
        result["error"] = f"Errore parsing: {str(exc)}"
        return result
    
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Testa il dataset Tello inviando messaggi a FunctionGemma"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Numero massimo di entry da testare (default: tutte)"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Output verboso con dettagli di ogni entry"
    )
    parser.add_argument(
        "--dataset",
        type=str,
        default="tello_dataset_FINAL.jsonl",
        help="Percorso del file dataset (default: tello_dataset_FINAL.jsonl)"
    )
    
    args = parser.parse_args()
    
    dataset_path = Path(args.dataset)
    
    if not dataset_path.exists():
        print(f"{ANSI_RED}[ERROR] File dataset non trovato: {dataset_path}{ANSI_RESET}")
        sys.exit(1)
    
    print(f"{ANSI_BOLD}{ANSI_CYAN}======================================================")
    print(f" Test Dataset Interpreter - Tello Dataset")
    print(f"======================================================{ANSI_RESET}\n")
    
    print(f"Dataset: {dataset_path}")
    print(f"Limite: {args.limit if args.limit else 'nessuno'}")
    print(f"Verbose: {args.verbose}\n")
    
    results = []
    total_entries = 0
    successful = 0
    failed = 0
    total_commands = 0
    
    try:
        with open(dataset_path, "r", encoding="utf-8") as f:
            for line_idx, line in enumerate(f):
                if not line.strip():
                    continue
                
                total_entries += 1
                
                # Limit
                if args.limit and total_entries > args.limit:
                    total_entries -= 1
                    break
                
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError as exc:
                    print(f"{ANSI_RED}[WARN] JSON parse error line {line_idx + 1}: {exc}{ANSI_RESET}")
                    failed += 1
                    continue
                
                # Test entry
                if args.verbose:
                    print(f"{ANSI_YELLOW}[Entry {total_entries}]{ANSI_RESET}")
                
                result = test_dataset_entry(total_entries, entry, verbose=args.verbose)
                results.append(result)
                
                if result["success"]:
                    successful += 1
                    total_commands += len(result["parsed_commands"])
                    if args.verbose:
                        print(f"{ANSI_GREEN}[OK] Success{ANSI_RESET}\n")
                    else:
                        print(f"{ANSI_GREEN}[OK]{ANSI_RESET}", end=" ", flush=True)
                else:
                    failed += 1
                    if args.verbose:
                        print(f"{ANSI_RED}[FAIL] Failed: {result['error']}{ANSI_RESET}\n")
                    else:
                        print(f"{ANSI_RED}[FAIL]{ANSI_RESET}", end=" ", flush=True)
        
        if not args.verbose:
            print("\n")
    
    except KeyboardInterrupt:
        print(f"\n{ANSI_YELLOW}[INTERRUPTED] User interrupted test{ANSI_RESET}")
        sys.exit(0)
    except Exception as exc:
        print(f"{ANSI_RED}[ERROR] Dataset read error: {exc}{ANSI_RESET}")
        sys.exit(1)
    
    # Report finale
    print(f"\n{ANSI_BOLD}{ANSI_CYAN}======================================================")
    print(f" Report Finale")
    print(f"======================================================{ANSI_RESET}\n")
    
    print(f"Total entries:      {total_entries}")
    print(f"Successful:         {ANSI_GREEN}{successful}{ANSI_RESET} ({100*successful//total_entries if total_entries > 0 else 0}%)")
    print(f"Failed:             {ANSI_RED}{failed}{ANSI_RESET} ({100*failed//total_entries if total_entries > 0 else 0}%)")
    print(f"Total commands:     {total_commands}")
    
    if failed > 0:
        print(f"\n{ANSI_YELLOW}Errori rilevati:{ANSI_RESET}")
        for res in results:
            if not res["success"] and res["error"]:
                print(f"  [{res['idx']}] {res['error']}")
                if res["user_message"]:
                    print(f"      Input: {res['user_message'][:80]}...")
    
    print(f"\n{ANSI_BOLD}Status: {'PASS [OK]' if failed == 0 else 'FAIL [ERROR]'}{ANSI_RESET}")
    
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
