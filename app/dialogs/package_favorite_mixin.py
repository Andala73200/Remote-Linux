from __future__ import annotations

from app.i18n import tr


class PackageFavoriteMixin:
    def _favorites(self) -> set[str]:
        return self.storage.favorite_packages(self.manager) if self.storage else set()

    def _sorted_rows(self, rows: list[dict[str, object]]) -> list[dict[str, object]]:
        favorites = {name.casefold() for name in self._favorites()}
        return sorted(
            rows,
            key=lambda row: (
                str(row.get("name") or "").casefold() not in favorites,
                int(row.get("popularity_rank", 10**9)),
            ),
        )

    def _render_favorites(self, query: str = "") -> None:
        known = {str(row.get("name") or "").casefold(): row for row in self.rows}
        needle = query.casefold()
        rows = []
        for name in sorted(self._favorites(), key=str.casefold):
            if needle and needle not in name.casefold():
                continue
            rows.append(known.get(name.casefold(), {
                "name": name, "repo": tr('ui.favorites'), "favorite_only": True,
            }))
        self._render_rows(rows)
        self._set_result_status(rows)
        self.search_button.setEnabled(True)
        if rows:
            self.table.selectRow(0)

    def _favorite_filter_changed(self, checked: bool) -> None:
        if self.storage:
            self.storage.set_package_favorites_only(self.manager, checked)
        self._update_filter_tooltip()
        if checked:
            self._render_favorites(self.search_edit.text().strip())
        elif self.search_edit.text().strip():
            self.search()
        else:
            self.rows.clear()
            self.table.setRowCount(0)
            self.status.setText(tr('ui.enter_a_name_or_keyword'))

    def _update_filter_tooltip(self) -> None:
        key = (
            'ui.show_all_packages'
            if self.favorite_filter_button.isChecked() else 'ui.favorites_only'
        )
        self.favorite_filter_button.setToolTip(tr(key))

    def _cell_clicked(self, row: int, column: int) -> None:
        if column != 0 or not self.storage:
            return
        item = self.table.item(row, 0)
        package = str(item.data(self._user_role) or "") if item else ""
        if not package:
            return
        current = {name.casefold() for name in self._favorites()}
        self.storage.set_package_favorite(
            self.manager, package, package.casefold() not in current
        )
        if self.favorite_filter_button.isChecked():
            self._render_favorites(self.search_edit.text().strip())
        else:
            self._render_rows(self._sorted_rows(self.rows))
