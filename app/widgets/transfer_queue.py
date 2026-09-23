from __future__ import annotations

import os
from collections.abc import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.core.session import RemoteSession
from app.core.transfers import TransferWorker
from app.i18n import byte_units, ntr, tr


class TransferRow(QFrame):
    action_requested = Signal()

    def __init__(self, title: str, direction: str, parent=None):
        super().__init__(parent)
        self.setObjectName("transferRow")
        self.title = QLabel(title)
        self.title.setToolTip(title)
        self.direction = QLabel(
            "↑ Windows → Linux" if direction == "upload" else "↓ Linux → Windows"
        )
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("%p%")
        self.details = QLabel("En attente")
        self.details.setWordWrap(True)
        self.action_button = QPushButton("Annuler")
        self.action_button.clicked.connect(self.action_requested)
        top = QHBoxLayout()
        top.addWidget(self.title, 1)
        top.addWidget(self.direction)
        top.addWidget(self.action_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 5, 6, 5)
        layout.addLayout(top)
        layout.addWidget(self.progress)
        layout.addWidget(self.details)

    def reset(self) -> None:
        self.progress.setValue(0)
        self.details.setText("En attente")
        self.action_button.setText("Annuler")
        self.action_button.setEnabled(True)

    def set_status(self, text: str) -> None:
        self.details.setText(text)

    def set_progress(self, transferred: int, total: int, speed: float, current: str) -> None:
        percent = 0 if total <= 0 else min(100, int(transferred * 100 / total))
        self.progress.setValue(percent)
        current_name = os.path.basename(current.replace("\\", "/")) or current
        self.details.setText(
            f"{percent}%  •  {self._size(transferred)} / {self._size(total)}"
            f"  •  {self._size(speed)}/s  •  {current_name}"
        )

    def finish(self, text: str, success: bool, retryable: bool = True) -> None:
        if success:
            self.progress.setValue(100)
            self.action_button.setEnabled(False)
        elif retryable:
            self.action_button.setText("Relancer")
            self.action_button.setEnabled(True)
        else:
            self.action_button.setText("Annulé")
            self.action_button.setEnabled(False)
        self.details.setText(text)

    @staticmethod
    def _size(value: float) -> str:
        for unit in byte_units():
            if value < 1024 or unit in {"To", "TB"}:
                return f"{value:.0f} {unit}" if unit in {"o", "B"} else f"{value:.1f} {unit}"
            value /= 1024
        return f"0 {byte_units()[0]}"


