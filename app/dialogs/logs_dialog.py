import re
import time

import paramiko
from PySide6.QtCore import QRegularExpression, QThread, Signal
from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout,
    QLabel, QLineEdit, QPlainTextEdit, QPushButton, QVBoxLayout,
)

from app.core.session import RemoteSession
from app.i18n import ntr, tr

_ACTIVE_LOG_READERS: set[QThread] = set()


def shutdown_log_readers(timeout_ms: int = 5000) -> bool:
    readers = list(_ACTIVE_LOG_READERS)
    for reader in readers:
        if isinstance(reader, LiveLogReader):
            reader.stop()
    if not readers:
        return True
    per_reader = max(1, timeout_ms // len(readers))
    stopped = True
    for reader in readers:
        if reader.isRunning() and not reader.wait(per_reader):
            stopped = False
        if not reader.isRunning():
            _ACTIVE_LOG_READERS.discard(reader)
    return stopped


class LiveLogReader(QThread):
    received = Signal(str)
    failed = Signal(str)

    def __init__(self, session: RemoteSession, command: str):
        super().__init__()
        self.session = session
        self.command = command
        self.channel: paramiko.Channel | None = None
        self.running = True

    def stop(self) -> None:
        self.running = False

    def run(self) -> None:
        try:
            self.channel = self.session.open_stream(self.command)
            while self.running and not self.channel.closed:
                if self.channel.recv_ready():
                    data = self.channel.recv(8192)
                    if not data:
                        break
                    self.received.emit(data.decode("utf-8", errors="replace"))
                elif self.channel.recv_stderr_ready():
                    data = self.channel.recv_stderr(8192)
                    if data:
                        self.received.emit(data.decode("utf-8", errors="replace"))
                else:
                    time.sleep(0.05)
        except Exception as exc:
            if self.running:
                self.failed.emit(str(exc))
        finally:
            if self.channel:
                try:
                    self.channel.close()
                except Exception:
                    pass


class LogHighlighter(QSyntaxHighlighter):
    def __init__(self, document):
        super().__init__(document)
        self.rules = []
        for pattern, color in [
            (r"\b(error|err|failed|failure|critical|panic)\b", "#FF6666"),
            (r"\b(warn|warning)\b", "#F2C866"),
            (r"\b(info|started|active|success)\b", "#55D187"),
            (r"\b(debug|trace)\b", "#9AA1AA"),
        ]:
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(color))
            expression = QRegularExpression(pattern, QRegularExpression.CaseInsensitiveOption)
            self.rules.append((expression, fmt))

    def highlightBlock(self, text: str) -> None:
        for expression, fmt in self.rules:
            iterator = expression.globalMatch(text)
            while iterator.hasNext():
                match = iterator.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), fmt)


class LogsDialog(QDialog):
    def __init__(self, title: str, text: str, command: str = "", parent=None, session: RemoteSession | None = None, live: bool = False):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(1050, 700)
        self.original_lines = text.splitlines()
        self.command = command
        self.reader: LiveLogReader | None = None
        self.live = live
        self.search = QLineEdit()
        self.search.setPlaceholderText("Rechercher dans les journaux…")
        self.search.textChanged.connect(self._filter)
        self.level = QComboBox()
        for label, words in [
            ("Tous", ""), ("Erreurs", "error|err|failed|critical|panic"),
            ("Avertissements", "warn|warning"), ("Informations", "info|started|active|success"),
        ]:
            self.level.addItem(label, words)
        self.level.currentIndexChanged.connect(self._filter)
        self.pause = QPushButton("Pause")
        self.pause.setCheckable(True)
        self.pause.setVisible(live)
        self.pause.toggled.connect(lambda value: self.pause.setText("Reprendre" if value else "Pause"))
        self.auto_scroll = QCheckBox("Défilement automatique")
        self.auto_scroll.setChecked(True)
        self.auto_scroll.setVisible(live)
        self.counter = QLabel()
        top = QHBoxLayout()
        top.addWidget(self.search, 1)
        top.addWidget(QLabel("Niveau :"))
        top.addWidget(self.level)
        top.addWidget(self.pause)
        top.addWidget(self.auto_scroll)
        top.addWidget(self.counter)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setFont(QFont("Consolas", 10))
        self.highlighter = LogHighlighter(self.output.document())
        export = QPushButton("Exporter…")
        export.clicked.connect(self._export)
        copy_command = QPushButton("Copier la commande")
        copy_command.setEnabled(bool(command))
        copy_command.clicked.connect(lambda: QApplication.clipboard().setText(command))
        clear = QPushButton("Effacer")
        clear.setVisible(live)
        clear.clicked.connect(self._clear)
        close = QPushButton("Fermer")
        close.clicked.connect(self.accept)
        buttons = QHBoxLayout()
        buttons.addWidget(export)
        buttons.addWidget(copy_command)
        buttons.addWidget(clear)
        buttons.addStretch(1)
        buttons.addWidget(close)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.output, 1)
        layout.addLayout(buttons)
        self._filter()
        if live and session:
            self.reader = LiveLogReader(session, command)
            self.reader.received.connect(self._append_live)
            self.reader.failed.connect(self._append_live_error)
            _ACTIVE_LOG_READERS.add(self.reader)
            self.reader.finished.connect(
                lambda item=self.reader: _ACTIVE_LOG_READERS.discard(item)
            )
            self.reader.start()

    def _append_live(self, text: str) -> None:
        self.original_lines.extend(text.splitlines())
        del self.original_lines[:-20000]
        if not self.pause.isChecked():
            self._filter()
            if self.auto_scroll.isChecked():
                self.output.moveCursor(QTextCursor.End)

    def _append_live_error(self, message: str) -> None:
        self._append_live(f"\nErreur : {message}\n")

    def _filter(self, *_args) -> None:
        needle = self.search.text().lower().strip()
        level_pattern = str(self.level.currentData() or "")
        matcher = re.compile(level_pattern, re.I) if level_pattern else None
        rows = [line for line in self.original_lines if (not needle or needle in line.lower()) and (not matcher or matcher.search(line))]
        self.output.setPlainText("\n".join(rows))
        line_label = ntr(len(self.original_lines), 'ui.line', 'ui.lines')
        self.counter.setText(f"{len(rows)} / {len(self.original_lines)} {line_label}")

    def _clear(self) -> None:
        self.original_lines.clear()
        self._filter()

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, tr('ui.export_logs'), "journaux.txt",
            tr('ui.text_txt_all_files'),
        )
        if path:
            try:
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(self.output.toPlainText())
            except OSError:
                pass

    def _stop_reader(self) -> None:
        reader = self.reader
        if not reader:
            return
        try:
            reader.received.disconnect(self._append_live)
            reader.failed.disconnect(self._append_live_error)
        except (RuntimeError, TypeError):
            pass
        reader.stop()
        if reader.wait(3000):
            _ACTIVE_LOG_READERS.discard(reader)
        else:
            # The global reference prevents Qt from destroying a QThread that is still
            # active. It will be released once the network operation returns.
            _ACTIVE_LOG_READERS.add(reader)
        self.reader = None

    def done(self, result: int) -> None:
        self._stop_reader()
        super().done(result)

    def closeEvent(self, event) -> None:
        self._stop_reader()
        event.accept()
