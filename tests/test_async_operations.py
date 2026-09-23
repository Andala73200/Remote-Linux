import os
import time
import unittest
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget

import app.core.temp_edit as temp_edit_module
import app.main_window as main_window_module
import app.widgets.services_widget as services_module
from app.core.transfer_stream import TransferCancelled
from app.core.transfers import TransferWorker
from app.dialogs.logs_dialog import LogsDialog, shutdown_log_readers
from app.widgets.transfer_queue import TransferQueueWidget, TransferRow


APP = QApplication.instance() or QApplication([])


class AsyncCallbackThreadTests(unittest.TestCase):
    def test_result_callback_runs_in_the_qt_application_thread(self):
        from PySide6.QtCore import QThread
        from app.core.async_task import run_async

        observed = []
        run_async(lambda: "ok", lambda value: observed.append((value, QThread.currentThread())))
        deadline = time.monotonic() + 2.0
        while not observed and time.monotonic() < deadline:
            APP.processEvents()
            time.sleep(0.005)

        self.assertEqual(observed[0][0], "ok")
        self.assertIs(observed[0][1], APP.thread())


class ServiceSessionRaceTests(unittest.TestCase):
    def test_action_remains_bound_to_the_origin_session(self):
        captured = {}
        original = services_module.run_async

        def defer(function, on_result=None, on_error=None, guard=None):
            captured["function"] = function

        class FakeSession:
            def __init__(self, profile_id):
                self.profile = SimpleNamespace(id=profile_id)
                self.calls = []

            def systemctl(self, action, service, password):
                self.calls.append((action, service, password))
                return 0, "ok"

        try:
            services_module.run_async = defer
            widget = services_module.ServicesWidget(SimpleNamespace(settings={}))
            first = FakeSession("first")
            second = FakeSession("second")
            widget.session = first
            widget._execute_action("restart", "demo.service", "secret")
            widget.session = second

            captured["function"]()

            self.assertEqual(first.calls, [("restart", "demo.service", "secret")])
            self.assertEqual(second.calls, [])
            widget.close()
        finally:
            services_module.run_async = original


class TemporaryEditTests(unittest.TestCase):
    @staticmethod
    def _watch_local_file(manager, content="same"):
        local = manager.root / "demo.conf"
        local.write_text(content, encoding="utf-8")
        size, mtime_ns = manager._local_state(local)
        watched = temp_edit_module.WatchedFile(
            profile_id="profile-a",
            remote_path="/etc/demo.conf",
            local_path=local,
            digest=manager._digest(local),
            local_size=size,
            local_mtime_ns=mtime_ns,
            remote_signature=(size, 1, 0),
            original_text=content,
        )
        manager.watched[str(local)] = watched
        return watched

    def test_reopening_does_not_overwrite_unsaved_local_changes(self):
        original_async = temp_edit_module.run_async
        original_open = temp_edit_module.QDesktopServices.openUrl

        def immediate(function, on_result=None, on_error=None, guard=None):
            try:
                result = function()
            except Exception as exc:
                if on_error:
                    on_error(str(exc))
            else:
                if on_result:
                    on_result(result)

        class FakeSession:
            def __init__(self):
                self.profile = SimpleNamespace(id="profile-a")
                self.downloads = 0

            def remote_file_signature(self, _path):
                return 10, self.downloads, 0

            def download_file(self, _remote, local):
                self.downloads += 1
                with open(local, "w", encoding="utf-8") as stream:
                    stream.write(f"remote-{self.downloads}")

        session = FakeSession()
        manager = None
        try:
            temp_edit_module.run_async = immediate
            temp_edit_module.QDesktopServices.openUrl = lambda _url: True
            manager = temp_edit_module.TempEditManager(QWidget(), lambda: session)
            manager.open_remote(session, "/etc/demo.conf")
            watched = next(iter(manager.watched.values()))
            watched.local_path.write_text("LOCAL-UNSAVED", encoding="utf-8")

            manager.open_remote(session, "/etc/demo.conf")

            self.assertEqual(
                watched.local_path.read_text(encoding="utf-8"),
                "LOCAL-UNSAVED",
            )
            self.assertEqual(session.downloads, 1)
        finally:
            if manager:
                manager.close()
            temp_edit_module.run_async = original_async
            temp_edit_module.QDesktopServices.openUrl = original_open

    def test_metadata_touch_after_upload_is_not_reported_as_pending(self):
        manager = temp_edit_module.TempEditManager(QWidget(), lambda: None)
        try:
            watched = self._watch_local_file(manager)
            watched.alerted = True
            newer = watched.local_mtime_ns + 1_000_000_000
            os.utime(watched.local_path, ns=(newer, newer))

            self.assertEqual(manager.pending_changes(), [])
            self.assertFalse(watched.alerted)
            self.assertEqual(watched.local_mtime_ns, newer)
        finally:
            manager.close()

    def test_real_content_change_is_still_reported_as_pending(self):
        manager = temp_edit_module.TempEditManager(QWidget(), lambda: None)
        try:
            watched = self._watch_local_file(manager)
            watched.local_path.write_text("changed", encoding="utf-8")

            self.assertEqual(manager.pending_changes(), ["/etc/demo.conf"])
        finally:
            manager.close()

    def test_metadata_only_change_does_not_raise_file_alert(self):
        manager = temp_edit_module.TempEditManager(QWidget(), lambda: None)
        try:
            watched = self._watch_local_file(manager)
            alerts = []
            manager.file_changed.connect(alerts.append)
            newer = watched.local_mtime_ns + 1_000_000_000
            os.utime(watched.local_path, ns=(newer, newer))

            manager._poll()
            watched.changed_at = time.monotonic() - 2.0
            manager._poll()

            self.assertEqual(alerts, [])
            self.assertFalse(watched.alerted)
        finally:
            manager.close()


