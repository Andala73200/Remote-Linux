from __future__ import annotations

import datetime
import re

from PySide6.QtWidgets import QMenu, QToolButton


SECRET_PATTERNS = (
    re.compile(r"(?i)(password|passwd|token|secret|api[_-]?key)(\s*[=:]\s*)\S+"),
    re.compile(r"(?i)(--(?:password|token|secret|api-key)(?:=|\s+))\S+"),
)


class TerminalNotificationCenter(QToolButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.events: list[dict[str, str]] = []
        self.setToolTip("Erreurs du terminal actif")
        self.clicked.connect(self.show_events)
        self._update_text()

    def add_failure(self, command: str, code: int, interrupted: bool = False) -> None:
        clean = self._sanitize(command)
        self.events.append({
            "command": clean or "Commande non enregistrée",
            "code": str(code),
            "kind": "Interrompue" if interrupted else "Échec",
            "time": datetime.datetime.now().strftime("%H:%M:%S"),
        })
        del self.events[:-50]
        self._update_text()

    def clear(self) -> None:
        self.events.clear()
        self._update_text()

    def _update_text(self) -> None:
        self.setText(f"⚠ {len(self.events)}" if self.events else "⚠")
        self.setStyleSheet(
            "QToolButton { color: #ff7777; }" if self.events else
            "QToolButton { color: #7f8792; }"
        )

    def show_events(self) -> None:
        menu = QMenu(self)
        if not self.events:
            menu.addAction("Aucune erreur dans ce terminal").setEnabled(False)
        for event in reversed(self.events):
            action = menu.addAction(
                f"[{event['time']}] {event['kind']} — code {event['code']}"
            )
            action.setToolTip(event["command"])
            submenu = QMenu(menu)
            detail = submenu.addAction(event["command"])
            detail.setEnabled(False)
            action.setMenu(submenu)
        if self.events:
            menu.addSeparator()
            menu.addAction("Tout acquitter", self.clear)
        menu.exec(self.mapToGlobal(self.rect().bottomLeft()))

    @staticmethod
    def _sanitize(command: str) -> str:
        text = " ".join(str(command).split())[:500]
        for pattern in SECRET_PATTERNS:
            text = pattern.sub(lambda match: match.group(1) + "••••", text)
        return text
