from __future__ import annotations

import re
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QLabel, QLineEdit, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from app.i18n import SOURCE_ROLE, TARGET_ROLE, tr


DATE_RE = re.compile(
    r"(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})"
    r"(?:[T ](?P<hour>\d{2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?)?"
)


@dataclass(frozen=True)
class Cell:
    text: object
    sort: object | None = None
    data: object | None = None
    translate: bool = False


class SortableItem(QTableWidgetItem):
    def __init__(self, text: object, sort_value: object | None = None):
        super().__init__(str(text))
        self.sort_value = str(text).casefold() if sort_value is None else sort_value

    def __lt__(self, other) -> bool:
        right = getattr(other, "sort_value", other.text().casefold())
        try:
            return self.sort_value < right
        except TypeError:
            return str(self.sort_value) < str(right)


class ColumnFilterBar(QWidget):
    def __init__(self, table: QTableWidget, headers: list[str], skip: set[int] | None = None):
        super().__init__()
        self.table = table
        self.editors: dict[int, QLineEdit] = {}
        self.extra_predicate = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel("Filtres :"))
        for column, header in enumerate(headers):
            if column in (skip or set()):
                continue
            editor = QLineEdit()
            editor.setClearButtonEnabled(True)
            editor.setPlaceholderText(header)
            editor.textChanged.connect(self.apply)
            self.editors[column] = editor
            layout.addWidget(editor, 1)

    def set_extra_predicate(self, predicate) -> None:
        self.extra_predicate = predicate
        self.apply()

    def clear(self) -> None:
        for editor in self.editors.values():
            editor.clear()

    def apply(self) -> None:
        filters = {
            column: editor.text().strip().casefold()
            for column, editor in self.editors.items() if editor.text().strip()
        }
        for row in range(self.table.rowCount()):
            visible = all(
                needle in (self.table.item(row, column).text().casefold()
                           if self.table.item(row, column) else "")
                for column, needle in filters.items()
            )
            if visible and self.extra_predicate:
                visible = bool(self.extra_predicate(row))
            self.table.setRowHidden(row, not visible)


class FilteredTablePage(QWidget):
    def __init__(
        self, headers: list[str], *, filters: bool = True,
        skip_filters: set[int] | None = None, default_sort: int = 0,
    ):
        super().__init__()
        self.headers = headers
        self.sort_column = default_sort
        self.sort_order = Qt.AscendingOrder
        self.table = QTableWidget(0, len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().sortIndicatorChanged.connect(self._sort_changed)
        self.table.setSortingEnabled(True)
        self.table.sortItems(self.sort_column, self.sort_order)
        self.filter_bar = (
            ColumnFilterBar(self.table, headers, skip_filters) if filters else None
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        if self.filter_bar:
            layout.addWidget(self.filter_bar)
        layout.addWidget(self.table, 1)

    def _sort_changed(self, column: int, order: Qt.SortOrder) -> None:
        self.sort_column, self.sort_order = column, order

    def fill(self, rows: list[list[object]]) -> None:
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for column, value in enumerate(row):
                cell = value if isinstance(value, Cell) else Cell(value)
                text = tr(cell.text, fragments=False) if cell.translate else cell.text
                item = SortableItem(text, cell.sort)
                if cell.translate:
                    item.setData(SOURCE_ROLE, str(cell.text))
                    item.setData(TARGET_ROLE, str(text))
                if cell.data is not None:
                    item.setData(Qt.UserRole, cell.data)
                self.table.setItem(row_index, column, item)
        self.table.setSortingEnabled(True)
        self.table.sortItems(self.sort_column, self.sort_order)
        self.table.resizeColumnsToContents()
        if self.filter_bar:
            self.filter_bar.apply()

    def set_extra_predicate(self, predicate) -> None:
        if self.filter_bar:
            self.filter_bar.set_extra_predicate(predicate)


def date_sort_value(value: object) -> int:
    match = DATE_RE.search(str(value))
    if not match:
        return -1
    groups = match.groupdict(default="00")
    return int("".join(groups[name] for name in (
        "year", "month", "day", "hour", "minute", "second"
    )))
