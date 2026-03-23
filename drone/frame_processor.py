"""
########################################################################
#  drone/frame_processor.py  –  Elaborazione frame con OpenCV + AI
#
#  Classe FrameProcessor:
#    process(frame) – applica la pipeline e ritorna i byte JPEG
#
#  Pipeline attuale:
#    1. Resize al formato di output configurato
#    2. Normalizzazione contrasto (opzionale, via convertScaleAbs)
#    3. Hook AI  ←  _ai_inference(): aggiungi qui i tuoi modelli
#    4. Encoding JPEG
#
#  Dipendenze: opencv-python, numpy
########################################################################
"""

from typing import Optional

import cv2
import numpy as np


########################################################################
#  FRAME PROCESSOR
########################################################################

class FrameProcessor:
    """
    Pipeline di elaborazione frame centralizzata.

    Istanziata una volta sola in app.py e riutilizzata per ogni frame.
    Tutti i parametri vengono passati nel costruttore (letti da .env
    tramite app.py) in modo da mantenere questo modulo indipendente
    da dotenv/flask.
    """

    def __init__(
        self,
        width:           int   = 960,
        height:          int   = 720,
        jpeg_quality:    int   = 80,
        enable_contrast: bool  = True,
        contrast_alpha:  float = 1.05,
        contrast_beta:   int   = 2,
    ) -> None:
        """
        Args:
            width, height:    Dimensioni output del frame (pixel)
            jpeg_quality:     Qualità JPEG 0-100 (80 = buon compromesso)
            enable_contrast:  Attiva normalizzazione contrasto via OpenCV
            contrast_alpha:   Fattore moltiplicativo contrasto (>1  =  più contrasto)
            contrast_beta:    Offset additivo contrasto (luminosità)
        """
        self.width           = width
        self.height          = height
        self.jpeg_quality    = jpeg_quality
        self.enable_contrast = enable_contrast
        self.contrast_alpha  = contrast_alpha
        self.contrast_beta   = contrast_beta

        # ── Parametri JPEG encoder ─────────────────────────────────────
        self._encode_params = [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality]

    # ----------------------------------------------------------------
    #  PIPELINE PRINCIPALE
    # ----------------------------------------------------------------

    def process(self, frame: np.ndarray) -> bytes:
        """
        Applica la pipeline completa a un frame BGR raw del drone.

        Args:
            frame: numpy array (H x W x 3, BGR) proveniente da DroneReader

        Returns:
            bytes JPEG del frame processato, oppure b"" in caso di errore
        """
        if frame is None or frame.size == 0:
            return b""

        # ── 1. Resize ──────────────────────────────────────────────────
        if frame.shape[1] != self.width or frame.shape[0] != self.height:
            frame = cv2.resize(frame, (self.width, self.height),
                               interpolation=cv2.INTER_LINEAR)

        # ── 2. Normalizzazione contrasto ───────────────────────────────
        if self.enable_contrast:
            # convertScaleAbs: dst = saturate(src * alpha + beta)
            frame = cv2.convertScaleAbs(
                frame,
                alpha=self.contrast_alpha,
                beta=self.contrast_beta,
            )

        # ── 3. Hook AI  (aggancia qui i tuoi modelli) ──────────────────
        frame = self._ai_inference(frame)

        # ── 4. Encoding JPEG ───────────────────────────────────────────
        ok, buffer = cv2.imencode(".jpg", frame, self._encode_params)
        if not ok:
            return b""

        return buffer.tobytes()

    def process_to_frame(self, frame: np.ndarray) -> np.ndarray | None:
        """
        Come process() ma ritorna il numpy array invece dei byte JPEG.
        Usato dal generatore MJPEG per aggiungere overlay prima dell'encoding.

        Garantisce sempre un array BGR scrivibile e contiguo, anche quando
        né il resize né il contrasto vengono applicati (il buffer originale
        di djitellopy può essere read-only).

        Returns:
            numpy.ndarray BGR elaborato e scrivibile, oppure None in caso di errore
        """
        if frame is None or frame.size == 0:
            return None

        if frame.shape[1] != self.width or frame.shape[0] != self.height:
            frame = cv2.resize(frame, (self.width, self.height),
                               interpolation=cv2.INTER_LINEAR)

        if self.enable_contrast:
            # convertScaleAbs crea già un nuovo array scrivibile
            frame = cv2.convertScaleAbs(
                frame,
                alpha=self.contrast_alpha,
                beta=self.contrast_beta,
            )
        else:
            # Copia esplicita: garantisce scrivibilità per cv2.putText e simili
            frame = np.ascontiguousarray(frame)

        return self._ai_inference(frame)

    # ----------------------------------------------------------------
    #  HOOK AI  –  da completare con i modelli reali
    # ----------------------------------------------------------------

    def _ai_inference(self, frame: np.ndarray) -> np.ndarray:
        """
        Punto di estensione per modelli AI (object detection, segmentation, OCR…).

        Riceve ed ritorna un frame BGR numpy array.
        Il frame restituito sarà quello visualizzato nel browser,
        quindi puoi disegnare bounding box, label, ecc. con cv2.rectangle / cv2.putText.

        Esempio di integrazione YOLO (da implementare):
            results = self._yolo_model(frame)
            for box in results.boxes:
                x1, y1, x2, y2 = map(int, box.xyxy[0])
                cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
            return frame

        Attualmente ritorna il frame invariato (pass-through).
        """
        # TODO: agganciare modelli AI (YOLO, EasyOCR, MediaPipe, ecc.)
        return frame
