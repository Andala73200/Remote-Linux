from __future__ import annotations

import socket
import threading

import websocket
from websocket import ABNF


class CloudflareSocket:
    """Socket adapter carrying SSH through a bounded WebSocket receive buffer."""

    MAX_RECV_BUFFER = 8 * 1024 * 1024
    RESUME_RECV_BUFFER = 4 * 1024 * 1024

    def __init__(self, ws: websocket.WebSocket, hostname: str, timeout: float | None):
        self._ws = ws
        self._hostname = hostname
        self._timeout = timeout
        self._recv_buffer = bytearray()
        self._condition = threading.Condition()
        self._send_lock = threading.Lock()
        self._closed = False
        self._error: BaseException | None = None

        # Keep the underlying WebSocket blocking. Paramiko timeouts are
        # reproduced locally on the bounded receive buffer.
        self._ws.settimeout(None)
        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            name=f"CloudflareReader-{hostname}",
            daemon=True,
        )
        self._reader_thread.start()

    def _append_payload(self, payload: bytes) -> None:
        view = memoryview(payload)
        offset = 0
        while offset < len(view):
            with self._condition:
                if len(self._recv_buffer) >= self.MAX_RECV_BUFFER:
                    self._condition.wait_for(
                        lambda: self._closed
                        or len(self._recv_buffer) <= self.RESUME_RECV_BUFFER
                    )
                if self._closed:
                    return
                available = self.MAX_RECV_BUFFER - len(self._recv_buffer)
                take = min(available, len(view) - offset)
                if take <= 0:
                    continue
                self._recv_buffer.extend(view[offset:offset + take])
                offset += take
                self._condition.notify_all()

    def _reader_loop(self) -> None:
        try:
            while not self._closed:
                opcode, payload = self._ws.recv_data(control_frame=True)
                if opcode == ABNF.OPCODE_CLOSE:
                    return
                if opcode in (ABNF.OPCODE_PING, ABNF.OPCODE_PONG):
                    continue
                if opcode not in (ABNF.OPCODE_BINARY, ABNF.OPCODE_TEXT):
                    continue
                if isinstance(payload, str):
                    payload = payload.encode("utf-8")
                if payload:
                    self._append_payload(payload)
        except websocket.WebSocketConnectionClosedException:
            pass
        except OSError as exc:
            self._error = exc
        except Exception as exc:
            self._error = exc
        finally:
            with self._condition:
                self._closed = True
                self._condition.notify_all()

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def last_error(self) -> BaseException | None:
        return self._error

    def recv(self, size: int) -> bytes:
        if size <= 0:
            return b""
        with self._condition:
            ready = self._condition.wait_for(
                lambda: bool(self._recv_buffer) or self._closed,
                timeout=self._timeout,
            )
            if not ready:
                raise socket.timeout("Délai de lecture Cloudflare dépassé.")
            if self._recv_buffer:
                result = bytes(self._recv_buffer[:size])
                del self._recv_buffer[:size]
                self._condition.notify_all()
                return result
            if self._error:
                if isinstance(self._error, OSError):
                    raise self._error
                raise OSError(f"Lecture Cloudflare interrompue : {self._error}")
            return b""

    def send(self, data: bytes | bytearray | memoryview) -> int:
        if self.closed:
            raise OSError("Le canal Cloudflare est fermé.")
        payload = bytes(data)
        if not payload:
            return 0
        with self._send_lock:
            try:
                self._ws.send(payload, opcode=ABNF.OPCODE_BINARY)
            except websocket.WebSocketTimeoutException as exc:
                raise socket.timeout("Délai d'envoi Cloudflare dépassé.") from exc
            except websocket.WebSocketConnectionClosedException as exc:
                self._mark_closed(exc)
                raise OSError("Le canal Cloudflare a été fermé.") from exc
            except OSError as exc:
                self._mark_closed(exc)
                raise
        return len(payload)

    def sendall(self, data: bytes | bytearray | memoryview) -> None:
        self.send(data)

    def _mark_closed(self, error: BaseException | None = None) -> None:
        with self._condition:
            if error is not None and self._error is None:
                self._error = error
            self._closed = True
            self._condition.notify_all()

    def settimeout(self, timeout: float | None) -> None:
        self._timeout = timeout

    def gettimeout(self) -> float | None:
        return self._timeout

    def setblocking(self, blocking: bool) -> None:
        self.settimeout(None if blocking else 0.0)

    def getpeername(self) -> tuple[str, int]:
        return self._hostname, 443

    def getsockname(self):
        try:
            return self._ws.sock.sock.getsockname()
        except Exception:
            return "127.0.0.1", 0

    def fileno(self) -> int:
        try:
            return int(self._ws.sock.sock.fileno())
        except Exception:
            return -1

    def shutdown(self, _how: int = socket.SHUT_RDWR) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._mark_closed()
        try:
            self._ws.abort()
        except Exception:
            pass
        try:
            self._ws.close(timeout=0.2)
        except Exception:
            pass
