from __future__ import annotations

from concurrent.futures import CancelledError
import socket
import time


class BoundedOutputCapture:
    """Keep complete small output and the head/tail of oversized output."""

    def __init__(self, limit: int):
        if limit < 2:
            raise ValueError("Output limit must be at least two bytes.")
        self.limit = int(limit)
        self.head_limit = self.limit // 2
        self.tail_limit = self.limit - self.head_limit
        self.total = 0
        self._data = bytearray()
        self._head: bytes | None = None
        self._tail = bytearray()

    @property
    def truncated(self) -> bool:
        return self._head is not None

    @property
    def omitted(self) -> int:
        if not self.truncated:
            return 0
        return max(0, self.total - len(self._head or b"") - len(self._tail))

    def add(self, chunk: bytes | bytearray | memoryview) -> None:
        payload = bytes(chunk)
        if not payload:
            return
        self.total += len(payload)
        if self._head is None:
            if len(self._data) + len(payload) <= self.limit:
                self._data.extend(payload)
                return
            combined = self._data + payload
            self._head = bytes(combined[:self.head_limit])
            self._tail = bytearray(combined[-self.tail_limit:])
            self._data.clear()
            return
        self._tail.extend(payload)
        if len(self._tail) > self.tail_limit:
            del self._tail[:-self.tail_limit]

    def render_text(self, marker: str, encoding: str = "utf-8") -> str:
        if self._head is None:
            return bytes(self._data).decode(encoding, errors="replace")
        head = self._head.decode(encoding, errors="replace")
        tail = bytes(self._tail).decode(encoding, errors="replace")
        return f"{head}\n[{marker}]\n{tail}"


def read_channel_output(
    channel,
    timeout: float | None,
    limit: int = 8 * 1024 * 1024,
    chunk_size: int = 64 * 1024,
    cancel_event=None,
) -> tuple[int, BoundedOutputCapture]:
    """Drain stdout/stderr progressively before requesting the exit status."""
    capture = BoundedOutputCapture(limit)
    last_activity = time.monotonic()
    while True:
        if cancel_event is not None and cancel_event.is_set():
            raise CancelledError()
        received = False
        while channel.recv_ready():
            if cancel_event is not None and cancel_event.is_set():
                raise CancelledError()
            capture.add(channel.recv(chunk_size))
            received = True
        while channel.recv_stderr_ready():
            if cancel_event is not None and cancel_event.is_set():
                raise CancelledError()
            capture.add(channel.recv_stderr(chunk_size))
            received = True
        finished = channel.exit_status_ready() or bool(
            getattr(channel, "closed", False)
        )
        if finished and not channel.recv_ready() and not channel.recv_stderr_ready():
            break
        if received:
            last_activity = time.monotonic()
        elif timeout is not None and time.monotonic() - last_activity >= timeout:
            raise socket.timeout("SSH command output timed out.")
        else:
            time.sleep(0.01)
    return channel.recv_exit_status(), capture
