"""
########################################################################
#  drone/ocr_sender.py  –  Invio frame al server OCR remoto
#
#  Classe OCRSender:
#    send_frame(frame) – codifica il frame, lo invia al server OCR se
#                        l'intervallo configurato è trascorso, e
#                        ritorna la lista dei testi riconosciuti
#
#  Flusso:
#    1. Controllo timer (invia solo ogni OCR_INTERVAL_SECONDS)
#    2. Encoding JPEG del frame numpy in memoria (senza file temporanei)
#    3. POST multipart → /predict sul server OCR remoto
#    4. Parsing JSON risposta → lista "testo  (confidenza%)"
#    5. Stampa risultati nel terminale
#
#  Configurazione (letta direttamente da .env tramite load_dotenv):
#    OCR_SERVER_URL       – URL base del server (es. http://192.168.1.100:8000)
#    OCR_TIMEOUT          – Timeout richiesta HTTP in secondi (default: 30)
#    OCR_INTERVAL_SECONDS – Intervallo minimo tra invii successivi (default: 5)
#    OCR_JPEG_QUALITY     – Qualità JPEG per encoding in memoria (default: 85)
#    OCR_MIN_CONFIDENCE   – Soglia minima confidenza per filtrare risultati (default: 0.0)
#
#  Dipendenze: opencv-python, numpy, requests, python-dotenv
########################################################################
"""

import os
import time
import threading
import queue
from typing import Optional

import cv2
import numpy as np
import requests
from dotenv import load_dotenv

# ── Carica variabili d'ambiente dal file .env ─────────────────────────
load_dotenv()


########################################################################
#  COSTANTI
########################################################################

# Endpoint OCR sul server remoto
_PREDICT_ENDPOINT: str = "/predict"

# Separatore visivo per i log nel terminale
_SEPARATOR: str = "─" * 60


########################################################################
#  OCR SENDER
########################################################################

