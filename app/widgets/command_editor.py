from PySide6.QtCore import Signal
from PySide6.QtGui import QKeyEvent, QTextCursor
from PySide6.QtWidgets import QPlainTextEdit
from PySide6.QtCore import Qt


class CommandEditor(QPlainTextEdit):
    submit_requested = Signal()
    history_up_requested = Signal()
    history_down_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPlaceholderText("Commande Linux…")
        self.setTabChangesFocus(False)
        self.document().blockCountChanged.connect(self._resize_to_content)
        self._resize_to_content()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        modifiers = event.modifiers()
        if key in (Qt.Key_Return, Qt.Key_Enter):
            if modifiers & Qt.ShiftModifier:
                super().keyPressEvent(event)
            else:
                self.submit_requested.emit()
            return
        single_line = self.document().blockCount() <= 1
        if key == Qt.Key_Up and (single_line or modifiers & Qt.AltModifier):
            self.history_up_requested.emit()
            return
        if key == Qt.Key_Down and (single_line or modifiers & Qt.AltModifier):
            self.history_down_requested.emit()
            return
        super().keyPressEvent(event)

    def set_command(self, text: str) -> None:
        self.setPlainText(text)
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.setTextCursor(cursor)

    def _resize_to_content(self, *_args) -> None:
        lines = max(1, min(6, self.document().blockCount()))
        line_height = self.fontMetrics().lineSpacing()
        margins = int(self.document().documentMargin() * 2) + 12
        self.setFixedHeight(lines * line_height + margins)
