import time

import paramiko
from PySide6.QtCore import QThread, Signal


class ShellReader(QThread):
    received = Signal(bytes)
    closed = Signal()
    failed = Signal(str)

    def __init__(self, channel: paramiko.Channel):
        super().__init__()
        self.channel = channel
        self._running = True

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:
        try:
            while self._running and not self.channel.closed:
                if self.channel.recv_ready():
                    data = self.channel.recv(16384)
                    if not data:
                        break
                    self.received.emit(data)
                else:
                    time.sleep(0.02)
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.closed.emit()