class OCRSender:
    """
    Invia frame del drone al server OCR remoto con cadenza regolabile.

    Istanziata una volta sola in app.py e riutilizzata per ogni frame.
    La configurazione viene letta direttamente dal .env tramite
    load_dotenv(), senza dipendere da parametri passati dall'esterno.

    Il timer interno garantisce che le richieste HTTP vengano spedite
    solo ogni OCR_INTERVAL_SECONDS secondi, indipendentemente dalla
    frequenza con cui viene chiamato send_frame().

    Thread-safety: il timer è protetto da un Lock, quindi send_frame()
    può essere chiamata da thread concorrenti senza problemi.
    """

    def __init__(self) -> None:
        """
        Legge la configurazione OCR dal file .env e inizializza lo stato interno.
        """
        # ── Lettura configurazione da .env ─────────────────────────────
        self._server_url:   str   = os.getenv("OCR_SERVER_URL",       "http://192.168.1.100:8000").rstrip("/")
        self._timeout:      int   = int(os.getenv("OCR_TIMEOUT",           "30"))
        self._interval:     float = float(os.getenv("OCR_INTERVAL_SECONDS", "5"))
        self._jpeg_quality: int   = int(os.getenv("OCR_JPEG_QUALITY",      "85"))
        self._min_conf:     float = float(os.getenv("OCR_MIN_CONFIDENCE",   "0.0"))

        # ── Parametri JPEG encoder (pre-calcolati per efficienza) ──────
        self._encode_params = [cv2.IMWRITE_JPEG_QUALITY, self._jpeg_quality]

        # ── Timer: timestamp (perf_counter) dell'ultimo invio ──────────
        # Inizializzato a 0 così il primo frame viene inviato subito
        self._last_sent: float = 0.0

        # ── Lock per thread-safety del timer e dei risultati ────────────────
        self._lock = threading.Lock()

        # ── Ultimo risultato OCR ricevuto (aggiornato dal thread daemon) ──
        # Contiene la lista di stringhe "testo  (confidenza%)" dell'ultima
        # risposta andata a buon fine; lista vuota finché nessuna risposta
        # è ancora arrivata.
        self.last_results: list = []

        # ── Ultime parole OCR pulite (senza confidenza) + id risultato ──
        self.last_words: list = []
        self.last_result_id: int = 0
        
        # ── Coda per thread worker (1 frame max alla volta) ───────────────
        self._frame_queue = queue.Queue(maxsize=1)
        
        # ── Lancia singolo thread worker daemon ───────────────────────────
        threading.Thread(
            target=self._worker_loop,
            daemon=True,
            name="ocr-worker",
        ).start()

        print(
            f"[ocr_sender] Inizializzato → server={self._server_url}"
            f" | intervallo={self._interval}s"
            f" | confidenza_min={self._min_conf:.0%}"
        )

    def reload_from_env(self) -> None:
        """Rilegge i parametri OCR dal .env e li applica a caldo."""
        load_dotenv(override=True)

        def _safe_int(name: str, fallback: int) -> int:
            raw = os.getenv(name)
            if raw is None:
                return fallback
            try:
                return int(str(raw).strip())
            except (TypeError, ValueError):
                return fallback

        def _safe_float(name: str, fallback: float) -> float:
            raw = os.getenv(name)
            if raw is None:
                return fallback
            try:
                return float(str(raw).strip())
            except (TypeError, ValueError):
                return fallback

        with self._lock:
            self._server_url = os.getenv("OCR_SERVER_URL", self._server_url).rstrip("/")
            self._timeout = _safe_int("OCR_TIMEOUT", self._timeout)
            self._interval = max(0.1, _safe_float("OCR_INTERVAL_SECONDS", self._interval))
            self._jpeg_quality = max(0, min(100, _safe_int("OCR_JPEG_QUALITY", self._jpeg_quality)))
            self._min_conf = max(0.0, min(1.0, _safe_float("OCR_MIN_CONFIDENCE", self._min_conf)))
            self._encode_params = [cv2.IMWRITE_JPEG_QUALITY, self._jpeg_quality]

        print(
            f"[ocr_sender] Config aggiornata → server={self._server_url}"
            f" | intervallo={self._interval}s"
            f" | confidenza_min={self._min_conf:.0%}"
        )

    # ----------------------------------------------------------------
    #  API PUBBLICA
    # ----------------------------------------------------------------

    def send_frame(self, frame: np.ndarray) -> None:
        """
        Invia il frame al server OCR in modo NON bloccante.

        Il controllo del timer avviene nel thread chiamante (è solo un
        confronto, microsecondario).  Se l'intervallo è trascorso la
        richiesta HTTP viene eseguita in un thread daemon separato, così
        il generatore MJPEG non aspetta mai la risposta e lo stream
        rimane fluido.

        Args:
            frame: numpy array (H x W x 3, BGR) proveniente da DroneReader
        """
        if frame is None or frame.size == 0:
            return

        # ── 1. Controllo timer (sincrono, velocissimo) ─────────────────
        now = time.perf_counter()
        with self._lock:
            if now - self._last_sent < self._interval:
                return
            # Aggiorna subito il timestamp per evitare invii doppi
            # da eventuali thread concorrenti
            self._last_sent = now

        # ── 2. Copia difensiva del frame prima di passarlo al thread ───
        # Il generatore MJPEG potrebbe sovrascrivere il buffer numpy
        # mentre il thread è ancora in esecuzione.
        frame_copy = frame.copy()

        # ── 3. Mette il frame in coda al worker (drop se coda piena) ───
        try:
            # Svuota prima la coda per evitare frame stantii
            while not self._frame_queue.empty():
                self._frame_queue.get_nowait()
        except queue.Empty:
            pass
        self._frame_queue.put(frame_copy)

    def _worker_loop(self) -> None:
        """
        Ciclo infinito del worker: attende frame dalla coda, li codifica
        e invia al server HTTP, slegando il flusso dai lag delle richieste.
        """
        while True:
            try:
                frame = self._frame_queue.get()
                self._process_and_send(frame)
            except Exception as e:
                print(f"[ocr_sender] Errore worker thread: {e}")

    def _process_and_send(self, frame: np.ndarray) -> None:
        """
        Esegue la pipeline OCR completa su un frame.

        Non blocca il chiamante; i risultati vengono stampati nel
        terminale al termine della richiesta HTTP.

        Pipeline:
            1. Encoding JPEG in memoria
            2. POST multipart al server OCR
            3. Parsing e filtraggio per confidenza
            4. Stampa risultati nel terminale
        """
        # ── 1. Encoding JPEG in memoria (nessun file temporaneo) ───────
        jpeg_bytes = self._encode_frame(frame)
        if not jpeg_bytes:
            return

        # ── 2. Invio POST multipart al server OCR ──────────────────────
        raw_results = self._post_to_server(jpeg_bytes)
        if raw_results is None:
            return

        # ── 3. Parsing e filtraggio per confidenza ─────────────────────
        filtered_items = self._filter_results(raw_results)
        texts = self._format_results(filtered_items)
        words = [str(item.get("text", "")).strip() for item in filtered_items if str(item.get("text", "")).strip()]

        # ── 4. Salvataggio risultato (thread-safe) ──────────────────────
        with self._lock:
            self.last_results = texts
            self.last_words = words
            self.last_result_id += 1

        # ── 5. Stampa risultati nel terminale ───────────────────────────
        self._print_results(texts, raw_results)

    # ----------------------------------------------------------------
    #  API LETTURA RISULTATI
    # ----------------------------------------------------------------

    def get_last_results(self) -> list:
        """
        Ritorna una copia dell'ultimo risultato OCR ricevuto.

        Thread-safe: può essere chiamato da qualsiasi thread
        (es. route Flask) senza rischi di race condition.

        Returns:
            Lista di stringhe "testo  (confidenza%)" dell'ultima
            risposta andata a buon fine; lista vuota se nessuna
            risposta è ancora arrivata.
        """
        with self._lock:
            return list(self.last_results)

    def get_last_words(self) -> list:
        """
        Ritorna una copia delle ultime parole OCR (solo testo, senza confidenza).
        """
        with self._lock:
            return list(self.last_words)

    def get_last_result_id(self) -> int:
        """
        Ritorna un contatore incrementale dell'ultimo risultato OCR disponibile.
        """
        with self._lock:
            return self.last_result_id

    # ----------------------------------------------------------------
    #  METODI INTERNI – pipeline
    # ----------------------------------------------------------------

    def _encode_frame(self, frame: np.ndarray) -> Optional[bytes]:
        """
        Codifica il frame numpy BGR in bytes JPEG in memoria.

        Returns:
            bytes JPEG oppure None in caso di errore di encoding
        """
        ok, buffer = cv2.imencode(".jpg", frame, self._encode_params)
        if not ok:
            print("[ocr_sender] Errore encoding JPEG del frame")
            return None
        return buffer.tobytes()

    def _post_to_server(self, jpeg_bytes: bytes) -> Optional[list]:
        """
        Esegue il POST multipart a /predict sul server OCR remoto.

        Il file viene inviato con nome "frame.jpg" e Content-Type
        image/jpeg, compatibile con il formato atteso da RestOCR.

        Returns:
            Lista raw di dict {text, confidence, bbox} oppure None su errore
        """
        try:
            files = {"file": ("frame.jpg", jpeg_bytes, "image/jpeg")}
            response = requests.post(
                f"{self._server_url}{_PREDICT_ENDPOINT}",
                files=files,
                timeout=self._timeout,
            )

            if response.status_code != 200:
                print(
                    f"[ocr_sender] Errore HTTP {response.status_code}: "
                    f"{response.text[:200]}"
                )
                return None

            data = response.json()
            return data.get("results", [])

        except requests.exceptions.ConnectionError:
            print(f"[ocr_sender] Server non raggiungibile: {self._server_url}")
            return None

        except requests.exceptions.Timeout:
            print(f"[ocr_sender] Timeout ({self._timeout}s) su {self._server_url}")
            return None

        except Exception as exc:
            print(f"[ocr_sender] Errore inatteso: {exc}")
            return None

    def _filter_results(self, raw_results: list) -> list:
        """
        Filtra e ordina i risultati OCR validi per confidenza decrescente.

        Args:
            raw_results: Lista di dict {text, confidence, bbox} dal server

        Returns:
            Lista di dict OCR validi e ordinati per confidenza decrescente
        """
        # Filtra per soglia minima e scarta entry malformate
        filtered = [
            item for item in raw_results
            if isinstance(item, dict)
            and item.get("confidence", 0.0) >= self._min_conf
            and "text" in item
        ]

        # Ordina per confidenza decrescente (testi più affidabili prima)
        filtered.sort(key=lambda x: x.get("confidence", 0.0), reverse=True)

        return filtered

    def _format_results(self, filtered_results: list) -> list:
        """Costruisce stringhe leggibili "testo  (confidenza%)" dai risultati filtrati."""
        return [
            f"{item['text']}  ({item['confidence']:.0%})"
            for item in filtered_results
        ]

    def _print_results(self, texts: list, raw_results: list) -> None:
        """
        Stampa nel terminale i risultati OCR con formattazione leggibile.

        Args:
            texts:       Lista di stringhe già parsed e filtrate
            raw_results: Lista raw dal server (usata solo per il conteggio totale)
        """
        print(f"\n[ocr_sender] {_SEPARATOR}")
        print(
            f"[ocr_sender] Risultati OCR – {len(texts)} testo/i "
            f"(su {len(raw_results)} totali, soglia >= {self._min_conf:.0%})"
        )
        print(f"[ocr_sender] {_SEPARATOR}")

        if texts:
            for i, text in enumerate(texts, start=1):
                print(f"[ocr_sender]   {i:2d}. {text}")
        else:
            print("[ocr_sender]   (nessun testo sopra la soglia di confidenza)")

        print(f"[ocr_sender] {_SEPARATOR}\n")