class TransferQueueWidget(QWidget):
    queue_changed = Signal(int)
    transfer_failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.jobs: list[dict[str, object]] = []
        self.active: dict[str, object] | None = None
        self.current_session: RemoteSession | None = None
        self.title = QLabel("TRANSFERTS")
        self.title.setStyleSheet("font-weight:bold;")
        self.clear_button = QPushButton("Effacer les échecs")
        self.clear_button.clicked.connect(self.clear_finished)
        header = QHBoxLayout()
        header.addWidget(self.title)
        header.addStretch(1)
        header.addWidget(self.clear_button)
        self.content = QWidget()
        self.rows = QVBoxLayout(self.content)
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.content)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.addLayout(header)
        layout.addWidget(scroll)
        self.setMaximumHeight(225)
        self.hide()

    def enqueue_upload(
        self,
        session: RemoteSession,
        paths: list[str],
        remote_dir: str,
        on_done: Callable[[object], None] | None = None,
    ) -> None:
        self._enqueue(session, "upload", paths, remote_dir, True, on_done)

    def enqueue_download(
        self,
        session: RemoteSession,
        paths: list[str],
        local_dir: str,
        overwrite: bool,
        on_done: Callable[[object], None] | None = None,
        on_finished: Callable[[bool, object], None] | None = None,
        initial_status: str | None = None,
        retryable: bool = True,
    ) -> None:
        self._enqueue(
            session, "download", paths, local_dir, overwrite, on_done,
            on_finished, initial_status, retryable,
        )

    def enqueue_download_file(
        self,
        session: RemoteSession,
        remote_path: str,
        local_path: str,
        on_done: Callable[[object], None] | None = None,
    ) -> None:
        self._enqueue(session, "download_file", [remote_path], local_path, True, on_done)

    def _enqueue(
        self,
        session: RemoteSession,
        direction: str,
        paths: list[str],
        destination: str,
        overwrite: bool,
        on_done: Callable[[object], None] | None,
        on_finished: Callable[[bool, object], None] | None = None,
        initial_status: str | None = None,
        retryable: bool = True,
    ) -> None:
        title = paths[0].replace("\\", "/").rstrip("/").split("/")[-1]
        if len(paths) > 1:
            other_count = len(paths) - 1
            title += f" + {other_count} {ntr(other_count, 'ui.other', 'ui.others')}"
        row = TransferRow(title, direction)
        self.rows.insertWidget(self.rows.count() - 1, row)
        job = {
            "session": session,
            "direction": direction,
            "paths": list(paths),
            "destination": destination,
            "overwrite": overwrite,
            "callback": on_done,
            "finished_callback": on_finished,
            "initial_status": initial_status,
            "retryable": retryable,
            "row": row,
            "worker": None,
            "finished": False,
            "success": False,
            "cancelled": False,
        }
        row.action_requested.connect(lambda: self._row_action(job))
        self.jobs.append(job)
        self.show()
        self.queue_changed.emit(len(self.jobs))
        self._start_next()

    def _start_next(self) -> None:
        if self.active:
            return
        job = next(
            (
                item
                for item in self.jobs
                if not item["finished"] and item["worker"] is None
            ),
            None,
        )
        if not job:
            return
        worker = TransferWorker(
            job["session"],
            job["direction"],
            job["paths"],
            job["destination"],
            bool(job["overwrite"]),
            self,
        )
        job["worker"] = worker
        self.active = job
        row = job["row"]
        row.reset()
        row.set_status(str(job.get("initial_status") or tr('ui.preparing_transfer')))
        worker.progress.connect(row.set_progress)
        worker.status.connect(row.set_status)
        worker.succeeded.connect(lambda result: self._finished(job, True, result))
        worker.failed.connect(lambda text: self._finished(job, False, text))
        worker.cancelled.connect(lambda text: self._cancelled(job, text))
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _row_action(self, job: dict[str, object]) -> None:
        if job["finished"]:
            if not job["success"] and not job["cancelled"]:
                session = self.current_session
                original = job["session"]
                if (
                    not session
                    or session.profile.id != original.profile.id
                ):
                    job["row"].set_status(
                        "Reconnecte le profil d’origine avant de relancer."
                    )
                    return
                job["session"] = session
                job["finished"] = False
                job["worker"] = None
                job["row"].reset()
                self._start_next()
            return
        worker = job.get("worker")
        if isinstance(worker, TransferWorker):
            worker.cancel()
            job["row"].set_status("Annulation en cours…")
        else:
            job["finished"] = True
            job["success"] = False
            job["cancelled"] = True
            text = tr('ui.cancelled_before_start')
            job["row"].finish(text, False, False)
            final_callback = job.get("finished_callback")
            if callable(final_callback):
                final_callback(False, text)
            self._start_next()

    def _finished(self, job: dict[str, object], success: bool, result: object) -> None:
        job["finished"] = True
        job["success"] = success
        row = job["row"]

        if success:
            # Successful transfers disappear immediately: the area keeps only
            # pending/in-progress transfers and failures.
            if callable(job.get("callback")):
                job["callback"](result)
            row.deleteLater()
            if job in self.jobs:
                self.jobs.remove(job)
            self.queue_changed.emit(len(self.jobs))
        else:
            retryable = bool(job.get("retryable", True))
            row.finish(f"Échec — {result}", False, retryable)
            if not retryable:
                row.action_button.setText(tr('ui.failure'))
            self.transfer_failed.emit(f"{row.title.text()} : {result}")

        self.active = None
        final_callback = job.get("finished_callback")
        if callable(final_callback):
            final_callback(success, result)
        self._start_next()
        if not self.jobs:
            self.hide()

    def _cancelled(self, job: dict[str, object], text: str) -> None:
        job["finished"] = True
        job["success"] = False
        job["cancelled"] = True
        job["row"].finish(text or "Transfert annulé.", False, False)
        self.active = None
        final_callback = job.get("finished_callback")
        if callable(final_callback):
            final_callback(False, text or tr('ui.transfer_cancelled'))
        self._start_next()

    def set_session(self, session: RemoteSession | None) -> None:
        self.current_session = session


    def active_transfer_count(self) -> int:
        return sum(1 for job in self.jobs if not bool(job["finished"]))

    def clear_finished(self) -> None:
        kept = []
        for job in self.jobs:
            if job["finished"]:
                job["row"].deleteLater()
            else:
                kept.append(job)
        self.jobs = kept
        self.queue_changed.emit(len(self.jobs))
        if not self.jobs:
            self.hide()

    def cancel_all(self) -> None:
        for job in self.jobs:
            if not job["finished"]:
                self._row_action(job)

    def shutdown(self, timeout_ms: int = 5000) -> bool:
        self.cancel_all()
        active = self.active
        worker = active.get("worker") if active else None
        if isinstance(worker, TransferWorker) and worker.isRunning():
            return bool(worker.wait(timeout_ms))
        return True
