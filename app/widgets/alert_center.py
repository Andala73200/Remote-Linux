import datetime

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QMenu, QToolButton


class AlertCenter(QToolButton):
    alert_added = Signal(str, str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.alerts: dict[str, dict[str, str]] = {}
        self.setText("🔔")
        self.setToolTip("Centre d’alertes")
        self.clicked.connect(self.show_alerts)

    def add_alert(self, key: str, title: str, message: str) -> None:
        previous = self.alerts.get(key)
        changed = not previous or previous.get("title") != title or previous.get("message") != message
        self.alerts[key] = {
            "title": title,
            "message": message,
            "time": datetime.datetime.now().strftime("%H:%M:%S"),
        }
        self._update_text()
        if changed:
            self.alert_added.emit(key, title, message)

    def remove_alert(self, key: str) -> None:
        self.alerts.pop(key, None)
        self._update_text()

    def clear(self) -> None:
        self.alerts.clear()
        self._update_text()

    def _update_text(self) -> None:
        self.setText(f"🔔 {len(self.alerts)}" if self.alerts else "🔔")

    def show_alerts(self) -> None:
        menu = QMenu(self)
        if not self.alerts:
            menu.addAction("Aucune alerte").setEnabled(False)
        for key, alert in reversed(list(self.alerts.items())):
            action = menu.addAction(f"[{alert['time']}] {alert['title']}")
            action.setToolTip(alert["message"])
            submenu = QMenu(menu)
            detail = submenu.addAction(alert["message"])
            detail.setEnabled(False)
            submenu.addAction(
                "Acquitter",
                lambda _checked=False, value=key: self.remove_alert(value),
            )
            action.setMenu(submenu)
        if self.alerts:
            menu.addSeparator()
            menu.addAction("Tout acquitter", self.clear)
        menu.exec(self.mapToGlobal(self.rect().bottomLeft()))
