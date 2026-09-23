from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal, Slot


class CompletionTaskSignals(QObject):
    result = Signal(int, object)
    error = Signal(int, str)
    finished = Signal(object)


class CompletionTask(QRunnable):
    def __init__(self, request_id: int, function: Callable[[], Any]):
        super().__init__()
        self.request_id = request_id
        self.function = function
        self.signals = CompletionTaskSignals()

    @Slot()
    def run(self) -> None:
        try:
            self.signals.result.emit(self.request_id, self.function())
        except Exception as exc:
            self.signals.error.emit(self.request_id, str(exc))
        finally:
            self.signals.finished.emit(self)


_ACTIVE_COMPLETION_TASKS: set[CompletionTask] = set()


def start_completion_task(
    request_id: int,
    function: Callable[[], Any],
    on_result: Callable[[int, object], None],
    on_error: Callable[[int, str], None],
) -> CompletionTask:
    task = CompletionTask(request_id, function)
    _ACTIVE_COMPLETION_TASKS.add(task)
    task.signals.result.connect(on_result, Qt.QueuedConnection)
    task.signals.error.connect(on_error, Qt.QueuedConnection)
    task.signals.finished.connect(_ACTIVE_COMPLETION_TASKS.discard)
    QThreadPool.globalInstance().start(task)
    return task
