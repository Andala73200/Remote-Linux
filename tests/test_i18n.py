from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import (
    QApplication, QLabel, QPlainTextEdit, QTableWidget, QTableWidgetItem,
    QTreeWidget, QTreeWidgetItem,
)

from app.dialogs.preferences_dialog import PreferencesDialog
from app.dialogs.package_manager_dialog import PackageManagerDialog
from app.i18n import configure
from app.i18n_catalog import DEFAULT_LANGUAGE, load_catalog
from app.main_window import MainWindow
from app.storage import Storage
from app.widgets.table_tools import Cell, FilteredTablePage


class I18nTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.language = configure(cls.app, "en")

    def setUp(self) -> None:
        self.language.set_language("en")

    def test_catalogs_are_symmetric_and_fallback_is_english(self) -> None:
        english = load_catalog("en")
        french = load_catalog("fr")
        self.assertEqual(DEFAULT_LANGUAGE, "en")
        self.assertEqual(set(english["strings"]), set(french["strings"]))
        self.assertEqual(set(english["fragments"]), set(french["fragments"]))
        self.assertTrue(all(key.startswith("ui.") for key in english["strings"]))
        self.assertTrue(
            all(key.startswith("fragment.") for key in english["fragments"])
        )
        self.assertEqual(english["strings"]["ui.preferences"], "Preferences")
        self.assertEqual(french["strings"]["ui.preferences"], "Préférences")
        self.assertEqual(self.language.translate("ui.preferences"), "Preferences")
        self.language.set_language("fr")
        self.assertEqual(self.language.translate("ui.preferences"), "Préférences")

    def test_preferences_exposes_stable_language_codes(self) -> None:
        settings = {"language": "en"}
        dialog = PreferencesDialog(
            settings, "Production", True, True, "Windows Credential Manager"
        )
        dialog.show()
        self.app.processEvents()
        self.assertEqual(dialog.windowTitle(), "Preferences")
        self.assertEqual(
            dialog.sudo_status.text(),
            "A sudo password is securely saved for “Production”.",
        )
        self.assertEqual(
            dialog.backend_status.text(),
            "Credential store: Windows Credential Manager",
        )
        self.assertEqual(dialog.language.itemText(1), "French")
        self.assertEqual(dialog.language.itemText(2), "English")
        self.assertTrue(dialog.venv_prompt.isChecked())
        self.assertTrue(dialog.values()["terminal_venv_prompt_enabled"])
        self.language.set_language("fr")
        self.app.processEvents()
        self.assertIn("mot de passe sudo est enregistré", dialog.sudo_status.text())
        self.assertEqual(
            dialog.backend_status.text(),
            "Coffre utilisé : Windows Credential Manager",
        )
        self.assertEqual(dialog.language.itemText(1), "Français")
        self.assertEqual(dialog.language.itemText(2), "Anglais")
        self.language.set_language("en")
        self.assertEqual(
            [dialog.language.itemData(index) for index in range(3)],
            ["auto", "fr", "en"],
        )
        self.assertEqual(dialog.values()["language"], "en")
        dialog.close()

    def test_pip_search_clears_stale_rows_and_uses_a_real_i18n_key(self) -> None:
        dialog = PackageManagerDialog(None, "pip", lambda: None)
        dialog.table.setRowCount(1)
        dialog._failed("ERROR: Network is unreachable")
        self.assertEqual(dialog.table.rowCount(), 0)
        self.assertEqual(
            dialog.status.text(),
            "Operation failed: ERROR: Network is unreachable",
        )

        dialog._search_ready({"manager": "pip", "rows": []})
        self.assertTrue(dialog.status.text().startswith("No results."))
        dialog.close()

    def test_main_window_switches_between_english_and_french(self) -> None:
        path = Path(tempfile.mkdtemp()) / "config.json"
        storage = Storage(path)
        storage.settings["language"] = "en"
        window = MainWindow(storage)
        window.show()
        self.app.processEvents()
        self.assertEqual(window.tabs.tabText(2), "System")
        self.assertEqual(window.menuBar().actions()[0].text(), "&File")
        self.assertEqual(window.backups.verify_button.text(), "Verify integrity")

        window.tasks.status.setText(
            "32 timer(s)  •  18 tâche(s) cron  •  lecture complète"
        )
        window.tabs.setCurrentWidget(window.tasks)
        self.app.processEvents()
        self.assertEqual(
            window.tasks.status.text(),
            "32 timer(s)  •  18 cron task(s)  •  full access",
        )

        self.language.set_language("fr")
        self.app.processEvents()
        self.assertEqual(window.tabs.tabText(2), "Système")
        self.assertEqual(window.menuBar().actions()[0].text(), "&Fichier")
        self.assertEqual(window.backups.verify_button.text(), "Vérifier l’intégrité")
        window.close()

    def test_raw_terminal_or_log_output_is_never_translated(self) -> None:
        output = QPlainTextEdit("Erreur brute du serveur\nAucune sauvegarde distante")
        output.show()
        self.app.processEvents()
        self.assertEqual(
            output.toPlainText(),
            "Erreur brute du serveur\nAucune sauvegarde distante",
        )
        output.close()

    def test_remote_names_and_data_cells_are_never_translated(self) -> None:
        table = QTableWidget(1, 1)
        table.setHorizontalHeaderLabels(["État"])
        table.setItem(0, 0, QTableWidgetItem("Oui"))
        tree = QTreeWidget()
        tree.setHeaderLabels(["Nom"])
        tree.addTopLevelItem(QTreeWidgetItem(["Sauvegardes"]))
        table.show()
        tree.show()
        self.app.processEvents()
        self.assertEqual(table.horizontalHeaderItem(0).text(), "Status")
        self.assertEqual(table.item(0, 0).text(), "Oui")
        self.assertEqual(tree.headerItem().text(0), "Name")
        self.assertEqual(tree.topLevelItem(0).text(0), "Sauvegardes")
        table.close()
        tree.close()

    def test_explicit_dynamic_table_values_follow_language_changes(self) -> None:
        page = FilteredTablePage(["Portée locale"])
        page.fill([[Cell("Toutes interfaces", translate=True)]])
        page.show()
        self.app.processEvents()
        self.assertEqual(page.table.item(0, 0).text(), "All interfaces")
        self.language.set_language("fr")
        self.app.processEvents()
        self.assertEqual(page.table.item(0, 0).text(), "Toutes interfaces")
        page.close()

    def test_dynamic_labels_are_retranslated_after_set_text(self) -> None:
        label = QLabel("Déconnecté")
        label.show()
        self.app.processEvents()
        self.assertEqual(label.text(), "Disconnected")

        label.setText("Sauvegardes OK")
        self.app.processEvents()
        self.assertEqual(label.text(), "Backups OK")

        label.setText(
            "PostgreSQL 16.15  •  16 base(s)  •  6 connexion(s) observée(s)"
        )
        self.app.processEvents()
        self.assertEqual(
            label.text(),
            "PostgreSQL 16.15  •  16 database(s)  •  6 observed connection(s)",
        )
        label.close()

    def test_heavy_widgets_are_not_rescanned_on_repaint_or_layout(self) -> None:
        table = QTableWidget(200, 8)
        table.show()
        self.app.processEvents()
        scans = 0
        original = self.language._translate_table

        def counted(widget) -> None:
            nonlocal scans
            scans += 1
            original(widget)

        self.language._translate_table = counted
        try:
            for _index in range(100):
                QApplication.sendEvent(table, QEvent(QEvent.Type.UpdateRequest))
                QApplication.sendEvent(table, QEvent(QEvent.Type.LayoutRequest))
        finally:
            self.language._translate_table = original
            table.close()
        self.assertEqual(scans, 0)

    def test_heavy_widget_is_translated_only_on_its_first_show(self) -> None:
        table = QTableWidget(200, 8)
        table.setHorizontalHeaderLabels(["État"] * 8)
        scans = 0
        original = self.language._translate_table

        def counted(widget) -> None:
            nonlocal scans
            scans += 1
            original(widget)

        self.language._translate_table = counted
        try:
            table.show()
            self.app.processEvents()
            first_show_scans = scans
            for _index in range(20):
                table.hide()
                table.show()
                self.app.processEvents()
        finally:
            self.language._translate_table = original
            table.close()
        self.assertEqual(first_show_scans, 1)
        self.assertEqual(scans, first_show_scans)

    def test_composite_translation_reuses_full_catalog_entries(self) -> None:
        self.assertEqual(
            self.language.translate(
                "Dernière vérification : réussie — 2026-08-27T14:33:17"
            ),
            "Last verification: succeeded — 2026-08-27T14:33:17",
        )
        self.assertEqual(
            self.language.translate(
                ">>> Connexion Cloudflare intégrée vers ssh.example.test…"
            ),
            ">>> Integrated Cloudflare connection to ssh.example.test…",
        )

    def test_invalid_language_falls_back_to_auto(self) -> None:
        self.language.set_language("xx")
        self.assertEqual(self.language.requested, "auto")
        self.assertIn(self.language.effective, {"fr", "en"})


if __name__ == "__main__":
    unittest.main()
