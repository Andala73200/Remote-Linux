from collections.abc import Callable

from PySide6.QtCore import (
    QMimeData, QPoint, QPointF, QPersistentModelIndex, QRectF, Qt, QTimer,
    QUrl, Signal,
)
from PySide6.QtGui import (
    QColor, QCursor, QDrag, QDragEnterEvent, QDropEvent, QPainter,
    QPen, QPolygonF,
)
from PySide6.QtWidgets import QAbstractItemView, QToolTip, QTreeWidget


SUMMARY_ROLE = Qt.UserRole + 3
DETAIL_ROLE = Qt.UserRole + 4


class RemoteTree(QTreeWidget):
    files_dropped = Signal(list, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.remote_drag_handler: Callable[[list[str]], None] | None = None
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setMouseTracking(True)
        self._hover_index = QPersistentModelIndex()
        self._hover_detail = ""
        self._hover_pos = QPoint()
        self._long_timer = QTimer(self)
        self._long_timer.setSingleShot(True)
        self._long_timer.setInterval(1100)
        self._long_timer.timeout.connect(self._show_long_tooltip)

    def prepare_for_reset(self) -> None:
        """Stop deferred interactions before Qt destroys the items."""
        self._long_timer.stop()
        self._hover_index = QPersistentModelIndex()
        self._hover_detail = ""
        self.clearSelection()
        QToolTip.hideText()

    def drawBranches(self, painter: QPainter, rect, index) -> None:
        """Replace Qt chevrons with a clickable open/closed folder indicator."""
        if not self.model().hasChildren(index):
            return
        size = min(16.0, max(12.0, float(rect.height() - 4)))
        x = float(rect.right()) - size - 2.0
        y = float(rect.center().y()) - size / 2.0
        body = QRectF(x, y + size * 0.25, size, size * 0.70)
        tab = QRectF(x + 1.0, y + 1.0, size * 0.48, size * 0.38)
        expanded = self.isExpanded(index)

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(QPen(QColor("#9A6A16"), 1.0))
        painter.setBrush(QColor("#D99A2B"))
        painter.drawRoundedRect(tab, 1.7, 1.7)
        painter.drawRoundedRect(body, 2.0, 2.0)

        if expanded:
            flap = QPolygonF([
                QPointF(x + 0.5, y + size * 0.40),
                QPointF(x + size - 0.5, y + size * 0.40),
                QPointF(x + size * 0.84, y + size - 0.5),
                QPointF(x + size * 0.10, y + size - 0.5),
            ])
            painter.setBrush(QColor("#F0C04F"))
            painter.drawPolygon(flap)
        else:
            painter.setBrush(QColor("#E7B13D"))
            painter.drawRoundedRect(
                QRectF(x + 0.6, y + size * 0.36, size - 1.2, size * 0.57),
                1.8, 1.8,
            )
        painter.restore()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        event.acceptProposedAction() if event.mimeData().hasUrls() else super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        event.acceptProposedAction() if event.mimeData().hasUrls() else super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        if not event.mimeData().hasUrls():
            super().dropEvent(event)
            return
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self.files_dropped.emit(paths, self.itemAt(event.position().toPoint()))
            event.acceptProposedAction()

    def selected_remote_paths(self) -> list[str]:
        paths: list[str] = []
        for item in self.selectedItems():
            path = item.data(0, Qt.UserRole)
            if path and str(path) not in paths:
                paths.append(str(path))
        return paths

    def startDrag(self, _supported_actions) -> None:
        if not self.remote_drag_handler:
            return
        paths = self.selected_remote_paths()
        if paths:
            self.remote_drag_handler(paths)

    def start_local_drag(self, local_paths: list[str]):
        if not local_paths:
            return Qt.IgnoreAction
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(path) for path in local_paths])
        drag = QDrag(self)
        drag.setMimeData(mime)
        return drag.exec(Qt.CopyAction)

    def mouseMoveEvent(self, event) -> None:
        super().mouseMoveEvent(event)
        index = self.indexAt(event.position().toPoint())
        persistent = QPersistentModelIndex(index)
        if persistent == self._hover_index:
            return

        self._hover_index = persistent
        self._hover_detail = ""
        self._hover_pos = event.globalPosition().toPoint()
        self._long_timer.stop()
        QToolTip.hideText()

        if not index.isValid():
            return
        item = self.itemFromIndex(index)
        if item is None:
            return
        summary = str(item.data(0, SUMMARY_ROLE) or "")
        self._hover_detail = str(item.data(0, DETAIL_ROLE) or "")
        if summary:
            QToolTip.showText(self._hover_pos, summary, self)
            if self._hover_detail:
                self._long_timer.start()

    def leaveEvent(self, event) -> None:
        self._long_timer.stop()
        self._hover_index = QPersistentModelIndex()
        self._hover_detail = ""
        QToolTip.hideText()
        super().leaveEvent(event)

    def _show_long_tooltip(self) -> None:
        if self._hover_index.isValid() and self._hover_detail:
            QToolTip.showText(QCursor.pos(), self._hover_detail, self)
