from __future__ import annotations

import unittest

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QLabel, QTableWidget

from app.i18n import configure


class I18nPerformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.language = configure(cls.app, "en")

    def setUp(self) -> None:
        self.language.set_language("en")

    def test_dynamic_labels_are_still_translated(self) -> None:
        label = QLabel("Déconnecté")
        label.show()
        self.app.processEvents()
        self.assertEqual(label.text(), "Disconnected")
        label.setText("Sauvegardes OK")
        self.app.processEvents()
        self.assertEqual(label.text(), "Backups OK")
        label.close()

    def test_table_repaints_do_not_trigger_translation_scan(self) -> None:
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


if __name__ == "__main__":
    unittest.main()
