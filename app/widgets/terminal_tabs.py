from PySide6.QtCore import Signal
from PySide6.QtWidgets import QTabBar, QTabWidget, QVBoxLayout, QWidget

from app.core.session import RemoteSession
from app.core.terminal_shortcut import DEFAULT_COMPLETION_SHORTCUT
from app.widgets.terminal_widget import TerminalWidget


class TerminalTabs(QWidget):
    command_sent = Signal(str)
    favorites_requested = Signal(object)
    path_requested = Signal(str)
    sudo_password_requested = Signal(object)
    package_manager_requested = Signal(str, object)
    venv_activation_requested = Signal(object, str)
    cd_completed = Signal(str, str, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.session: RemoteSession | None = None
        self.input_allowed = False
        self.right_click_paste = False
        self.venv_prompt_enabled = True
        self.completion_shortcut = DEFAULT_COMPLETION_SHORTCUT
        self.environment_name = "Linux —"
        self.environment_family = "other"
        self.environment_package_manager = ""
        self._building = True
        self.tabs = QTabWidget()
        self.tabs.setProperty("rl_i18n_skip_tabs", True)
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self.close_terminal)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.plus_page = QWidget()
        self.tabs.addTab(self.plus_page, "+")
        self._hide_plus_close()
        self._building = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.tabs)
        self.add_terminal()

    def _plus_index(self) -> int:
        return self.tabs.indexOf(self.plus_page)

    def _hide_plus_close(self) -> None:
        index = self._plus_index()
        if index >= 0:
            self.tabs.tabBar().setTabButton(index, QTabBar.LeftSide, None)
            self.tabs.tabBar().setTabButton(index, QTabBar.RightSide, None)

    def terminals(self) -> list[TerminalWidget]:
        return [self.tabs.widget(i) for i in range(self.tabs.count()) if self.tabs.widget(i) is not self.plus_page]

    def add_terminal(self) -> TerminalWidget:
        terminal = TerminalWidget()
        terminal.command_sent.connect(self.command_sent)
        terminal.favorites_requested.connect(self.favorites_requested)
        terminal.path_requested.connect(self.path_requested)
        terminal.sudo_password_requested.connect(self.sudo_password_requested)
        terminal.package_manager_requested.connect(self.package_manager_requested)
        terminal.venv_activation_requested.connect(self.venv_activation_requested)
        terminal.cd_completed.connect(self.cd_completed)
        index = self.tabs.insertTab(self._plus_index(), terminal, f"Terminal {len(self.terminals()) + 1}")
        self.tabs.setCurrentIndex(index)
        self._hide_plus_close()
        terminal.set_right_click_paste(self.right_click_paste)
        terminal.set_venv_prompt_enabled(self.venv_prompt_enabled)
        terminal.set_completion_shortcut(self.completion_shortcut)
        terminal.set_environment(
            self.environment_name, self.environment_family,
            self.environment_package_manager,
        )
        if self.session:
            terminal.attach_session(self.session)
            terminal.set_input_allowed(self.input_allowed)
        return terminal

    def _tab_changed(self, index: int) -> None:
        if not self._building and index == self._plus_index():
            self.add_terminal()

    def close_terminal(self, index: int) -> None:
        terminal = self.tabs.widget(index)
        terminals = self.terminals()
        if terminal is self.plus_page or terminal not in terminals or len(terminals) == 1:
            return
        current = self.tabs.currentWidget()
        closing_position = terminals.index(terminal)
        replacement = None
        if current is terminal:
            replacement = (
                terminals[closing_position + 1]
                if closing_position + 1 < len(terminals)
                else terminals[closing_position - 1]
            )
        terminal.detach_session()
        self.tabs.removeTab(index)
        terminal.deleteLater()
        self._rename_tabs()
        self._hide_plus_close()
        if replacement is not None:
            self.tabs.setCurrentWidget(replacement)
            replacement.console.setFocus()
        elif current in self.terminals():
            self.tabs.setCurrentWidget(current)

    def _rename_tabs(self) -> None:
        for index, terminal in enumerate(self.terminals(), start=1):
            self.tabs.setTabText(self.tabs.indexOf(terminal), f"Terminal {index}")

    def set_session(self, session: RemoteSession | None, reconnect: bool = False) -> None:
        self.session = session
        for terminal in self.terminals():
            terminal.detach_session()
            if session:
                terminal.attach_session(session, preserve_output=True)
                if reconnect:
                    terminal.append_system(">>> Reconnexion automatique réussie.\n")
                terminal.set_input_allowed(self.input_allowed)

    def set_input_allowed(self, allowed: bool, message: str = "") -> None:
        self.input_allowed = allowed
        for terminal in self.terminals():
            terminal.set_input_allowed(allowed, message)

    def apply_preferences(self, settings: dict[str, object]) -> None:
        self.right_click_paste = bool(settings.get("terminal_right_click_paste", False))
        self.venv_prompt_enabled = bool(
            settings.get("terminal_venv_prompt_enabled", True)
        )
        self.completion_shortcut = str(
            settings.get("terminal_completion_shortcut", DEFAULT_COMPLETION_SHORTCUT)
            or DEFAULT_COMPLETION_SHORTCUT
        )
        for terminal in self.terminals():
            terminal.set_right_click_paste(self.right_click_paste)
            terminal.set_venv_prompt_enabled(self.venv_prompt_enabled)
            terminal.set_completion_shortcut(self.completion_shortcut)

    def set_environment(
        self, name: str, family: str, package_manager: str = ""
    ) -> None:
        self.environment_name = name or "Linux"
        self.environment_family = family or "other"
        self.environment_package_manager = str(package_manager or "").lower()
        for terminal in self.terminals():
            terminal.set_environment(
                self.environment_name, self.environment_family,
                self.environment_package_manager,
            )

    def active_terminal(self) -> TerminalWidget:
        widget = self.tabs.currentWidget()
        return self.terminals()[-1] if widget is self.plus_page else widget

    def send_command(self, command: str, follow_tree: bool = False) -> None:
        self.active_terminal().send_command(command, follow_tree=follow_tree)

    def append_system(self, text: str) -> None:
        self.active_terminal().append_system(text)

    def redraw_prompt(self) -> None:
        self.active_terminal().redraw_prompt()

    def current_command(self) -> str:
        return self.active_terminal().last_command

    def active_title(self) -> str:
        terminal = self.active_terminal()
        return self.tabs.tabText(self.tabs.indexOf(terminal))

    def rename_active(self, title: str) -> None:
        terminal = self.active_terminal()
        self.tabs.setTabText(self.tabs.indexOf(terminal), title)

    def current_path(self) -> str:
        return self.active_terminal().current_path
