from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QTabWidget, QVBoxLayout, QWidget,
)

from app.i18n import ntr, tr
from app.widgets.storage_cards import DiskCard, VolumeCard, human_size
from app.widgets.system_pages import TextDetailsPage
from app.widgets.smart_health_panel import SmartHealthPanel


class StoragePage(QWidget):
    analyze_requested = Signal(str)
    action_requested = Signal(str, dict)
    refresh_requested = Signal()
    smart_refresh_requested = Signal()
    smart_health_changed = Signal(str, str)

    TECHNICAL_FS = {"tmpfs", "devtmpfs", "squashfs", "efivarfs", "proc", "sysfs", "cgroup2", "overlay"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.mount_rows: list[dict] = []
        self.inventory: dict = {}
        self.summary = QLabel("Aucune donnée de stockage")
        self.summary.setStyleSheet("font-size:11pt;font-weight:600;")
        self.show_technical = QCheckBox("Afficher les volumes techniques")
        self.show_technical.toggled.connect(self._rebuild_volumes)
        self.show_technical.toggled.connect(self._rebuild_disks)
        refresh = QPushButton("🔄  Actualiser")
        refresh.clicked.connect(self.refresh_requested)
        top = QHBoxLayout()
        top.addWidget(self.summary, 1)
        top.addWidget(self.show_technical)
        top.addWidget(refresh)
        self.tabs = QTabWidget()
        self.volumes_page = QWidget()
        self.volume_grid = QGridLayout(self.volumes_page)
        self.volume_grid.setAlignment(self.volume_grid.alignment())
        volume_scroll = QScrollArea()
        volume_scroll.setWidgetResizable(True)
        volume_scroll.setWidget(self.volumes_page)
        self.disks_page = QWidget()
        self.disks_layout = QVBoxLayout(self.disks_page)
        self.disks_layout.addStretch(1)
        disk_scroll = QScrollArea()
        disk_scroll.setWidgetResizable(True)
        disk_scroll.setWidget(self.disks_page)
        self.analysis_page = TextDetailsPage()
        self.analysis_page.set_text("Sélectionne un volume puis utilise « Analyser ».")
        self.advanced_page = TextDetailsPage()
        self.smart_page = SmartHealthPanel()
        self.smart_page.refresh_requested.connect(self.smart_refresh_requested)
        self.smart_page.health_changed.connect(self.smart_health_changed)
        self.tabs.addTab(volume_scroll, "📊  Vue rapide")
        self.tabs.addTab(disk_scroll, "💽  Disques physiques")
        self.tabs.addTab(self.analysis_page, "🔎  Analyse")
        self.tabs.addTab(self.smart_page, "❤  Santé SMART")
        self.tabs.addTab(self.advanced_page, "⚙  Détails avancés")
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.tabs, 1)

    def update_mounts(self, rows: list[dict], _formatter=None) -> None:
        self.mount_rows = [dict(row) for row in rows]
        useful = [row for row in self.mount_rows if self._is_useful(row)]
        total = sum(int(row.get("total") or 0) for row in useful)
        used = sum(int(row.get("used") or 0) for row in useful)
        free = sum(int(row.get("free") or 0) for row in useful)
        volume_label = ntr(len(useful), 'ui.useful_volume', 'ui.useful_volumes')
        self.summary.setText(
            f"{len(useful)} {volume_label}   •   {human_size(total)} {tr('ui.total_c8d2e4')}   •   "
            f"{human_size(used)} {tr('ui.used_69e1d5')}   •   {human_size(free)} {tr('ui.free_0ce019')}"
        )
        self._rebuild_volumes()
        self._update_advanced()

    def set_inventory(self, payload: dict) -> None:
        self.inventory = dict(payload or {})
        self._rebuild_disks()
        self._update_advanced()

    def show_analysis(self, text: str) -> None:
        self.analysis_page.set_text(text)
        self.tabs.setCurrentWidget(self.analysis_page)

    def set_show_technical(self, enabled: bool) -> None:
        self.show_technical.setChecked(enabled)

    def _rebuild_volumes(self) -> None:
        self._clear_layout(self.volume_grid)
        rows = self.mount_rows if self.show_technical.isChecked() else [row for row in self.mount_rows if self._is_useful(row)]
        rows = sorted(rows, key=lambda row: (str(row.get("mount")) != "/", str(row.get("mount", "")).lower()))
        if not rows:
            self.volume_grid.addWidget(QLabel("Aucun volume à afficher."), 0, 0)
            return
        for index, row in enumerate(rows):
            card = VolumeCard(row)
            card.analyze_requested.connect(self.analyze_requested)
            self.volume_grid.addWidget(card, index // 2, index % 2)
        self.volume_grid.setColumnStretch(0, 1)
        self.volume_grid.setColumnStretch(1, 1)

    def _rebuild_disks(self) -> None:
        self._clear_layout(self.disks_layout)
        devices = list((self.inventory.get("blockdevices") or []))
        physical = [row for row in devices if str(row.get("type")) == "disk"]
        if not self.show_technical.isChecked():
            physical = [row for row in physical if not str(row.get("name", "")).startswith(("loop", "ram"))]
        if not physical:
            self.disks_layout.addWidget(QLabel("Aucun disque physique détecté."))
        for device in physical:
            card = DiskCard(device)
            card.action_requested.connect(self.action_requested)
            self.disks_layout.addWidget(card)
        self.disks_layout.addStretch(1)

    def _update_advanced(self) -> None:
        lines = ["VOLUMES MONTÉS"]
        for row in self.mount_rows:
            lines.append(
                f"{row.get('device', ''):<24} {row.get('mount', ''):<30} "
                f"{row.get('percent', ''):>5}  {row.get('fstype', '')}"
            )
        lines.append("\nINVENTAIRE LSBLK BRUT\n")
        lines.append(str(self.inventory))
        self.advanced_page.set_text("\n".join(lines))

    @classmethod
    def _is_useful(cls, row: dict) -> bool:
        fs = str(row.get("fstype") or "").lower()
        device = str(row.get("device") or "")
        mount = str(row.get("mount") or "")
        if fs in cls.TECHNICAL_FS or device.startswith(("tmpfs", "udev", "overlay")):
            return False
        return device.startswith("/dev/") or mount in {"/", "/home", "/boot", "/boot/efi"} or mount.startswith(("/mnt/", "/media/"))

    @staticmethod
    def _clear_layout(layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            child = item.layout()
            if widget:
                widget.deleteLater()
            elif child:
                StoragePage._clear_layout(child)
