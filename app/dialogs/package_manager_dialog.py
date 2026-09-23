from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout,
)

from app.core.async_task import run_async
from app.dialogs.package_favorite_mixin import PackageFavoriteMixin
from app.i18n import ntr, tr


class PackageManagerDialog(PackageFavoriteMixin, QDialog):
    def __init__(
        self, session, manager: str, password_provider, parent=None,
        venv_path: str = "", storage=None,
    ):
        super().__init__(parent)
        self.session = session
        self.manager = str(manager).lower()
        self.venv_path = str(venv_path or "")
        self.password_provider = password_provider
        self.storage = storage
        self.installing = False
        self.rows: list[dict[str, object]] = []
        self._user_role = Qt.UserRole
        self.setWindowTitle(f"{tr('ui.package_manager')} — {manager.upper()}")
        self.resize(1050, 650)
        info = QLabel(
            tr('ui.the_search_runs_directly_against_the_repositories_configured_on')
        )
        info.setWordWrap(True)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(tr('ui.package_name_or_keyword'))
        self.search_edit.returnPressed.connect(self.search)
        self.search_button = QPushButton(tr('ui.search_action'))
        self.search_button.clicked.connect(self.search)
        self.favorite_filter_button = QPushButton("★")
        self.favorite_filter_button.setObjectName("packageFavoriteFilter")
        self.favorite_filter_button.setCheckable(True)
        self.favorite_filter_button.setFixedWidth(38)
        self.favorite_filter_button.setChecked(
            bool(storage and storage.package_favorites_only(self.manager))
        )
        self.favorite_filter_button.setStyleSheet(
            "QPushButton#packageFavoriteFilter { font-size: 13pt; padding: 3px; }"
            "QPushButton#packageFavoriteFilter:checked { background: #5A4A1E;"
            " border-color: #D7AE45; color: #FFD166; }"
        )
        self.favorite_filter_button.toggled.connect(self._favorite_filter_changed)
        self._update_filter_tooltip()
        search_row = QHBoxLayout()
        search_row.addWidget(self.search_edit, 1)
        search_row.addWidget(self.search_button)
        search_row.addWidget(self.favorite_filter_button)
        self.status = QLabel(tr('ui.enter_a_name_or_keyword'))
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([
            "★", tr('ui.package'), tr('ui.version'), tr('ui.installed'),
            tr('ui.repository'), tr('ui.description'),
        ])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.cellClicked.connect(self._cell_clicked)
        self.install_button = QPushButton(
            tr('ui.install_or_update_selected_package')
            if self.manager == "pip" else tr('ui.install_selected_package')
        )
        self.install_button.setEnabled(False)
        self.install_button.clicked.connect(self.install)
        self.table.itemSelectionChanged.connect(
            lambda: self.install_button.setEnabled(self.table.currentRow() >= 0)
        )
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        self.close_button = buttons.button(QDialogButtonBox.Close)
        self.close_button.setText(tr('ui.close'))
        buttons.rejected.connect(self.reject)
        actions = QHBoxLayout()
        actions.addWidget(self.install_button)
        actions.addStretch(1)
        actions.addWidget(buttons)
        layout = QVBoxLayout(self)
        layout.addWidget(info)
        layout.addLayout(search_row)
        layout.addWidget(self.status)
        layout.addWidget(self.table, 1)
        layout.addLayout(actions)
        self.search_edit.setFocus()
        if self.favorite_filter_button.isChecked():
            self._render_favorites()

    def search(self) -> None:
        query = self.search_edit.text().strip()
        if self.favorite_filter_button.isChecked():
            self._render_favorites(query)
            return
        if not query:
            return
        self.table.setRowCount(0)
        self.search_button.setEnabled(False)
        self.install_button.setEnabled(False)
        self.status.setText(f"{tr('ui.search_status')} {self.manager.upper()} {tr('fragment.in_progress')}")
        session = self.session
        run_async(
            lambda: session.package_search(
                query, self.manager, self.venv_path
            ),
            self._search_ready,
            self._failed,
            guard=lambda: self.session is session,
        )

    def _search_ready(self, result: object) -> None:
        data = dict(result or {})
        self.rows = [dict(row) for row in data.get("rows") or []]
        rows = self._sorted_rows(self.rows)
        self._render_rows(rows)
        self._set_result_status(rows)
        self.search_button.setEnabled(True)
        if rows:
            self.table.selectRow(0)

    def _render_rows(self, rows: list[dict[str, object]]) -> None:
        favorites = {name.casefold() for name in self._favorites()}
        self.table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            name = str(row.get("name") or "")
            is_favorite = name.casefold() in favorites
            if self.manager == "pip":
                versions = ", ".join(row.get("versions") or [])
                details = (
                    f"{tr('ui.available_versions')}: {versions}"
                    if versions else ""
                )
                values = (
                    "★" if is_favorite else "☆", name,
                    row.get("version") or "—",
                    row.get("installed_version") or (
                        "—" if row.get("favorite_only") else tr('ui.no')
                    ),
                    row.get("repo") or tr('ui.configured_pip_indexes'),
                    details,
                )
            else:
                values = tuple(
                    row.get(key, "")
                    for key in ("name", "version", "installed", "repo", "summary")
                )
                values = ("★" if is_favorite else "☆", *values)
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.UserRole, name)
                if column == 0:
                    item.setTextAlignment(Qt.AlignCenter)
                if is_favorite:
                    item.setBackground(QColor("#263648" if column else "#5A4A1E"))
                    if column == 0:
                        item.setForeground(QColor("#FFD166"))
                    elif column == 1:
                        font = item.font()
                        font.setBold(True)
                        item.setFont(font)
                self.table.setItem(index, column, item)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setStretchLastSection(True)

    def _set_result_status(self, rows: list[dict[str, object]]) -> None:
        result_label = ntr(len(rows), 'ui.result_b39950', 'ui.results')
        if not rows:
            self.status.setText(tr('ui.no_results'))
        elif self.favorite_filter_button.isChecked():
            label = ntr(len(rows), 'ui.favorite', 'ui.favorites')
            self.status.setText(f"{len(rows)} {label}")
        else:
            self.status.setText(
                f"{len(rows)} {result_label} "
                f"{tr('ui.in_the_configured_repositories')}"
            )

    def install(self) -> None:
        row = self.table.currentRow()
        item = self.table.item(row, 1) if row >= 0 else None
        installed = self.table.item(row, 3) if row >= 0 else None
        if not item:
            return
        package = item.text()
        if (
            self.manager != "pip"
            and installed
            and installed.text() == "Oui"
        ):
            QMessageBox.information(self, tr('ui.package_already_installed'), f"{package} {tr('ui.is_already_installed')}")
            return
        try:
            command = self.session.package_install_preview(
                package, self.manager, self.venv_path
            )
        except ValueError as exc:
            QMessageBox.warning(self, tr('ui.invalid_package'), tr(str(exc)))
            return
        if QMessageBox.question(
            self, tr('ui.install_package'),
            f"{tr('ui.install')} « {package} » ?\n\n"
            f"{tr('ui.command_to_run')}\n{command}"
        ) != QMessageBox.Yes:
            return
        password = None if self.manager == "pip" else self.password_provider()
        if password is False:
            return
        self.search_button.setEnabled(False)
        self.install_button.setEnabled(False)
        self.status.setText(f"{tr('ui.installing')} {package} {tr('fragment.in_progress')}")
        self.installing = True
        self.close_button.setEnabled(False)
        session = self.session
        run_async(
            lambda: session.install_package(
                package, password, self.manager, self.venv_path
            ),
            lambda result: self._install_ready(package, result),
            self._failed,
            guard=lambda: self.session is session,
        )

    def _install_ready(self, package: str, result: object) -> None:
        code, output = result
        self.installing = False
        self.close_button.setEnabled(True)
        self.search_button.setEnabled(True)
        if int(code) == 0:
            QMessageBox.information(
                self, tr('ui.installation_completed'),
                str(output) or f"{package} {tr('ui.is_already_installed')}"
            )
            self.search()
            return
        self.status.setText(f"{tr('ui.installation_failed_for')} {package}.")
        QMessageBox.critical(self, tr('ui.installation_failed'), str(output) or f"Code {code}")

    def _failed(self, text: str) -> None:
        self.installing = False
        self.table.setRowCount(0)
        self.close_button.setEnabled(True)
        self.search_button.setEnabled(True)
        self.install_button.setEnabled(False)
        raw = str(text)
        details = "" if raw in {"ui.operation_failed", "fragment.operation_failed"} else (
            tr(raw) if raw.startswith(("ui.", "fragment.")) else raw
        )
        self.status.setText(f"{tr('ui.operation_failed')} {details}".rstrip())

    def reject(self) -> None:
        if self.installing:
            QMessageBox.information(
                self, tr('ui.installation_in_progress'),
                tr('ui.wait_for_the_installation_to_finish_before_closing_this')
            )
            return
        super().reject()
