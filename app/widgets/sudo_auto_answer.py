import re
import time
from collections.abc import Callable

import paramiko
from PySide6.QtCore import QObject, QTimer

from app.core.session import RemoteSession


class SudoAutoAnswer(QObject):
    """Detect the sudo prompt and answer it like real keyboard input."""

    ANSWER_DELAY_MS = 500
    REJECTION_WINDOW_SECONDS = 15.0

    def __init__(
        self,
        context_provider: Callable[
            [], tuple[RemoteSession | None, paramiko.Channel | None]
        ],
        output_callback: Callable[[bytes], None],
        error_callback: Callable[[str], None],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._context_provider = context_provider
        self._output_callback = output_callback
        self._error_callback = error_callback
        self._prompt_pattern = re.compile(
            r"\[sudo\]\s*(?:password|mot\s+de\s+passe)\s+"
            r"(?:for|pour|de)\s+([^:\r\n]+?)\s*:\s*$",
            re.IGNORECASE,
        )
        self._rejection_pattern = re.compile(
            r"(?:sorry,\s*try\s+again|mot\s+de\s+passe\s+incorrect)",
            re.IGNORECASE,
        )
        self._ansi_pattern = re.compile(
            r"\x1B(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))"
        )
        self._redaction_timer = QTimer(self)
        self._redaction_timer.setSingleShot(True)
        self._redaction_timer.timeout.connect(self._finish_redaction)
        self.reset()

    def reset(self) -> None:
        self._tail = ""
        self._last_prompt_at = 0.0
        self._last_answer_at = 0.0
        self._pending = False
        self._disabled_after_rejection = False
        self._generation = getattr(self, "_generation", 0) + 1
        self._redaction_timer.stop()
        self._redaction_secret = b""
        self._redaction_buffer = bytearray()

    def prepare_for_command(self) -> None:
        """Re-enable detection for the next command that is sent."""
        self._tail = ""
        self._pending = False
        self._disabled_after_rejection = False
        self._generation += 1
        self._finish_redaction(discard=True)

    def handle_output(self, data: bytes) -> None:
        plain_text = self._plain_text(data)
        self._detect_rejection(plain_text)
        self._detect_prompt(plain_text)
        visible = self._redact_password_echo(data)
        if visible:
            self._output_callback(visible)

    def _plain_text(self, data: bytes) -> str:
        text = data.decode("utf-8", errors="replace")
        return self._ansi_pattern.sub("", text).replace("\r", "")

    def _detect_rejection(self, text: str) -> None:
        if not self._last_answer_at or not self._rejection_pattern.search(text):
            return
        if time.monotonic() - self._last_answer_at > self.REJECTION_WINDOW_SECONDS:
            return

        # The refusal can come from the terminal read mode, not from the secret itself.
        # Keep the stored password and block only the
        # automatic retries for this command.
        self._pending = False
        self._disabled_after_rejection = True
        self._tail = ""
        self._generation += 1
        self._finish_redaction(discard=True)
        self._error_callback(
            "\r\n>>> L’envoi automatique sudo a été refusé pour cette commande. "
            "Le mot de passe mémorisé n’a pas été supprimé.\r\n"
        )

    def _detect_prompt(self, text: str) -> None:
        session, channel = self._context_provider()
        if (
            self._disabled_after_rejection
            or not session
            or not channel
            or channel.closed
            or not session.sudo_password
        ):
            return

        self._tail = (self._tail + text)[-768:]
        match = self._prompt_pattern.search(self._tail)
        if not match:
            return

        expected_user = session.profile.user.strip()
        if expected_user and match.group(1).strip() != expected_user:
            return

        now = time.monotonic()
        if self._pending or now - self._last_prompt_at < 3.0:
            return

        password = session.sudo_password.replace("\r", "").replace("\n", "")
        if not password:
            return

        self._pending = True
        self._tail = ""
        self._generation += 1
        generation = self._generation
        password_bytes = password.encode("utf-8")
        QTimer.singleShot(
            self.ANSWER_DELAY_MS,
            lambda: self._send_answer(generation, channel, password_bytes),
        )

    def _send_answer(
        self,
        generation: int,
        expected_channel: paramiko.Channel,
        password: bytes,
    ) -> None:
        channel = self._validated_channel(generation, expected_channel)
        self._pending = False
        if channel is None:
            return
        try:
            self._start_redaction(password)
            # sudo commands are sent with -S, so sudo reads one line
            # from stdin. The newline terminates that line without using /dev/tty.
            channel.sendall(password + b"\n")
            now = time.monotonic()
            self._last_prompt_at = now
            self._last_answer_at = now
        except Exception as exc:
            self._finish_redaction(discard=True)
            self._report_error("Envoi automatique du mot de passe sudo", exc)

    def _validated_channel(
        self,
        generation: int,
        expected_channel: paramiko.Channel,
    ) -> paramiko.Channel | None:
        if generation != self._generation:
            return None
        _, channel = self._context_provider()
        if channel is not expected_channel or channel is None or channel.closed:
            return None
        return channel

    def _start_redaction(self, password: bytes) -> None:
        self._redaction_secret = password
        self._redaction_buffer.clear()
        self._redaction_timer.start(2500)

    def _redact_password_echo(self, data: bytes) -> bytes:
        secret = self._redaction_secret
        if not secret:
            return data

        self._redaction_buffer.extend(data)
        buffer = bytes(self._redaction_buffer).replace(secret, b"")
        keep = self._matching_suffix_length(buffer, secret)
        if keep:
            visible, pending = buffer[:-keep], buffer[-keep:]
        else:
            visible, pending = buffer, b""
        self._redaction_buffer = bytearray(pending)
        return visible

    @staticmethod
    def _matching_suffix_length(data: bytes, secret: bytes) -> int:
        maximum = min(len(data), max(0, len(secret) - 1))
        for size in range(maximum, 0, -1):
            if data.endswith(secret[:size]):
                return size
        return 0

    def _finish_redaction(self, discard: bool = False) -> None:
        self._redaction_timer.stop()
        pending = bytes(self._redaction_buffer)
        self._redaction_secret = b""
        self._redaction_buffer.clear()
        if pending and not discard:
            self._output_callback(pending)

    def _report_error(self, action: str, exc: Exception) -> None:
        self._error_callback(f"\r\n>>> {action} impossible : {exc}\r\n")
