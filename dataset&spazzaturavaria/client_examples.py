#!/usr/bin/env python3
"""
RestOCR API Client - Esempi di utilizzo da macchina remota

Usaggio:
    python3 client_examples.py --server 192.168.1.100 --image /path/to/image.jpg
"""

import requests
import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Any

class RestOCRClient:
    """Client per RestOCR API"""
    
    def __init__(self, server_url: str, timeout: int = 60):
        """
        Inizializza client OCR
        
        Args:
            server_url: URL base del server (es: http://192.168.1.100:8000)
            timeout: Timeout per le richieste in secondi
        """
        self.server_url = server_url.rstrip('/')
        self.timeout = timeout
        
    def _check_server(self) -> bool:
        """Verifica se il server è raggiungibile"""
        try:
            response = requests.get(
                f"{self.server_url}/docs",
                timeout=5
            )
            return response.status_code == 200
        except Exception:
            return False
    
    def predict(self, image_path: str) -> Dict[str, Any]:
        """
        Esegue OCR su un'immagine
        
        Args:
            image_path: Percorso dell'immagine locale
            
        Returns:
            Dictionary con risultati OCR:
            {
                "results": [
                    {
                        "text": "testo riconosciuto",
                        "confidence": 0.95,
                        "bbox": [[x1, y1], [x2, y2], [x3, y3], [x4, y4]]
                    },
                    ...
                ]
            }
        """
        image_file = Path(image_path)
        
        if not image_file.exists():
            raise FileNotFoundError(f"Immagine non trovata: {image_path}")
        
        if not image_file.is_file():
            raise ValueError(f"Non è un file: {image_path}")
        
        # Formati supportati
        supported_formats = {'.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.webp'}
        if image_file.suffix.lower() not in supported_formats:
            raise ValueError(f"Formato non supportato: {image_file.suffix}")
        
        with open(image_file, 'rb') as f:
            files = {'file': f}
            response = requests.post(
                f"{self.server_url}/predict",
                files=files,
                timeout=self.timeout
            )
        
        if response.status_code != 200:
            raise RuntimeError(
                f"Errore server {response.status_code}: {response.text}"
            )
        
        return response.json()


def example_basic():
    """Esempio base di utilizzo"""
    print("=" * 70)
    print("ESEMPIO 1: Utilizzo Base")
    print("=" * 70)
    
    client = RestOCRClient("http://192.168.1.100:8000")
    
    # Verifica server
    if not client._check_server():
        print("✗ Server non raggiungibile")
        return
    
    print("✓ Server raggiungibile")
    
    try:
        results = client.predict("/path/to/image.jpg")
        
        for item in results['results']:
            print(f"Testo: {item['text']}")
            print(f"Confidenza: {item['confidence']:.2%}\n")
            
    except Exception as e:
        print(f"✗ Errore: {e}")


def example_batch():
    """Esempio: Elaborazione batch di immagini"""
    print("=" * 70)
    print("ESEMPIO 2: Batch Processing")
    print("=" * 70)
    
    client = RestOCRClient("http://192.168.1.100:8000")
    
    images = [
        "/path/to/image1.jpg",
        "/path/to/image2.jpg",
        "/path/to/image3.jpg"
    ]
    
    results = {}
    
    for image_path in images:
        try:
            print(f"Elaboro: {image_path}...")
            ocr_result = client.predict(image_path)
            results[image_path] = ocr_result
            print(f"✓ {len(ocr_result['results'])} elementi rilevati\n")
            
        except Exception as e:
            print(f"✗ Errore su {image_path}: {e}\n")
            results[image_path] = None
    
    # Salva risultati
    with open("batch_results.json", "w") as f:
        json.dump(results, f, indent=2)
    
    print("Risultati salvati in batch_results.json")


def example_filter_confidence():
    """Esempio: Filtraggio per confidenza minima"""
    print("=" * 70)
    print("ESEMPIO 3: Filtraggio per Confidenza")
    print("=" * 70)
    
    client = RestOCRClient("http://192.168.1.100:8000")
    
    MIN_CONFIDENCE = 0.90  # Solo testi con 90%+ confidenza
    
    try:
        results = client.predict("/path/to/image.jpg")
        
        high_confidence = [
            item for item in results['results']
            if item['confidence'] >= MIN_CONFIDENCE
        ]
        
        print(f"Totale elementi: {len(results['results'])}")
        print(f"Con confidenza >= {MIN_CONFIDENCE:.0%}: {len(high_confidence)}\n")
        
        for item in high_confidence:
            print(f"✓ {item['text']} ({item['confidence']:.2%})")
            
    except Exception as e:
        print(f"✗ Errore: {e}")


def example_save_results():
    """Esempio: Salvataggio risultati in diversi formati"""
    print("=" * 70)
    print("ESEMPIO 4: Salvataggio Risultati")
    print("=" * 70)
    
    client = RestOCRClient("http://192.168.1.100:8000")
    
    try:
        results = client.predict("/path/to/image.jpg")
        
        # Formato JSON
        with open("ocr_results.json", "w") as f:
            json.dump(results, f, indent=2)
        print("✓ Salvato: ocr_results.json")
        
        # Formato TXT (solo testo)
        with open("ocr_results.txt", "w") as f:
            for item in results['results']:
                f.write(f"{item['text']}\n")
        print("✓ Salvato: ocr_results.txt")
        
        # Formato CSV
        import csv
        with open("ocr_results.csv", "w", newline='') as f:
            writer = csv.writer(f)
            writer.writerow(["testo", "confidenza"])
            for item in results['results']:
                writer.writerow([item['text'], f"{item['confidence']:.4f}"])
        print("✓ Salvato: ocr_results.csv")
        
    except Exception as e:
        print(f"✗ Errore: {e}")