class TransferTests(unittest.TestCase):
    def test_cancelled_transfer_has_a_distinct_signal(self):
        class FakeSession:
            def upload_paths(self, *_args, **_kwargs):
                raise TransferCancelled("Annulation demandée")

        cancelled = []
        failed = []
        worker = TransferWorker(FakeSession(), "upload", ["a"], "/tmp")
        worker.cancelled.connect(cancelled.append)
        worker.failed.connect(failed.append)

        worker.run()

        self.assertEqual(cancelled, ["Annulation demandée"])
        self.assertEqual(failed, [])

    def test_retry_requires_and_rebinds_the_original_profile(self):
        queue = TransferQueueWidget()
        original_session = SimpleNamespace(profile=SimpleNamespace(id="profile-a"))
        other_session = SimpleNamespace(profile=SimpleNamespace(id="profile-b"))
        replacement_session = SimpleNamespace(profile=SimpleNamespace(id="profile-a"))
        row = TransferRow("demo", "upload")
        job = {
            "session": original_session,
            "row": row,
            "finished": True,
            "success": False,
            "cancelled": False,
            "worker": None,
        }

        queue.set_session(other_session)
        queue._row_action(job)
        self.assertTrue(job["finished"])
        self.assertIn("profil d’origine", row.details.text())

        started = []
        queue._start_next = lambda: started.append(True)
        queue.set_session(replacement_session)
        queue._row_action(job)

        self.assertFalse(job["finished"])
        self.assertIs(job["session"], replacement_session)
        self.assertEqual(started, [True])
        queue.close()


class UpdateStateTests(unittest.TestCase):
    def test_connection_change_cannot_leave_update_state_locked(self):
        captured = {}
        original = main_window_module.run_async

        def defer(function, on_result=None, on_error=None, guard=None):
            captured["error"] = on_error

        class DummyWindow:
            def __init__(self):
                self.session = SimpleNamespace(
                    install_updates=lambda *_args: (0, "ok")
                )
                self._updates_operation = None
                self.updates_installing = False
                self._status = SimpleNamespace(showMessage=lambda _text: None)

            def statusBar(self):
                return self._status

        try:
            main_window_module.run_async = defer
            window = DummyWindow()
            main_window_module.MainWindow._install_updates(
                window,
                ["demo-package"],
                None,
            )
            self.assertTrue(window.updates_installing)
            window.session = SimpleNamespace()

            captured["error"]("Connexion perdue")

            self.assertFalse(window.updates_installing)
            self.assertIsNone(window._updates_operation)
        finally:
            main_window_module.run_async = original


class LogReaderLifetimeTests(unittest.TestCase):
    def test_dialog_keeps_a_running_reader_alive_until_it_stops(self):
        class BlockingSession:
            def open_stream(self, _command):
                time.sleep(3.3)
                raise RuntimeError("stream stopped")

        dialog = LogsDialog(
            "test",
            "",
            "journalctl -f",
            session=BlockingSession(),
            live=True,
        )
        time.sleep(0.05)
        reader = dialog.reader

        dialog._stop_reader()

        self.assertTrue(reader.isRunning())
        self.assertTrue(shutdown_log_readers(1500))
        dialog.close()


if __name__ == "__main__":
    unittest.main()
