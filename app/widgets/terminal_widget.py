import paramiko
from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from app.core.async_task import run_async
from app.core.cd_command import is_cd_command
from app.core.shell_reader import ShellReader
from app.core.session import RemoteSession
from app.core.terminal_shell_state import ShellCommandTracker
from app.i18n import tr
from app.widgets.terminal_completion_controller import TerminalCompletionController
from app.widgets.terminal_history_mixin import TerminalHistoryMixin
from app.widgets.terminal_notifications import TerminalNotificationCenter
from app.widgets.terminal_venv_mixin import TerminalVenvMixin
from app.widgets.vt_terminal import VtTerminal

class TerminalWidget(TerminalHistoryMixin, TerminalVenvMixin, QWidget):
    command_sent = Signal(str)
    favorites_requested = Signal(object)
    path_requested = Signal(str)
    path_changed = Signal(str)
    sudo_password_requested = Signal(object)
    package_manager_requested = Signal(str, object)
    venv_activation_requested = Signal(object, str)
    cd_completed = Signal(str, str, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.session: RemoteSession | None = None
        self.channel: paramiko.Channel | None = None
        self.reader: ShellReader | None = None
        self.last_command = ""
        self.current_path = "~"
        self.input_allowed = False
        self.package_managers: set[str] = set()
        self._init_venv_support()
        self.tracker = ShellCommandTracker()
        self._cwd_command = self._shell_setup_command()
        self._setup_pending = False
        self._setup_tail = b""
        self._setup_timer = QTimer(self)
        self._setup_timer.setSingleShot(True)
        self._setup_timer.timeout.connect(self._flush_setup_tail)
        self._follow_tree_pending: list[bool] = []

        self.console = VtTerminal()
        self.console.data_entered.connect(self._send_terminal_data)
        self.console.resized.connect(self._resize_pty)
        self.console.cwd_changed.connect(self.set_current_path)
        self.console.virtual_env_changed.connect(self.set_virtual_env)
        self.console.command_finished.connect(self._command_finished)
        self.completion = TerminalCompletionController(self)
        self._init_command_history()

        self.environment_label = QLabel("Linux —")
        self.environment_label.setStyleSheet("color: #8fa2b8;")
        self.environment_label.setToolTip("Distribution détectée sur le serveur")
        self.notifications = TerminalNotificationCenter()

        self.sudo_button = QPushButton("Sudo")
        self.sudo_button.setToolTip(
            "Envoyer le mot de passe sudo mémorisé dans le terminal actif. "
            "À utiliser uniquement lorsque sudo demande le mot de passe."
        )
        self.sudo_button.setMaximumWidth(72)
        self.sudo_button.clicked.connect(self.send_sudo_password)

        self.path_edit = QLineEdit(self.current_path)
        self.path_edit.setReadOnly(True)
        self.path_edit.setToolTip(
            "Dossier actuel du terminal actif — sélectionnable et copiable"
        )
        self.recall_button = QPushButton("↶")
        self.recall_button.setFixedWidth(38)
        self.recall_button.setToolTip(tr("ui.recall_last_command"))
        self.recall_button.setEnabled(False)
        self.recall_button.clicked.connect(self.recall_last_command)
        self.path_button = QPushButton("↗ Arbo.")
        self.path_button.setToolTip("Afficher ce dossier dans l’arborescence")
        self.path_button.clicked.connect(
            lambda: self.path_requested.emit(self.current_path)
        )
        self.favorite_button = QPushButton("★ Favoris ▼")
        self.favorite_button.clicked.connect(
            lambda: self.favorites_requested.emit(self.favorite_button)
        )

        action_row = QHBoxLayout()
        action_row.addWidget(self.sudo_button)
        action_row.addWidget(self.environment_label)
        action_row.addWidget(QLabel("Dossier :"))
        action_row.addWidget(self.path_edit, 1)
        action_row.addWidget(self.recall_button)
        action_row.addWidget(self.path_button)
        action_row.addWidget(self.favorite_button)
        action_row.addWidget(self.notifications)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(self.console, 1)
        layout.addWidget(self.completion.panel)
        layout.addLayout(action_row)
        self._update_enabled()

    def attach_session(self, session: RemoteSession, preserve_output: bool = True) -> None:
        self.detach_session()
        self.session = session
        if preserve_output:
            self._write_system(
                f"\r\n>>> Terminal connecté à {session.profile.name}\r\n"
            )
        self.channel = session.open_shell(
            width=self.console.columns, height=self.console.rows
        )
        self.reader = ShellReader(self.channel)
        self.reader.received.connect(self._on_shell_data)
        self.reader.failed.connect(
            lambda text: self._write_system(
                f"\r\n>>> Erreur terminal : {text}\r\n"
            )
        )
        self.reader.closed.connect(self._channel_closed)
        self.reader.start()
        self._setup_pending = True
        self._setup_tail = b""
        self._setup_timer.start(900)
        self.channel.send(self._cwd_command + "\n")
        run_async(
            session.remote_home,
            lambda path: self.set_current_path(str(path)),
            guard=lambda: self.session is session,
        )
        self._update_enabled()
        self.console.setFocus()

    def _on_shell_data(self, data: bytes) -> None:
        cleaned = self._filter_setup_echo(data)
        if cleaned:
            self.console.feed_bytes(cleaned)

    def _filter_setup_echo(self, data: bytes) -> bytes:
        if not self._setup_pending:
            return data
        combined = self._setup_tail + data
        command = self._cwd_command.encode("utf-8")
        patterns = (command + b"\r\n", command + b"\n")
        for pattern in patterns:
            if pattern in combined:
                self._setup_pending = False
                self._setup_timer.stop()
                self._setup_tail = b""
                return combined.replace(pattern, b"", 1)
        keep = len(command) + 8
        if len(combined) <= keep:
            self._setup_tail = combined
            return b""
        output, self._setup_tail = combined[:-keep], combined[-keep:]
        return output

    def _flush_setup_tail(self) -> None:
        if not self._setup_pending:
            return
        self._setup_pending = False
        pending, self._setup_tail = self._setup_tail, b""
        if pending:
            self.console.feed_bytes(pending)

    def _send_terminal_data(self, data: bytes) -> None:
        if not self.channel or self.channel.closed or not self.input_allowed:
            return
        if self.completion.handle_input(data):
            return
        if data in {b"\x1b[A", b"\x1bOA"}:
            restore_line = (
                self.tracker.line
                if self.tracker.at_prompt and self.tracker.reliable
                else None
            )
            self.remember_history_restore_line(restore_line)
        try:
            payload, manager, command = self.tracker.input(
                data, self.package_managers
            )
            self.channel.sendall(payload)
            if manager:
                self.package_manager_requested.emit(manager, self)
            elif command is not None:
                self._follow_tree_pending.append(False)
                self.record_command_history(command)
                self.command_sent.emit(command)
        except Exception as exc:
            self._write_system(f"\r\n>>> Erreur d'envoi : {exc}\r\n")

    def _resize_pty(self, columns: int, rows: int) -> None:
        if self.channel and not self.channel.closed:
            try:
                self.channel.resize_pty(width=columns, height=rows)
            except Exception:
                pass

    def _channel_closed(self) -> None:
        if self.session and self.channel and self.channel.closed:
            self._write_system(
                "\r\n>>> Terminal fermé. Ouvre un nouvel onglet avec +.\r\n"
            )
            self._update_enabled()

    def detach_session(self) -> None:
        self.completion.cancel()
        if self.reader:
            self.reader.stop()
            self.reader.wait(600)
            self.reader = None
        if self.session and self.channel:
            self.session.close_shell(self.channel)
        self.channel = None
        self.session = None
        self._setup_pending = False
        self._setup_timer.stop()
        self._setup_tail = b""
        self.tracker.reset()
        self._follow_tree_pending.clear()
        self._venv_session_detached()
        self._update_enabled()

    def set_input_allowed(self, allowed: bool, message: str = "") -> None:
        del message
        self.input_allowed = allowed
        self._update_enabled()

    def set_right_click_paste(self, enabled: bool) -> None:
        self.console.right_click_paste = bool(enabled)

    def set_completion_shortcut(self, shortcut: str) -> None:
        self.console.set_completion_shortcut(shortcut)

    def _update_enabled(self) -> None:
        ready = bool(
            self.session and self.channel and not self.channel.closed and self.input_allowed
        )
        self.console.set_input_enabled(ready)
        self.sudo_button.setEnabled(ready)
        self.favorite_button.setEnabled(bool(self.session))
        self.path_button.setEnabled(bool(self.session))
        self.recall_button.setEnabled(bool(ready and self._command_history))

    def send_sudo_password(self) -> None:
        if not self.session or not self.channel or self.channel.closed:
            self._write_system(">>> Aucun terminal connecté.\r\n")
            return
        password = self.session.sudo_password
        if not password:
            self.sudo_password_requested.emit(self)
            return
        self.inject_sudo_password(password)

    def inject_sudo_password(self, password: str) -> None:
        if not self.channel or self.channel.closed:
            self._write_system(">>> Aucun terminal connecté.\r\n")
            return
        safe_password = str(password).replace("\r", "").replace("\n", "")
        if not safe_password:
            return
        try:
            self.channel.sendall((safe_password + "\n").encode("utf-8"))
            self.console.setFocus()
        except Exception as exc:
            self._write_system(
                f">>> Envoi manuel du mot de passe sudo impossible : {exc}\r\n"
            )

    def send_command(self, command: str, follow_tree: bool = False) -> None:
        self.completion.cancel()
        if not self.channel or self.channel.closed:
            self._write_system(">>> Aucun terminal connecté.\r\n")
            return
        try:
            if self.tracker.at_prompt and (self.tracker.line or not self.tracker.reliable):
                self.channel.send("\x15")
            self.channel.send(command + "\n")
            self.tracker.submitted(command)
            self._follow_tree_pending.append(bool(follow_tree))
            self.record_command_history(command)
            self.command_sent.emit(command)
        except Exception as exc:
            self._write_system(f">>> Erreur d'envoi : {exc}\r\n")

    def set_current_path(self, path: str) -> None:
        self.current_path = path or "~"
        self.path_edit.setText(self.current_path)
        self.path_changed.emit(self.current_path)
        self._venv_path_changed()

    def _write_system(self, text: str) -> None:
        self.console.write_system(tr(text))

    def append_system(self, text: str) -> None:
        self._write_system(text)

    def redraw_prompt(self) -> None:
        if not self.channel or self.channel.closed:
            return
        try:
            self.channel.sendall(b"\n")
        except Exception as exc:
            self._write_system(
                f"\r\n>>> Réaffichage du prompt impossible : {exc}\r\n"
            )

    def set_environment(
        self, name: str, family: str, package_manager: str = ""
    ) -> None:
        self._environment_name = name or "Linux"
        self.package_managers = {"pip"}
        manager = str(package_manager or "").lower()
        if manager in {"apt", "dnf", "yum"}:
            self.package_managers.add(manager)
        elif family == "ubuntu":
            self.package_managers.add("apt")
        elif family == "redhat":
            self.package_managers.add("dnf")
        self._refresh_environment_badge()

    def _command_finished(self, code: int) -> None:
        self.completion.cancel()
        completed = self.tracker.prompt(code)
        if not completed:
            return
        command, exit_code = completed
        follow_tree = self._follow_tree_pending.pop(0) if self._follow_tree_pending else False
        if exit_code != 0:
            self.notifications.add_failure(
                command, exit_code, interrupted=exit_code in {130, 143}
            )
            return
        if is_cd_command(command):
            QTimer.singleShot(
                0,
                lambda cmd=command, forced=follow_tree: self.cd_completed.emit(
                    cmd, self.current_path, forced
                ),
            )

    @staticmethod
    def _shell_setup_command() -> str:
        return (
            "if [ -n \"${BASH_VERSION:-}\" ]; then "
            "__rl_old_pc=\"$PROMPT_COMMAND\"; "
            "__rl_prompt(){ local r=$?; "
            "printf '\\033]777;ANDALA_VENV=%s\\007' \"${VIRTUAL_ENV:-}\"; "
            "printf '\\033]777;ANDALA_CWD=%s\\007' \"$PWD\"; "
            "printf '\\033]777;REMOTE_LINUX_STATUS=%s\\007' \"$r\"; "
            "[ -z \"$__rl_old_pc\" ] || eval \"$__rl_old_pc\"; return $r; }; "
            "PROMPT_COMMAND=__rl_prompt; "
            "elif [ -n \"${ZSH_VERSION:-}\" ]; then autoload -Uz add-zsh-hook; "
            "__rl_prompt(){ local r=$?; "
            "printf '\\033]777;ANDALA_VENV=%s\\007' \"${VIRTUAL_ENV:-}\"; "
            "printf '\\033]777;ANDALA_CWD=%s\\007' \"$PWD\"; "
            "printf '\\033]777;REMOTE_LINUX_STATUS=%s\\007' \"$r\"; }; "
            "add-zsh-hook precmd __rl_prompt; fi"
        )
