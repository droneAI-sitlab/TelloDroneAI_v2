"""
########################################################################
#  drone/frame_reader.py  –  Acquisizione frame dal drone Tello
#
#  Classe DroneReader:
#    start_stream() – connette al drone e avvia lo stream MJPEG
#    get_frame()    – ritorna l'ultimo frame BGR (numpy array) o None
#    stop_stream()  – ferma lo stream e rilascia le risorse
#    disconnect()   – ferma stream e chiude la connessione
#    get_battery()  – interroga il drone per il livello batteria
#
#  Dipendenze: djitellopy, numpy
########################################################################
"""

import threading
import time
from typing import Optional

import cv2
import numpy as np
from djitellopy import Tello
from djitellopy.tello import BackgroundFrameRead


########################################################################
#  COSTANTI
########################################################################

# Pausa dopo streamon() per dare tempo al decoder di inizializzarsi
_STREAM_INIT_DELAY: float = 2.0

# Timeout risposta drone (secondi)
_DRONE_RESPONSE_TIMEOUT: int = 5


########################################################################
#  DRONE READER
########################################################################

class DroneReader:
    """
    Wrapper pulito attorno a djitellopy.Tello.

    Espone solo le operazioni necessarie all'app Flask
    (connect, stream, frame, disconnect, battery) e incapsula
    tutta la gestione degli errori.

    Thread-safety: get_frame() può essere chiamata da thread diversi;
    djitellopy usa internamente un thread per aggiornare il frame.
    """

    def __init__(self, host: str, video_port: int) -> None:
        """
        Args:
            host:       IP del drone (default Tello: 192.168.10.1)
            video_port: Porta del flusso video UDP (default: 11111)
        """
        self._host       = host
        self._video_port = video_port
        self._tello: Optional[Tello] = None
        self._frame_read: Optional[BackgroundFrameRead] = None

        # Flag di stato interni (non sostituiscono app_state in app.py)
        self._stream_ready = False

        # Lock leggero per proteggere i flag
        self._lock = threading.Lock()

    # ----------------------------------------------------------------
    #  STREAM VIDEO
    # ----------------------------------------------------------------

    def start_stream(self) -> bool:
        """
        Connette al drone e avvia lo stream MJPEG in un'unica operazione.
        Replica il pattern di get_tello_client(): connect → streamoff → streamon → get_frame_read.

        Returns:
            True  – stream avviato
            False – errore
        """
        try:
            # Crea un'istanza Tello fresca ad ogni avvio stream
            self._tello = Tello(host=self._host)
            self._tello.RESPONSE_TIMEOUT = _DRONE_RESPONSE_TIMEOUT

            # Connessione al drone
            self._tello.connect()
            print("[frame_reader] Connesso al drone")

            # Stop preventivo in caso di stream già attivo
            try:
                self._tello.streamoff()
            except Exception:
                pass

            self._tello.streamon()

            # Attesa inizializzazione decoder interno
            time.sleep(_STREAM_INIT_DELAY)

            self._frame_read = self._tello.get_frame_read()

            with self._lock:
                self._stream_ready = True
            print("[frame_reader] Stream avviato")
            return True

        except Exception as exc:
            print(f"[frame_reader] Errore start_stream(): {exc}")
            with self._lock:
                self._stream_ready = False
            return False

    # ----------------------------------------------------------------
    #  ACQUISIZIONE FRAME
    # ----------------------------------------------------------------

    def get_frame(self) -> np.ndarray | None:
        """
        Ritorna l'ultimo frame BGR acquisito dal drone.

        Può essere chiamata ripetutamente da un thread generatore.
        Non blocca: ritorna None se lo stream non è attivo o se
        djitellopy non ha ancora un frame disponibile.

        Returns:
            numpy.ndarray (H x W x 3, dtype=uint8, BGR) oppure None
        """
        with self._lock:
            ready = self._stream_ready

        if not ready or self._frame_read is None:
            return None

        frame = self._frame_read.frame

        # Filtra frame vuoti o non inizializzati
        if frame is None or frame.size == 0:
            return None

        # Conversione BGR → RGB (djitellopy restituisce BGR)
        return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

    # ----------------------------------------------------------------
    #  STOP STREAM
    # ----------------------------------------------------------------

    def stop_stream(self) -> None:
        """
        Ferma lo stream video e rilascia il BackgroundFrameRead.
        Sicuro da chiamare anche se lo stream non è attivo.
        """
        with self._lock:
            self._stream_ready = False

        if self._frame_read is not None:
            try:
                self._frame_read.stop()
            except Exception:
                pass
            self._frame_read = None

        if self._tello is not None:
            try:
                self._tello.streamoff()
            except Exception:
                pass

        print("[frame_reader] Stream fermato")

    def cleanup_connection(self) -> None:
        """
        Cleanup aggressivo: ferma stream e chiude la connessione TCP al drone.
        Deve essere chiamato prima di tentare una nuova connessione.
        Libera il drone da connessioni dangling.
        """
        self.stop_stream()

        if self._tello is not None:
            try:
                self._tello.end()
                print("[frame_reader] Connessione TCP al drone chiusa")
            except Exception as exc:
                print(f"[frame_reader] Errore durante end(): {exc}")
            finally:
                self._tello = None

    # ----------------------------------------------------------------
    #  DISCONNECT
    # ----------------------------------------------------------------

    def disconnect(self) -> None:
        """
        Ferma lo stream e chiude la connessione al drone.
        Sicuro da chiamare in qualsiasi stato.
        """
        self.stop_stream()

        if self._tello is not None:
            try:
                self._tello.end()
            except Exception:
                pass

        print("[frame_reader] Disconnesso")

    # ----------------------------------------------------------------
    #  TELEMETRIA
    # ----------------------------------------------------------------

    def get_battery(self) -> int:
        """
        Interroga il drone per il livello di batteria.

        Returns:
            int 0-100 (percentuale), oppure 0 in caso di errore
        """
        with self._lock:
            ready = self._stream_ready

        if not ready or self._tello is None:
            return 0

        try:
            return int(self._tello.get_battery())
        except Exception as exc:
            print(f"[frame_reader] Errore get_battery(): {exc}")
            return 0

    def get_tello(self):
        """
        Espone l'istanza djitellopy.Tello per i moduli che necessitano
        di accesso diretto all'SDK (es. CommandExecutor).

        Returns:
            djitellopy.Tello oppure None se non ancora connesso
        """
        return self._tello
