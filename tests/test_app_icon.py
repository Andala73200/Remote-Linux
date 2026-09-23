from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QIcon

import main


class ApplicationIconTests(unittest.TestCase):
    def test_png_icon_is_available_to_the_application(self):
        path = main._resource_path("app", "assets", "remote_linux.png")

        self.assertTrue(path.is_file())
        self.assertFalse(QIcon(str(path)).isNull())

    def test_windows_ico_contains_multiple_resolutions(self):
        path = main._resource_path("app", "assets", "remote_linux.ico")
        icon = QIcon(str(path))

        self.assertTrue(path.is_file())
        self.assertFalse(icon.isNull())
        self.assertGreaterEqual(len(icon.availableSizes()), 5)


if __name__ == "__main__":
    unittest.main()