def example_error_handling():
    """Esempio: Gestione errori robusta"""
    print("=" * 70)
    print("ESEMPIO 5: Gestione Errori")
    print("=" * 70)
    
    server_url = "http://192.168.1.100:8000"
    client = RestOCRClient(server_url)
    
    # Test 1: Server non raggiungibile
    print("Test 1: Server non raggiungibile")
    bad_client = RestOCRClient("http://999.999.999.999:8000")
    try:
        bad_client.predict("/tmp/test.jpg")
    except Exception as e:
        print(f"✓ Errore catturato: {type(e).__name__}\n")
    
    # Test 2: File non trovato
    print("Test 2: File non trovato")
    try:
        client.predict("/path/that/does/not/exist.jpg")
    except FileNotFoundError as e:
        print(f"✓ Errore catturato: {e}\n")
    
    # Test 3: Formato non supportato
    print("Test 3: Formato non supportato")
    try:
        client.predict("/path/to/file.txt")
    except ValueError as e:
        print(f"✓ Errore catturato: {e}\n")


def example_with_retry():
    """Esempio: Retry con backoff esponenziale"""
    print("=" * 70)
    print("ESEMPIO 6: Retry con Backoff")
    print("=" * 70)
    
    import time
    
    def predict_with_retry(
        client: RestOCRClient,
        image_path: str,
        max_retries: int = 3,
        backoff_factor: float = 2.0
    ) -> Dict[str, Any]:
        """OCR con retry automatico"""
        
        for attempt in range(max_retries):
            try:
                return client.predict(image_path)
            except Exception as e:
                if attempt == max_retries - 1:
                    raise
                
                wait_time = backoff_factor ** attempt
                print(f"Tentativo {attempt + 1} fallito, riprovo tra {wait_time}s...")
                time.sleep(wait_time)
    
    client = RestOCRClient("http://192.168.1.100:8000")
    
    try:
        results = predict_with_retry(client, "/path/to/image.jpg")
        print(f"✓ OCR riuscito dopo retry: {len(results['results'])} elementi")
    except Exception as e:
        print(f"✗ Fallito dopo tutti i retry: {e}")


def example_repo_samples(server_url: str):
    """Esempio: Test rapido con le immagini sample del repository"""
    print("=" * 70)
    print("ESEMPIO 7: Test immagini sample")
    print("=" * 70)

    client = RestOCRClient(server_url)
    samples_dir = Path(__file__).resolve().parent / "test_images"
    sample_files = [
        samples_dir / "sample_01.png",
        samples_dir / "sample_02.png",
        samples_dir / "sample_03.png",
    ]

    if not client._check_server():
        print(f"✗ Server non raggiungibile: {server_url}")
        return

    print(f"✓ Server raggiungibile: {server_url}")

    for image_path in sample_files:
        if not image_path.exists():
            print(f"✗ File mancante: {image_path}")
            continue

        try:
            result = client.predict(str(image_path))
            print(f"\n[{image_path.name}] ✓ {len(result['results'])} elementi rilevati")
            for item in result["results"][:3]:
                print(f"- {item['text']} ({item['confidence']:.2%})")
        except Exception as e:
            print(f"\n[{image_path.name}] ✗ Errore: {e}")


# Interfaccia CLI
def main():
    parser = argparse.ArgumentParser(
        description="RestOCR API Client - Esempi di utilizzo"
    )
    parser.add_argument(
        "--server",
        default="http://127.0.0.1:8000",
        help="URL server OCR (default: http://127.0.0.1:8000)"
    )
    parser.add_argument(
        "--image",
        help="Percorso immagine da elaborare"
    )
    parser.add_argument(
        "--example",
        choices=["basic", "batch", "filter", "save", "errors", "retry", "samples"],
        help="Esegui esempio specifico"
    )
    
    args = parser.parse_args()
    
    if args.image and args.server:
        # Mode: Uso diretto
        client = RestOCRClient(args.server)
        
        try:
            print(f"Connessione a {args.server}...")
            if not client._check_server():
                print("✗ Server non raggiungibile")
                sys.exit(1)
            
            print(f"Elaborazione: {args.image}")
            results = client.predict(args.image)
            
            print(f"\n✓ Rilevati {len(results['results'])} elementi:\n")
            
            for i, item in enumerate(results['results'], 1):
                print(f"{i}. {item['text']}")
                print(f"   Confidenza: {item['confidence']:.2%}")
                print()
                
        except Exception as e:
            print(f"✗ Errore: {e}")
            sys.exit(1)
    
    elif args.example:
        # Mode: Esempi
        examples = {
            "basic": example_basic,
            "batch": example_batch,
            "filter": example_filter_confidence,
            "save": example_save_results,
            "errors": example_error_handling,
            "retry": example_with_retry,
            "samples": lambda: example_repo_samples(args.server),
        }
        examples[args.example]()
    
    else:
        # Mode: Help
        parser.print_help()
        print("\n" + "=" * 70)
        print("ESEMPI DI UTILIZZO:")
        print("=" * 70)
        print("\n1. Test singola immagine:")
        print("   python3 client_examples.py --server http://192.168.1.100:8000 \\")
        print("                              --image /path/to/image.jpg")
        print("\n2. Esegui esempi:")
        print("   python3 client_examples.py --example basic")
        print("   python3 client_examples.py --example batch")
        print("   python3 client_examples.py --example filter")
        print("   python3 client_examples.py --example save")
        print("   python3 client_examples.py --example errors")
        print("   python3 client_examples.py --example retry")
        print("   python3 client_examples.py --example samples --server http://127.0.0.1:8000")


if __name__ == "__main__":
    main()
