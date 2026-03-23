#!/usr/bin/env python3
"""
########################################################################
#  Count Dataset Entries
#  
#  Conta il numero di entry nel dataset JSONL.
########################################################################
"""

import json
import sys
from pathlib import Path

def count_entries(dataset_path: str) -> int:
    """Conta le entry nel dataset JSONL."""
    path = Path(dataset_path)
    
    if not path.exists():
        print(f"[ERROR] File not found: {dataset_path}")
        return 0
    
    count = 0
    errors = 0
    
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                if not line.strip():
                    continue
                
                try:
                    json.loads(line)
                    count += 1
                except json.JSONDecodeError as e:
                    errors += 1
                    print(f"[WARN] JSON error at line {line_no}: {e}")
    
    except Exception as exc:
        print(f"[ERROR] {exc}")
        return 0
    
    print(f"Total entries: {count}")
    if errors:
        print(f"Parse errors: {errors}")
    
    return count


if __name__ == "__main__":
    dataset = sys.argv[1] if len(sys.argv) > 1 else "tello_dataset_FINAL.jsonl"
    count_entries(dataset)
