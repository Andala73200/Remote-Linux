from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from app.i18n import ntr, tr


class UpdatesDialog(QDialog):
    def __init__(self, updates: list[dict[str, str]], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Mises à jour disponibles")
        self.resize(820, 520)

        update_label = ntr(len(updates), 'ui.update_detected', 'ui.updates_detected')
        label = QLabel(
            f"{len(updates)} {update_label}. "
            f"{tr('ui.clear_the_packages_you_do_not_want_to_install')}"
        )
        label.setWordWrap(True)

        self.select_all_button = QPushButton("Tout cocher")
        self.select_none_button = QPushButton("Tout décocher")
        self.selection_label = QLabel()

        selection_controls = QHBoxLayout()
        selection_controls.addWidget(self.select_all_button)
        selection_controls.addWidget(self.select_none_button)
        selection_controls.addStretch(1)
        selection_controls.addWidget(self.selection_label)

        self.table = QTableWidget(len(updates), 4)
        self.table.setHorizontalHeaderLabels(
            ["Paquet", "Version actuelle", "Nouvelle version", "Architecture"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)

        for row, update in enumerate(updates):
            package = QTableWidgetItem(str(update.get("name", "")))
            package.setFlags(package.flags() | Qt.ItemIsUserCheckable)
            package.setCheckState(Qt.Checked)
            self.table.setItem(row, 0, package)
            self.table.setItem(row, 1, QTableWidgetItem(str(update.get("old", ""))))
            self.table.setItem(row, 2, QTableWidgetItem(str(update.get("new", ""))))
            self.table.setItem(row, 3, QTableWidgetItem(str(update.get("arch", ""))))

        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setStretchLastSection(True)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.install_button = buttons.addButton(
            "Installer la sélection", QDialogButtonBox.AcceptRole
        )

        self.select_all_button.clicked.connect(lambda: self._set_all_checked(True))
        self.select_none_button.clicked.connect(lambda: self._set_all_checked(False))
        self.table.itemChanged.connect(self._update_selection_state)
        self.install_button.clicked.connect(self._accept_if_selected)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(label)
        layout.addLayout(selection_controls)
        layout.addWidget(self.table, 1)
        layout.addWidget(buttons)

        self._update_selection_state()

    def selected_packages(self) -> list[str]:
        result = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.checkState() == Qt.Checked:
                result.append(item.text())
        return result

    def _set_all_checked(self, checked: bool) -> None:
        state = Qt.Checked if checked else Qt.Unchecked
        self.table.blockSignals(True)
        try:
            for row in range(self.table.rowCount()):
                item = self.table.item(row, 0)
                if item:
                    item.setCheckState(state)
        finally:
            self.table.blockSignals(False)
        self._update_selection_state()

    def _update_selection_state(self, *_args) -> None:
        selected = len(self.selected_packages())
        total = self.table.rowCount()
        selected_label = ntr(selected, 'ui.selected_singular', 'ui.selected_plural')
        self.selection_label.setText(f"{selected} / {total} {selected_label}")
        self.install_button.setText(f"{tr('ui.install_selection')} ({selected})")
        self.install_button.setEnabled(selected > 0)
        self.select_all_button.setEnabled(selected < total)
        self.select_none_button.setEnabled(selected > 0)

    def _accept_if_selected(self) -> None:
        if self.selected_packages():
            self.accept()
