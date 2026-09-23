from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QProxyStyle, QStyle, QStyleFactory, QStyleOption


class VisibleCheckBoxStyle(QProxyStyle):
    """Draw readable checkboxes throughout the dark theme."""

    INDICATOR_SIZE = 18

    def __init__(self):
        super().__init__(QStyleFactory.create("Fusion"))

    def pixelMetric(self, metric, option=None, widget=None):
        if metric in (QStyle.PM_IndicatorWidth, QStyle.PM_IndicatorHeight):
            return self.INDICATOR_SIZE
        return super().pixelMetric(metric, option, widget)

    def drawPrimitive(self, element, option: QStyleOption, painter, widget=None):
        if element not in (
            QStyle.PE_IndicatorCheckBox,
            QStyle.PE_IndicatorItemViewItemCheck,
        ):
            super().drawPrimitive(element, option, painter, widget)
            return

        rect = QRectF(option.rect).adjusted(1.0, 1.0, -1.0, -1.0)
        enabled = bool(option.state & QStyle.State_Enabled)
        checked = bool(option.state & QStyle.State_On)
        partial = bool(option.state & QStyle.State_NoChange)
        hovered = bool(option.state & QStyle.State_MouseOver)

        border = QColor("#8D99A8" if enabled else "#4B515A")
        background = QColor("#14171B" if enabled else "#202329")
        if hovered and enabled:
            border = QColor("#83C7FF")
            background = QColor("#222A32")
        if checked or partial:
            background = QColor("#2D8FD5" if enabled else "#3B586B")
            border = QColor("#87D0FF" if enabled else "#5D7180")

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(QPen(border, 1.6))
        painter.setBrush(background)
        painter.drawRoundedRect(rect, 3.2, 3.2)

        if checked:
            path = QPainterPath()
            path.moveTo(QPointF(rect.left() + rect.width() * 0.20, rect.center().y()))
            path.lineTo(QPointF(rect.left() + rect.width() * 0.43, rect.bottom() - rect.height() * 0.23))
            path.lineTo(QPointF(rect.right() - rect.width() * 0.16, rect.top() + rect.height() * 0.22))
            painter.setPen(QPen(QColor("#FFFFFF"), 2.25, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path)
        elif partial:
            painter.setPen(QPen(QColor("#FFFFFF"), 2.4, Qt.SolidLine, Qt.RoundCap))
            painter.drawLine(
                QPointF(rect.left() + rect.width() * 0.23, rect.center().y()),
                QPointF(rect.right() - rect.width() * 0.23, rect.center().y()),
            )
        painter.restore()
