from __future__ import annotations

from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QTabWidget,
    QVBoxLayout, QWidget,
)

from app.core.async_task import run_async
from app.i18n import ntr, tr
from app.i18n import tr
from app.widgets.table_tools import Cell, FilteredTablePage, date_sort_value


class TasksWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.session = None
        self.status = QLabel("Déconnecté")
        self.status.setStyleSheet("font-size:12pt;font-weight:600;")
        self.status.setWordWrap(True)
        self.refresh_button = QPushButton("🔄 Actualiser")
        self.refresh_button.clicked.connect(self.refresh)
        top = QHBoxLayout()
        top.addWidget(self.status, 1)
        top.addWidget(self.refresh_button)
        self.pages = QTabWidget()
        self.timers = FilteredTablePage([
            "Timer", "État", "Dernière exécution", "Prochaine exécution",
            "Résultat", "Service déclenché",
        ], filters=False, default_sort=0)
        self.cron = FilteredTablePage([
            "Source", "Utilisateur", "Planification", "Dernière trace",
            "Prochaine exécution", "Résultat", "Commande",
        ], filters=False, default_sort=0)
        self.cron_log = QPlainTextEdit()
        self.cron_log.setReadOnly(True)
        self.pages.addTab(self.timers, "systemd timers")
        self.pages.addTab(self.cron, "cron")
        self.pages.addTab(self.cron_log, "Journaux cron récents")
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.pages, 1)
        self.refresh_button.setEnabled(False)

    def set_session(self, session) -> None:
        self.session = session
        self.timers.fill([])
        self.cron.fill([])
        self.cron_log.clear()
        self.refresh_button.setEnabled(bool(session))
        self.status.setText("Déconnecté" if not session else "Lecture des tâches planifiées…")
        if session:
            self.status.setText("Prêt — lecture de cron et des timers systemd")

    def refresh(self) -> None:
        if not self.session:
            return
        session = self.session
        self.refresh_button.setEnabled(False)
        self.status.setText("Lecture de cron et des timers systemd…")
        run_async(
            session.scheduled_tasks,
            self._ready,
            self._failed,
            guard=lambda: self.session is session,
        )

    def _ready(self, result: object) -> None:
        data = dict(result or {})
        timer_rows = list(data.get("timers") or [])
        cron_rows = list(data.get("cron") or [])
        self._fill_timers(timer_rows)
        self._fill_cron(cron_rows)
        self.cron_log.setPlainText(
            str(data.get("cron_log") or tr('ui.no_accessible_cron_log'))
        )
        privilege = "lecture complète" if data.get("privileged") else "lecture partielle"
        timer_label = ntr(len(timer_rows), 'ui.timer_35c66b', 'ui.timers')
        cron_label = ntr(len(cron_rows), 'ui.cron_task', 'ui.cron_tasks')
        self.status.setText(
            f"{len(timer_rows)} {timer_label}  •  {len(cron_rows)} {cron_label}  •  {tr(privilege)}"
        )
        self.refresh_button.setEnabled(True)

    def _fill_timers(self, rows: list[dict]) -> None:
        values = []
        for row in rows:
            values.append([
                row.get("name", ""),
                f"{row.get('active', '—')} / {row.get('sub', '—')}",
                Cell(row.get("last", "—"), date_sort_value(row.get("last", ""))),
                Cell(row.get("next", "—"), date_sort_value(row.get("next", ""))),
                Cell(row.get("result", "—"), translate=True),
                row.get("command", "—"),
            ])
        self.timers.fill(values)

    def _fill_cron(self, rows: list[dict]) -> None:
        values = []
        keys = ("name", "user", "schedule", "last", "next", "result", "command")
        for row in rows:
            line = []
            for key in keys:
                value = row.get(key, "")
                if key in {"last", "next"}:
                    line.append(Cell(value, date_sort_value(value)))
                elif key == "name":
                    line.append(Cell(value, translate=True))
                elif key == "result":
                    line.append(Cell(value, translate=True))
                else:
                    line.append(value)
            values.append(line)
        self.cron.fill(values)

    def _failed(self, text: str) -> None:
        self.refresh_button.setEnabled(bool(self.session))
        self.status.setText(f"{tr('ui.unable_to_read_scheduled_tasks')} {text}")
