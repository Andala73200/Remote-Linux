from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QScrollArea, QWidget

from app.core.terminal_completion import CompletionCandidate


class TerminalCompletionPanel(QScrollArea):
    selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFixedHeight(58)
        self._container = QWidget()
        self._layout = QHBoxLayout(self._container)
        self._layout.setContentsMargins(4, 4, 4, 4)
        self._layout.setSpacing(5)
        self._layout.addStretch(1)
        self.setWidget(self._container)
        self.hide()

    def show_candidates(self, candidates: list[CompletionCandidate]) -> None:
        self.clear_candidates()
        if not candidates:
            return
        for candidate in candidates:
            button = QPushButton(candidate.label)
            button.setToolTip(candidate.line.rstrip())
            button.clicked.connect(
                lambda _checked=False, line=candidate.line: self.selected.emit(line)
            )
            self._layout.insertWidget(self._layout.count() - 1, button)
        self.horizontalScrollBar().setValue(0)
        self.show()

    def clear_candidates(self) -> None:
        while self._layout.count() > 1:
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.hide()
