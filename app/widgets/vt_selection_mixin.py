from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication, QMenu


class VtSelectionMixin:
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.RightButton and getattr(self, "right_click_paste", False):
            self.setFocus()
            self.paste_clipboard()
            event.accept()
            return
        if event.button() == Qt.LeftButton:
            self.setFocus()
            position = self._point_to_cell(event.position().toPoint())
            self.selection_start = self.selection_end = position
            self._dragging = True
            self.viewport().update()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._dragging:
            self.selection_end = self._point_to_cell(event.position().toPoint())
            self.viewport().update()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._dragging = False
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:
        if getattr(self, "right_click_paste", False):
            event.accept()
            return
        menu = QMenu(self)
        copy_action = menu.addAction("Copier")
        copy_action.setEnabled(self.selection_start != self.selection_end)
        copy_action.triggered.connect(self.copy_selection)
        menu.addAction("Coller", self.paste_clipboard).setEnabled(self.input_enabled)
        menu.addSeparator()
        menu.addAction("Effacer l’affichage", self.clear_terminal)
        menu.exec(event.globalPos())

    def _point_to_cell(self, point: QPoint) -> tuple[int, int]:
        row = self.verticalScrollBar().value() + max(
            0, min(self.screen.rows - 1, point.y() // self.cell_height)
        )
        col = max(0, min(self.screen.columns - 1, point.x() // self.cell_width))
        return row, col

    def _selection_bounds(self):
        if self.selection_start is None or self.selection_end is None:
            return None
        return tuple(sorted((self.selection_start, self.selection_end)))

    @staticmethod
    def _inside_selection(row: int, col: int, bounds) -> bool:
        start, end = bounds
        return start <= (row, col) <= end

    def copy_selection(self) -> None:
        bounds = self._selection_bounds()
        if not bounds or bounds[0] == bounds[1]:
            return
        combined = self.screen.history + self.screen.lines
        (start_row, start_col), (end_row, end_col) = bounds
        chunks = []
        for row in range(start_row, min(end_row, len(combined) - 1) + 1):
            left = start_col if row == start_row else 0
            right = end_col if row == end_row else self.screen.columns - 1
            chunks.append("".join(cell.char for cell in combined[row][left : right + 1]).rstrip())
        QApplication.clipboard().setText("\n".join(chunks))

    def copy(self) -> None:
        self.copy_selection()

    def paste(self) -> None:
        self.paste_clipboard()

    def clear(self) -> None:
        self.clear_terminal()

    def find(self, text: str) -> bool:
        if not text:
            return False
        combined = self.screen.history + self.screen.lines
        start_row = self.selection_end[0] if self.selection_end else 0
        for pass_start, pass_end in ((start_row, len(combined)), (0, start_row)):
            for row in range(pass_start, pass_end):
                line = "".join(cell.char for cell in combined[row])
                start_col = self.selection_end[1] + 1 if self.selection_end and row == start_row else 0
                column = line.find(text, start_col)
                if column >= 0:
                    self.selection_start = (row, column)
                    self.selection_end = (
                        row, min(self.screen.columns - 1, column + len(text) - 1)
                    )
                    self.verticalScrollBar().setValue(min(row, len(self.screen.history)))
                    self.viewport().update()
                    return True
        return False
