from collections.abc import Callable
from concurrent.futures import CancelledError
from typing import Any
import logging

from PySide6.QtCore import (
    QCoreApplication, QObject, QRunnable, QThreadPool, Signal, Slot, Qt,
)

LOGGER = logging.getLogger(__name__)


class TaskSignals(QObject):
    result = Signal(object)
    error = Signal(str)
    finished = Signal()


class Task(QRunnable):
    def __init__(
        self,
        function: Callable[[], Any],
        should_run: Callable[[], bool] | None = None,
    ):
        super().__init__()
        self.function = function
        self.should_run = should_run
        self.signals = TaskSignals()

    @Slot()
    def run(self) -> None:
        try:
            if self.should_run is not None and not self.should_run():
                return
            self.signals.result.emit(self.function())
        except CancelledError:
            pass
        except Exception as exc:
            LOGGER.exception("Error in asynchronous task")
            self.signals.error.emit(str(exc))
        finally:
            self.signals.finished.emit()


class _CallbackBridge(QObject):
    """Deliver worker callbacks in the Qt application thread."""

    def __init__(
        self,
        on_result: Callable[[Any], None] | None,
        on_error: Callable[[str], None] | None,
        guard: Callable[[], bool] | None,
        cleanup: Callable[[], None],
    ):
        super().__init__()
        self._on_result = on_result
        self._on_error = on_error
        self._guard = guard
        self._cleanup = cleanup

    def _allowed(self) -> bool:
        try:
            return self._guard is None or bool(self._guard())
        except Exception:
            LOGGER.exception("Asynchronous task guard failed")
            return False

    @Slot(object)
    def result(self, value: Any) -> None:
        if self._allowed() and self._on_result:
            self._on_result(value)

    @Slot(str)
    def error(self, text: str) -> None:
        if self._allowed() and self._on_error:
            self._on_error(text)

    @Slot()
    def finished(self) -> None:
        self._cleanup()
        self.deleteLater()


_ACTIVE_TASKS: dict[Task, _CallbackBridge] = {}


def run_async(
    function: Callable[[], Any],
    on_result: Callable[[Any], None] | None = None,
    on_error: Callable[[str], None] | None = None,
    guard: Callable[[], bool] | None = None,
) -> Task:
    """Start a task and marshal all callbacks through the Qt event loop."""
    task = Task(function, guard)

    def cleanup() -> None:
        _ACTIVE_TASKS.pop(task, None)

    bridge = _CallbackBridge(on_result, on_error, guard, cleanup)
    app = QCoreApplication.instance()
    if app is not None and bridge.thread() is not app.thread():
        bridge.moveToThread(app.thread())
    _ACTIVE_TASKS[task] = bridge

    queued = Qt.ConnectionType.QueuedConnection
    task.signals.result.connect(bridge.result, queued)
    task.signals.error.connect(bridge.error, queued)
    task.signals.finished.connect(bridge.finished, queued)
    QThreadPool.globalInstance().start(task)
    return task
