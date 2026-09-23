from PySide6.QtCore import QObject, QTimer, Signal

from app.core.session import RemoteSession


class SessionMonitor(QObject):
    disconnected = Signal()
    FAILURE_THRESHOLD = 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self.session: RemoteSession | None = None
        self.failures = 0
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self._check)

    def set_session(self, session: RemoteSession | None) -> None:
        self.session = session
        self.failures = 0
        if session:
            self.timer.start()
        else:
            self.timer.stop()

    def _check(self) -> None:
        if not self.session:
            return
        if self.session.is_alive():
            self.failures = 0
            return
        self.failures += 1
        if self.failures >= self.FAILURE_THRESHOLD:
            self.timer.stop()
            self.disconnected.emit()
