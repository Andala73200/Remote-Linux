from __future__ import annotations

from PySide6.QtCore import QObject
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QStyle, QSystemTrayIcon


class WindowsNotifier(QObject):
    """Native notifications shown only when the window is not in the foreground."""

    def __init__(self, window, enabled: bool = True):
        super().__init__(window)
        self.window = window
        self.enabled = False
        self.available = QSystemTrayIcon.isSystemTrayAvailable()
        self.tray = QSystemTrayIcon(window)
        icon = window.windowIcon()
        if icon.isNull():
            icon = window.style().standardIcon(QStyle.SP_ComputerIcon)
        self.tray.setIcon(icon)
        self.tray.setToolTip("Remote Linux")

        menu = QMenu(window)
        show_action = QAction("Afficher Remote Linux", menu)
        show_action.triggered.connect(self.restore_window)
        quit_action = QAction("Quitter", menu)
        quit_action.triggered.connect(window.close)
        menu.addAction(show_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._activated)
        self.set_enabled(enabled)

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled and self.available)
        self.tray.setVisible(self.enabled)

    def notify(self, title: str, message: str) -> None:
        if not self.enabled:
            return
        if self.window.isActiveWindow() and not self.window.isMinimized():
            return
        self.tray.showMessage(
            title,
            message,
            QSystemTrayIcon.MessageIcon.Information,
            7000,
        )

    def restore_window(self) -> None:
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()

    def close(self) -> None:
        self.tray.hide()

    def _activated(self, reason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.restore_window()
