"""
########################################################################
#  drone/media_capture.py  –  Foto e registrazione video dal feed drone
########################################################################
"""

import datetime
import os
import threading
import time
from typing import Optional

import cv2

from drone.frame_reader import DroneReader


class DroneMediaCapture:
    """Gestisce scatto foto e registrazione video dal feed di DroneReader."""

    def __init__(self, drone_reader: DroneReader) -> None:
        self._reader = drone_reader
        self._lock = threading.Lock()

        self._output_dir = os.getenv("MEDIA_OUTPUT_DIR", "captures")
        self._video_fps = float(os.getenv("MEDIA_VIDEO_FPS", "20.0"))
        self._video_codec = os.getenv("MEDIA_VIDEO_CODEC", "mp4v")

        self._recording_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._video_writer: Optional[cv2.VideoWriter] = None
        self._recording_path: Optional[str] = None

    def reload_from_env(self) -> None:
        """Rilegge i parametri media da .env e li applica per le prossime acquisizioni."""
        with self._lock:
            self._output_dir = os.getenv("MEDIA_OUTPUT_DIR", self._output_dir)
            try:
                self._video_fps = float(os.getenv("MEDIA_VIDEO_FPS", str(self._video_fps)))
            except (TypeError, ValueError):
                pass
            self._video_fps = max(1.0, self._video_fps)

            codec = str(os.getenv("MEDIA_VIDEO_CODEC", self._video_codec)).strip()
            if codec:
                self._video_codec = codec

    def take_photo(self) -> str:
        """Scatta una foto dal frame corrente e ritorna il path del file."""
        frame = self._reader.get_frame()
        if frame is None:
            raise RuntimeError("Nessun frame disponibile: avvia lo stream prima")

        self._ensure_output_dir()
        file_path = self._build_output_path("photo", "jpg")

        bgr_frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        ok = cv2.imwrite(file_path, bgr_frame)
        if not ok:
            raise RuntimeError("Salvataggio foto fallito")

        return file_path

    def start_video_recording(self) -> str:
        """Avvia la registrazione video continua e ritorna il path file."""
        with self._lock:
            if self._recording_thread is not None and self._recording_thread.is_alive():
                raise RuntimeError(f"Registrazione gia attiva: {self._recording_path}")

            frame = self._reader.get_frame()
            if frame is None:
                raise RuntimeError("Nessun frame disponibile: avvia lo stream prima")

            self._ensure_output_dir()
            file_path = self._build_output_path("video", "mp4")

            height, width, _ = frame.shape
            fourcc = cv2.VideoWriter_fourcc(*self._video_codec)
            writer = cv2.VideoWriter(file_path, fourcc, self._video_fps, (width, height))
            if not writer.isOpened():
                raise RuntimeError("Impossibile aprire il file video per la registrazione")

            self._video_writer = writer
            self._recording_path = file_path
            self._stop_event.clear()
            self._recording_thread = threading.Thread(
                target=self._recording_loop,
                daemon=True,
                name="drone-video-recording",
            )
            self._recording_thread.start()
            return file_path

    def stop_video_recording(self) -> str:
        """Ferma la registrazione video e ritorna il path del file registrato."""
        with self._lock:
            running = self._recording_thread is not None and self._recording_thread.is_alive()
            current_path = self._recording_path

        if not running:
            raise RuntimeError("Nessuna registrazione video attiva")

        self._stop_event.set()

        with self._lock:
            thread = self._recording_thread

        if thread is not None:
            thread.join(timeout=3.0)

        with self._lock:
            self._release_writer_locked()
            self._recording_thread = None
            self._stop_event.clear()
            if current_path is None:
                raise RuntimeError("Registrazione terminata ma path non disponibile")
            return current_path

    def is_recording(self) -> bool:
        with self._lock:
            return self._recording_thread is not None and self._recording_thread.is_alive()

    def get_recording_path(self) -> Optional[str]:
        with self._lock:
            return self._recording_path

    def stop_video_recording_silent(self) -> None:
        """Ferma la registrazione ignorando errori (utile in cleanup)."""
        try:
            self.stop_video_recording()
        except Exception:
            pass

    def _recording_loop(self) -> None:
        """Thread loop: legge frame dal drone e li scrive nel file video."""
        frame_interval = 1.0 / max(1.0, self._video_fps)

        while not self._stop_event.is_set():
            frame = self._reader.get_frame()
            if frame is None:
                time.sleep(0.01)
                continue

            bgr_frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

            with self._lock:
                writer = self._video_writer

            if writer is None:
                break

            writer.write(bgr_frame)
            time.sleep(frame_interval)

        with self._lock:
            self._release_writer_locked()

    def _ensure_output_dir(self) -> None:
        os.makedirs(self._output_dir, exist_ok=True)

    def _build_output_path(self, prefix: str, extension: str) -> str:
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        file_name = f"{prefix}_{timestamp}.{extension}"
        return os.path.join(self._output_dir, file_name)

    def _release_writer_locked(self) -> None:
        if self._video_writer is not None:
            try:
                self._video_writer.release()
            except Exception:
                pass
            self._video_writer = None
