from __future__ import annotations

import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from websocket import ABNF, WebSocketConnectionClosedException

from app.core.bounded_output import BoundedOutputCapture, read_channel_output
from app.core.cloudflare_socket import CloudflareSocket
from app.i18n_catalog import (
    DEFAULT_LANGUAGE, load_catalogs, reverse_index, translate_from_catalogs,
)
from app.storage import Storage


class BoundedOutputTests(unittest.TestCase):
    def test_large_output_keeps_head_and_tail(self):
        capture = BoundedOutputCapture(20)
        capture.add(b"0123456789")
        capture.add(b"ABCDEFGHIJKLMN")

        self.assertTrue(capture.truncated)
        self.assertEqual(capture.total, 24)
        self.assertEqual(capture.omitted, 4)
        rendered = capture.render_text("4 bytes omitted")
        self.assertTrue(rendered.startswith("0123456789"))
        self.assertTrue(rendered.endswith("EFGHIJKLMN"))
        self.assertIn("4 bytes omitted", rendered)


class _FakeChannel:
    def __init__(self, stdout=None, stderr=None, code=0, closed=False):
        self.stdout = list(stdout or [])
        self.stderr = list(stderr or [])
        self.code = code
        self.closed = closed

    def recv_ready(self):
        return bool(self.stdout)

    def recv(self, _size):
        return self.stdout.pop(0)

    def recv_stderr_ready(self):
        return bool(self.stderr)

    def recv_stderr(self, _size):
        return self.stderr.pop(0)

    def exit_status_ready(self):
        return not self.stdout and not self.stderr and not self.closed

    def recv_exit_status(self):
        return self.code


class ChannelDrainTests(unittest.TestCase):
    def test_channel_is_drained_before_exit_status_with_a_shared_limit(self):
        channel = _FakeChannel([b"A" * 15], [b"B" * 15], code=7)
        code, capture = read_channel_output(
            channel, timeout=1.0, limit=20, chunk_size=8
        )
        self.assertEqual(code, 7)
        self.assertEqual(capture.total, 30)
        self.assertEqual(capture.omitted, 10)

    def test_closed_channel_without_exit_status_does_not_wait_forever(self):
        channel = _FakeChannel(code=-1, closed=True)
        code, capture = read_channel_output(channel, timeout=0.05, limit=20)
        self.assertEqual(code, -1)
        self.assertEqual(capture.total, 0)


class I18nKeyTests(unittest.TestCase):
    def test_direct_fragment_keys_take_priority_over_legacy_replacements(self):
        catalogs = load_catalogs()
        strings = reverse_index(catalogs, "strings")
        fragments = reverse_index(catalogs, "fragments")

        translated = translate_from_catalogs(
            "fragment.read_failed", "fr", catalogs, strings, fragments
        )
        self.assertEqual(translated, "Lecture impossible :")
        self.assertEqual(
            translate_from_catalogs(
                "fragment.unknown_key", "fr", catalogs, strings, fragments
            ),
            "fragment.unknown_key",
        )
        self.assertEqual(DEFAULT_LANGUAGE, "en")


class StorageRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.target = self.root / "config.json"
        Storage(self.target)

    def tearDown(self):
        self.temp.cleanup()

    def test_io_error_never_rewrites_existing_configuration(self):
        original = self.target.read_bytes()
        with patch.object(Path, "read_text", side_effect=OSError("blocked")):
            with self.assertRaises(OSError):
                Storage(self.target)
        self.assertEqual(self.target.read_bytes(), original)

    def test_failed_corrupt_backup_never_overwrites_original(self):
        self.target.write_text("{broken", encoding="utf-8")
        original = self.target.read_bytes()
        with patch.object(shutil, "copy2", side_effect=OSError("backup blocked")):
            with self.assertRaises(OSError):
                Storage(self.target)
        self.assertEqual(self.target.read_bytes(), original)

    def test_corrupt_json_is_backed_up_before_defaults_are_written(self):
        self.target.write_text("{broken", encoding="utf-8")
        Storage(self.target)
        backups = list(self.root.glob("config.corrompu-*.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), "{broken")
        json.loads(self.target.read_text(encoding="utf-8"))


class _FakeWebSocket:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.sent = False
        self.closed = False
        self.sock = None

    def settimeout(self, _timeout):
        pass

    def recv_data(self, control_frame=True):
        del control_frame
        if not self.sent:
            self.sent = True
            return ABNF.OPCODE_BINARY, self.payload
        while not self.closed:
            time.sleep(0.005)
        raise WebSocketConnectionClosedException()

    def send(self, payload, opcode=None):
        del opcode
        return len(payload)

    def abort(self):
        self.closed = True

    def close(self, timeout=None):
        del timeout
        self.closed = True


class CloudflareBackpressureTests(unittest.TestCase):
    def test_receive_buffer_is_bounded_without_losing_payload(self):
        old_max = CloudflareSocket.MAX_RECV_BUFFER
        old_resume = CloudflareSocket.RESUME_RECV_BUFFER
        CloudflareSocket.MAX_RECV_BUFFER = 32
        CloudflareSocket.RESUME_RECV_BUFFER = 16
        sock = None
        try:
            payload = bytes(range(96))
            sock = CloudflareSocket(_FakeWebSocket(payload), "demo", 1.0)
            received = bytearray()
            deadline = time.monotonic() + 2.0
            while len(received) < len(payload) and time.monotonic() < deadline:
                with sock._condition:
                    self.assertLessEqual(len(sock._recv_buffer), 32)
                received.extend(sock.recv(16))
            self.assertEqual(bytes(received), payload)
        finally:
            if sock:
                sock.close()
            CloudflareSocket.MAX_RECV_BUFFER = old_max
            CloudflareSocket.RESUME_RECV_BUFFER = old_resume


if __name__ == "__main__":
    unittest.main()
