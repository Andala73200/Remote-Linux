from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget


class MiniChart(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.values: list[float] = []
        self.maximum = 100.0
        self.setMinimumHeight(85)

    def set_values(self, values: list[float], maximum: float | None = None) -> None:
        self.values = values[-120:]
        self.maximum = max(1.0, maximum or max(self.values, default=1.0))
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(6, 6, -6, -6)
        painter.fillRect(rect, QColor("#0d0f12"))
        painter.setPen(QPen(QColor("#2f343d"), 1))
        for index in range(1, 4):
            y = rect.top() + rect.height() * index / 4
            painter.drawLine(rect.left(), int(y), rect.right(), int(y))
        if len(self.values) < 2:
            painter.end()
            return
        points = QPolygonF()
        step = rect.width() / max(1, len(self.values) - 1)
        for index, value in enumerate(self.values):
            x = rect.left() + index * step
            y = rect.bottom() - min(self.maximum, max(0.0, value)) / self.maximum * rect.height()
            points.append(QPointF(x, y))
        painter.setPen(QPen(QColor("#66d9e8"), 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.drawPolyline(points)
        painter.end()
