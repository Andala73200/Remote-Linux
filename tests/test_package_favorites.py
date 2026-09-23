from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.dialogs.package_manager_dialog import PackageManagerDialog
from app.i18n import configure
from app.storage import Storage


class PackageFavoriteStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "data.json"
        self.storage = Storage(self.path)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_package_favorites_are_global_and_separated_by_manager(self) -> None:
        self.storage.set_package_favorite("pip", "openpyxl", True)
        self.storage.set_package_favorite("apt", "nginx", True)
        self.storage.set_package_favorite("dnf", "podman", True)

        reloaded = Storage(self.path)
        self.assertEqual(reloaded.favorite_packages("pip"), {"openpyxl"})
        self.assertEqual(reloaded.favorite_packages("apt"), {"nginx"})
        self.assertEqual(reloaded.favorite_packages("dnf"), {"podman"})

        profile_id = reloaded.profiles[0].id
        reloaded.remove_profile_data(profile_id)
        self.assertEqual(reloaded.favorite_packages("pip"), {"openpyxl"})

    def test_favorite_matching_is_case_insensitive(self) -> None:
        self.storage.set_package_favorite("pip", "OpenPyXL", True)
        self.storage.set_package_favorite("pip", "openpyxl", True)
        self.assertEqual(self.storage.favorite_packages("pip"), {"openpyxl"})
        self.storage.set_package_favorite("pip", "OPENPYXL", False)
        self.assertEqual(self.storage.favorite_packages("pip"), set())


class PackageFavoriteDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.language = configure(cls.app, "en")

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.storage = Storage(Path(self.temp.name) / "data.json")
        self.storage.set_package_favorite("pip", "openpyxl", True)
        self.dialog = PackageManagerDialog(
            None, "pip", lambda: None, storage=self.storage
        )

    def tearDown(self) -> None:
        self.dialog.close()
        self.temp.cleanup()

    def test_favorites_are_first_and_starred_in_normal_results(self) -> None:
        self.dialog._search_ready({"rows": [
            {"name": "openai", "popularity_rank": 2},
            {"name": "openpyxl", "popularity_rank": 20},
        ]})
        self.assertEqual(self.dialog.table.item(0, 1).text(), "openpyxl")
        self.assertEqual(self.dialog.table.item(0, 0).text(), "★")

    def test_filter_lists_global_favorites_without_a_remote_search(self) -> None:
        self.dialog.favorite_filter_button.setChecked(True)
        self.assertEqual(self.dialog.table.rowCount(), 1)
        self.assertEqual(self.dialog.table.item(0, 1).text(), "openpyxl")
        self.assertTrue(self.storage.package_favorites_only("pip"))

        reopened = PackageManagerDialog(
            None, "pip", lambda: None, storage=Storage(self.storage.path)
        )
        self.assertTrue(reopened.favorite_filter_button.isChecked())
        reopened.close()

    def test_clicking_the_star_updates_the_global_list(self) -> None:
        self.dialog._search_ready({"rows": [{"name": "requests"}]})
        self.dialog._cell_clicked(0, 0)
        self.assertIn("requests", self.storage.favorite_packages("pip"))

    def test_apt_and_dnf_use_the_same_global_filter(self) -> None:
        for manager, package in (("apt", "nginx"), ("dnf", "podman")):
            self.storage.set_package_favorite(manager, package, True)
            dialog = PackageManagerDialog(
                None, manager, lambda: None, storage=self.storage
            )
            dialog.favorite_filter_button.setChecked(True)
            self.assertEqual(dialog.table.item(0, 1).text(), package)
            dialog.close()

    def test_internal_fragment_key_can_never_leak_to_the_status(self) -> None:
        self.dialog._failed("fragment.operation_failed")
        self.assertNotIn("fragment.", self.dialog.status.text())


if __name__ == "__main__":
    unittest.main()
