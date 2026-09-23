from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QGridLayout, QGroupBox, QLabel,
    QVBoxLayout,
)

from app.i18n import byte_units, ntr, tr


class ServerHealthDialog(QDialog):
    def __init__(self, server_name: str, data: dict, alert_count: int = 0, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Santé du serveur — {server_name}")
        self.setMinimumWidth(720)
        title = QLabel(f"État de {server_name}")
        title.setStyleSheet("font-size:16pt;font-weight:700;")
        grid = QGridLayout()
        system = dict(data.get("system") or {})
        cpu = system.get("cpu_percent")
        load = (list(system.get("load") or []) + [0, 0, 0])[:3]
        grid.addWidget(self._card(
            "CPU",
            "—" if cpu is None else f"{float(cpu):.1f} %",
            f"{tr('ui.load')} {load[0]:.2f} / {load[1]:.2f} / {load[2]:.2f}",
            self._metric_color(cpu, 70, 90),
        ), 0, 0)
        memory = float(system.get("memory_percent") or 0)
        grid.addWidget(self._card(
            "Mémoire", f"{memory:.1f} %",
            f"{self._bytes(system.get('memory_used', 0))} / {self._bytes(system.get('memory_total', 0))}",
            self._metric_color(memory, 75, 90),
        ), 0, 1)
        disk = float(system.get("disk_percent") or 0)
        grid.addWidget(self._card(
            "Disque racine", f"{disk:.1f} {tr('fragment.used_8ee6f0')}",
            f"{tr('fragment.free')} {self._bytes(system.get('disk_free', 0))}",
            self._metric_color(disk, 75, 90),
        ), 1, 0)
        services = dict(data.get("services") or {})
        failed = list(services.get("failed") or [])
        services_accessible = bool(services.get("accessible"))
        service_value = (
            "Indisponible" if not services_accessible else
            "OK" if not failed else f"{len(failed)} {tr('fragment.failed')}"
        )
        service_detail = (
            "systemd non accessible" if not services_accessible else
            ", ".join(failed[:4]) or "Aucun service en échec"
        )
        service_color = (
            "#f2c866" if not services_accessible else
            "#55d187" if not failed else "#ff6666"
        )
        grid.addWidget(self._card(
            "Services", service_value, service_detail, service_color
        ), 1, 1)
        backups = dict(data.get("backups") or {})
        backup_value, backup_detail, backup_color = self._backup_text(backups)
        grid.addWidget(self._card(
            "Sauvegardes", backup_value, backup_detail, backup_color
        ), 2, 0)
        smart = dict(data.get("smart") or {})
        devices = list(smart.get("devices") or [])
        if not smart.get("installed"):
            smart_value, smart_detail, smart_color = "Non surveillé", "smartmontools absent", "#f2c866"
        elif not devices:
            smart_value, smart_detail, smart_color = "Indisponible", "Aucun disque lisible", "#f2c866"
        else:
            failed_disks = sum(row.get("passed") is False for row in devices)
            known_disks = sum(row.get("passed") is not None for row in devices)
            smart_value = (
                f"{failed_disks} {tr('fragment.failed')}" if failed_disks else
                "Inconnu" if not known_disks else "OK"
            )
            smart_detail = f"{len(devices)} {ntr(len(devices), 'ui.disk_analyzed', 'ui.disks_analyzed')}"
            smart_color = (
                "#ff6666" if failed_disks else "#f2c866" if not known_disks else "#55d187"
            )
        grid.addWidget(self._card(
            "Disques SMART", smart_value, smart_detail, smart_color
        ), 2, 1)
        self.alerts = QLabel(
            tr('ui.no_active_global_alerts')
            if not alert_count else
            f"{alert_count} {ntr(alert_count, 'ui.active_global_alert', 'ui.active_global_alerts')}."
        )
        self.alerts.setStyleSheet(
            "color:#55d187;" if not alert_count else "color:#f2c866;font-weight:600;"
        )
        self.disable = QCheckBox("Ne plus afficher pour ce serveur")
        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        buttons.button(QDialogButtonBox.Ok).setText("Fermer")
        buttons.accepted.connect(self.accept)
        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addLayout(grid)
        layout.addWidget(self.alerts)
        layout.addWidget(self.disable)
        layout.addWidget(buttons)

    @staticmethod
    def _card(title: str, value: str, detail: str, color: str) -> QGroupBox:
        box = QGroupBox(title)
        value_label = QLabel(value)
        value_label.setStyleSheet(f"font-size:15pt;font-weight:700;color:{color};")
        detail_label = QLabel(detail)
        detail_label.setWordWrap(True)
        layout = QVBoxLayout(box)
        layout.addWidget(value_label)
        layout.addWidget(detail_label)
        return box

    @staticmethod
    def _backup_text(data: dict) -> tuple[str, str, str]:
        if not data.get("installed"):
            return "Non surveillé", "pgBackRest absent", "#f2c866"
        if not data.get("configured"):
            return "À configurer", str(data.get("error") or "Stanza inaccessible"), "#f2c866"
        if data.get("healthy"):
            last = dict(data.get("last") or {})
            return "OK", f"Dernière : {last.get('date', '—')}", "#55d187"
        return "Erreur", str(data.get("errors") or data.get("error") or "À vérifier"), "#ff6666"

    @staticmethod
    def _metric_color(value, warning: float, critical: float) -> str:
        if value is None:
            return "#8d96a3"
        if float(value) >= critical:
            return "#ff6666"
        if float(value) >= warning:
            return "#f2c866"
        return "#55d187"

    @staticmethod
    def _bytes(value: object) -> str:
        number = float(value or 0)
        for unit in byte_units():
            if number < 1024 or unit in {"To", "TB"}:
                return f"{number:.1f} {unit}"
            number /= 1024
        return f"0 {byte_units()[0]}"
