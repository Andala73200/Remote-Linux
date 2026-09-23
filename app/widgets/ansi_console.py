import re

from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QTextEdit


ANSI_RE = re.compile(r"\x1b\[([0-9;]*)m")
CONTROL_RE = re.compile(r"\x1b\[(?![0-9;]*m)[0-9?;]*[A-Za-z]|\x1b\].*?(?:\x07|\x1b\\)")


class AnsiConsole(QTextEdit):
    COLORS = {
        30: "#1b1d20", 31: "#ff6b6b", 32: "#69db7c", 33: "#ffd43b",
        34: "#74c0fc", 35: "#da77f2", 36: "#66d9e8", 37: "#f1f3f5",
        90: "#868e96", 91: "#ff8787", 92: "#8ce99a", 93: "#ffe066",
        94: "#a5d8ff", 95: "#e599f7", 96: "#99e9f2", 97: "#ffffff",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setAcceptRichText(False)
        self.setFont(QFont("Consolas", 10))
        self.document().setMaximumBlockCount(10000)
        self._format = QTextCharFormat()
        self._format.setForeground(QColor("#f1f3f5"))

    def append_ansi(self, text: str) -> None:
        if not text:
            return
        text = CONTROL_RE.sub("", text)
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.End)
        position = 0
        for match in ANSI_RE.finditer(text):
            self._insert(cursor, text[position:match.start()])
            self._apply_codes(match.group(1))
            position = match.end()
        self._insert(cursor, text[position:])
        self.setTextCursor(cursor)
        self.ensureCursorVisible()

    def append_system(self, text: str) -> None:
        fmt = QTextCharFormat()
        fmt.setForeground(QColor("#8be9fd"))
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(text, fmt)
        self.setTextCursor(cursor)
        self.ensureCursorVisible()

    def _insert(self, cursor: QTextCursor, chunk: str) -> None:
        if not chunk:
            return
        chunk = chunk.replace("\r\n", "\n").replace("\r", "")
        cursor.insertText(chunk, self._format)

    def _apply_codes(self, raw: str) -> None:
        codes = [0] if raw == "" else [int(item or 0) for item in raw.split(";")]
        for code in codes:
            if code == 0:
                self._format = QTextCharFormat()
                self._format.setForeground(QColor("#f1f3f5"))
            elif code == 1:
                self._format.setFontWeight(QFont.Bold)
            elif code == 22:
                self._format.setFontWeight(QFont.Normal)
            elif code in self.COLORS:
                self._format.setForeground(QColor(self.COLORS[code]))
