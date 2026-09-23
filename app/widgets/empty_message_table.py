from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QPalette
from PySide6.QtWidgets import QTableWidget

from app.i18n import tr


class EmptyMessageTable(QTableWidget):
    def __init__(self, rows: int, columns: int, parent=None):
        super().__init__(rows, columns, parent)
        self._empty_title_key = ""
        self._empty_hint_key = ""

    def set_empty_message(self, title_key: str = "", hint_key: str = "") -> None:
        self._empty_title_key = str(title_key or "")
        self._empty_hint_key = str(hint_key or "")
        self.viewport().update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self.rowCount() or not self._empty_title_key:
            return
        title = tr(self._empty_title_key)
        hint = tr(self._empty_hint_key) if self._empty_hint_key else ""
        text = title if not hint else f"{title}\n{hint}"
        painter = QPainter(self.viewport())
        painter.setPen(self.palette().color(QPalette.ColorRole.PlaceholderText))
        rect = self.viewport().rect().adjusted(24, 24, -24, -24)
        flags = (
            Qt.AlignmentFlag.AlignCenter.value
            | Qt.TextFlag.TextWordWrap.value
        )
        painter.drawText(rect, flags, text)
