import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QToolButton

from app.core.storage_ops import StorageOperationsMixin
from app.storage import Storage
from app.widgets.storage_cards import PartitionRow


APP = QApplication.instance() or QApplication([])


class StorageSafetyTests(unittest.TestCase):
    def test_protected_partition_disables_unmount_and_automount(self):
        row = PartitionRow(
            {
                "name": "sda1",
                "path": "/dev/sda1",
                "mountpoints": ["/"],
                "fstype": "ext4",
            },
            protected=True,
        )
        actions = {
            action.text(): action.isEnabled()
            for action in row.findChild(QToolButton).menu().actions()
        }
        self.assertFalse(actions["⏏  Démonter"])
        self.assertFalse(actions["⚙  Montage automatique…"])
        row.close()

    def test_safe_mountpoint_accepts_data_locations(self):
        for path in ("/mnt/data", "/media/usb-1", "/srv/archive", "/data"):
            with self.subTest(path=path):
                self.assertEqual(StorageOperationsMixin._safe_mountpoint(path), path)

    def test_safe_mountpoint_rejects_system_and_shell_paths(self):
        unsafe = (
            "/",
            "/boot/data",
            "/etc/demo",
            "/var/lib/demo",
            "/mnt/data;touch-pwned",
            "/mnt/$(id)",
            "/mnt/space here",
        )
        for path in unsafe:
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    StorageOperationsMixin._safe_mountpoint(path)

    def test_remote_guard_checks_all_children_and_protected_mounts(self):
        destructive = StorageOperationsMixin._destructive_guard("/dev/sda")
        protected = StorageOperationsMixin._protected_mount_guard("/dev/sda1")
        self.assertIn("for node in $(lsblk", destructive)
        self.assertIn("swap actif sur le disque ou une partition", destructive)
        self.assertIn("/etc|/etc/*", protected)
        self.assertIn("/usr|/usr/*|/var|/var/*", protected)


class ConfigurationImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.target = self.root / "config.json"
        self.storage = Storage(self.target)

    def tearDown(self):
        self.temp.cleanup()

    def test_invalid_import_never_overwrites_current_configuration(self):
        original = self.target.read_bytes()
        source = self.root / "invalid.json"
        source.write_text("[]", encoding="utf-8")

        with self.assertRaises(ValueError):
            self.storage.import_configuration(source)

        self.assertEqual(self.target.read_bytes(), original)

    def test_valid_import_is_atomic_and_creates_a_backup(self):
        payload = json.loads(self.target.read_text(encoding="utf-8"))
        payload["settings"]["system_level"] = "expert"
        source = self.root / "valid.json"
        source.write_text(json.dumps(payload), encoding="utf-8")

        backup = self.storage.import_configuration(source)

        self.assertIsNotNone(backup)
        self.assertTrue(backup.exists())
        self.assertEqual(self.storage.settings["system_level"], "expert")


if __name__ == "__main__":
    unittest.main()
