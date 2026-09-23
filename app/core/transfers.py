from __future__ import annotations

import threading
import time

from PySide6.QtCore import QThread, Signal

from app.core.session import RemoteSession
from app.core.transfer_stream import TransferCancelled


class TransferWorker(QThread):
    progress = Signal(int, int, float, str)
    status = Signal(str)
    succeeded = Signal(object)
    failed = Signal(str)
    cancelled = Signal(str)

    def __init__(
        self,
        session: RemoteSession,
        direction: str,
        paths: list[str],
        destination: str,
        overwrite: bool = True,
        parent=None,
    ):
        super().__init__(parent)
        self.session = session
        self.direction = direction
        self.paths = list(paths)
        self.destination = destination
        self.overwrite = overwrite
        self.cancel_event = threading.Event()
        self._last_bytes = 0
        self._last_at = 0.0
        self._speed = 0.0

    def cancel(self) -> None:
        self.cancel_event.set()

    def _progress(self, transferred: int, total: int, current: str) -> None:
        now = time.monotonic()
        if self._last_at:
            elapsed = now - self._last_at
            delta = max(0, transferred - self._last_bytes)
            if elapsed >= 0.05 and delta > 0:
                instant = delta / elapsed
                self._speed = instant if self._speed <= 0 else self._speed * 0.7 + instant * 0.3
        self._last_bytes = transferred
        self._last_at = now
        self.progress.emit(transferred, total, self._speed, current)

    def run(self) -> None:
        self._last_at = time.monotonic()
        try:
            if self.direction == "upload":
                result = self.session.upload_paths(
                    self.paths,
                    self.destination,
                    progress=self._progress,
                    cancel_event=self.cancel_event,
                    status=self.status.emit,
                )
            elif self.direction == "download":
                result = self.session.download_paths(
                    self.paths,
                    self.destination,
                    overwrite=self.overwrite,
                    progress=self._progress,
                    cancel_event=self.cancel_event,
                    status=self.status.emit,
                )
            elif self.direction == "download_file":
                self.session.download_file(
                    self.paths[0],
                    self.destination,
                    progress=self._progress,
                    cancel_event=self.cancel_event,
                    status=self.status.emit,
                )
                result = self.destination
            else:
                raise ValueError(f"Direction de transfert inconnue : {self.direction}")
            self.succeeded.emit(result)
        except TransferCancelled as exc:
            self.cancelled.emit(str(exc) or "Transfert annulé.")
        except Exception as exc:
            self.failed.emit(str(exc))
