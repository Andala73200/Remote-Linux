from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
    QPlainTextEdit, QPushButton, QScrollArea, QTableWidget, QTableWidgetItem,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from app.i18n import byte_units, ntr, tr
from app.widgets.mini_chart import MiniChart


class MetricCard(QGroupBox):
    clicked = Signal()

    def __init__(self, title: str, parent=None):
        super().__init__(title, parent)
        self.value_label = QLabel("—")
        self.value_label.setStyleSheet("font-size: 17pt; font-weight: bold;")
        self.detail_label = QLabel("")
        self.detail_label.setWordWrap(True)
        self.chart = MiniChart()
        layout = QVBoxLayout(self)
        layout.addWidget(self.value_label)
        layout.addWidget(self.detail_label)
        layout.addWidget(self.chart)

    def set_metric(self, value: str, detail: str, history: list[float], maximum: float | None = None) -> None:
        self.value_label.setText(value)
        self.detail_label.setText(detail)
        self.chart.set_values(history, maximum)

    def mousePressEvent(self, event) -> None:
        self.clicked.emit()
        super().mousePressEvent(event)


class CoreCard(QGroupBox):
    def __init__(self, name: str, parent=None):
        super().__init__(name, parent)
        self.value = QLabel("—")
        self.value.setStyleSheet("font-size: 13pt; font-weight: bold;")
        self.frequency = QLabel("")
        self.chart = MiniChart()
        self.chart.setMinimumHeight(55)
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(self.value)
        row.addStretch(1)
        row.addWidget(self.frequency)
        layout.addLayout(row)
        layout.addWidget(self.chart)

    def update_value(self, percent: float, frequency: float, history: list[float]) -> None:
        self.value.setText(f"{percent:.1f} %")
        self.frequency.setText(f"{frequency / 1_000_000:.0f} MHz" if frequency else "")
        self.chart.set_values(history, 100)


class CpuPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.model_info = QLabel("")
        self.model_info.setWordWrap(True)
        self.model_info.setStyleSheet("font-weight: bold;")
        self.summary = QLabel("—")
        self.summary.setWordWrap(True)
        self.cards: dict[str, CoreCard] = {}
        self.grid_widget = QWidget()
        self.grid = QGridLayout(self.grid_widget)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.grid_widget)
        self.processes = QTableWidget(0, 5)
        self.processes.setHorizontalHeaderLabels(["PID", "Utilisateur", "Processus", "CPU %", "RAM %"])
        self.processes.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.processes.horizontalHeader().setStretchLastSection(True)
        layout = QVBoxLayout(self)
        layout.addWidget(self.model_info)
        layout.addWidget(self.summary)
        layout.addWidget(scroll, 2)
        layout.addWidget(QLabel("Processus consommant le plus de CPU"))
        layout.addWidget(self.processes, 1)

    def set_cpu_info(self, text: str) -> None:
        wanted = ("Model name:", "Architecture:", "CPU(s):", "Core(s) per socket:", "Thread(s) per core:", "Socket(s):", "CPU max MHz:", "CPU min MHz:")
        lines = [line.strip() for line in text.splitlines() if line.strip().startswith(wanted)]
        self.model_info.setText("   •   ".join(lines[:8]))

    def update_cores(self, values: dict[str, float], freqs: dict[str, int], histories: dict[str, list[float]], load: list[float]) -> None:
        names = sorted(values, key=lambda value: int(value[3:]) if value[3:].isdigit() else 9999)
        if set(names) != set(self.cards):
            while self.grid.count():
                item = self.grid.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            self.cards.clear()
            for index, name in enumerate(names):
                card = CoreCard(name.upper())
                self.cards[name] = card
                self.grid.addWidget(card, index // 3, index % 3)
        core_label = ntr(len(names), 'ui.logical_core', 'ui.logical_cores')
        self.summary.setText(
            f"{len(names)} {core_label}  •  {tr('ui.load')} "
            f"{load[0]:.2f} / {load[1]:.2f} / {load[2]:.2f}"
        )
        for name in names:
            self.cards[name].update_value(values[name], freqs.get(name, 0), histories.get(name, []))

    def update_processes(self, rows: list[dict[str, str]]) -> None:
        self.processes.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for column, key in enumerate(("pid", "user", "name", "cpu", "mem")):
                self.processes.setItem(row_index, column, QTableWidgetItem(str(row.get(key, ""))))
        self.processes.resizeColumnsToContents()


class MemoryPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.card = MetricCard("Mémoire et swap")
        self.processes = QTableWidget(0, 5)
        self.processes.setHorizontalHeaderLabels(["PID", "Utilisateur", "Processus", "CPU %", "RAM %"])
        self.processes.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.processes.horizontalHeader().setStretchLastSection(True)
        layout = QVBoxLayout(self)
        layout.addWidget(self.card)
        layout.addWidget(QLabel("Processus les plus gourmands"))
        layout.addWidget(self.processes, 1)

    def update_processes(self, rows: list[dict[str, str]]) -> None:
        self.processes.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for column, key in enumerate(("pid", "user", "name", "cpu", "mem")):
                self.processes.setItem(row_index, column, QTableWidgetItem(str(row.get(key, ""))))
        self.processes.resizeColumnsToContents()


class StoragePage(QWidget):
    analyze_requested = Signal(str)
    def __init__(self, parent=None):
        super().__init__(parent)
        self.mounts = QTableWidget(0, 6)
        self.mounts.setHorizontalHeaderLabels(["Périphérique", "Montage", "Total", "Utilisé", "Libre", "%"])
        self.mounts.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.mounts.horizontalHeader().setStretchLastSection(True)
        self.devices = QTreeWidget()
        self.devices.setHeaderLabels(["Périphérique", "Type", "Taille", "FS", "Montage", "Modèle / Liaison"])
        self.devices.header().setStretchLastSection(True)
        self.analyze_button = QPushButton("Analyser les plus gros éléments du volume sélectionné")
        self.analyze_button.clicked.connect(self._request_analysis)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Volumes montés"))
        layout.addWidget(self.mounts, 1)
        layout.addWidget(self.analyze_button)
        layout.addWidget(QLabel("Disques, partitions et périphériques rattachés"))
        layout.addWidget(self.devices, 1)

    def update_mounts(self, rows: list[dict], formatter) -> None:
        self.mounts.setRowCount(len(rows))
        for r, row in enumerate(rows):
            values = [row["device"], row["mount"], formatter(row["total"]), formatter(row["used"]), formatter(row["free"]), row["percent"]]
            for c, value in enumerate(values):
                self.mounts.setItem(r, c, QTableWidgetItem(str(value)))
        self.mounts.resizeColumnsToContents()

    def _request_analysis(self) -> None:
        row = self.mounts.currentRow()
        mount = self.mounts.item(row, 1).text() if row >= 0 and self.mounts.item(row, 1) else "/"
        self.analyze_requested.emit(mount)

    def set_inventory(self, payload: dict) -> None:
        self.devices.clear()
        for device in payload.get("blockdevices", []) or []:
            self._add_device(None, device)
        self.devices.expandAll()
        for column in range(5):
            self.devices.resizeColumnToContents(column)

    def _add_device(self, parent: QTreeWidgetItem | None, device: dict) -> None:
        mounts = device.get("mountpoints") or []
        if isinstance(mounts, str):
            mounts = [mounts]
        extra = " ".join(str(device.get(key) or "") for key in ("vendor", "model", "tran")).strip()
        values = [
            str(device.get("name", "")), str(device.get("type", "")), self._size(int(device.get("size") or 0)),
            str(device.get("fstype") or ""), ", ".join(value for value in mounts if value), extra,
        ]
        item = QTreeWidgetItem(values)
        parent.addChild(item) if parent else self.devices.addTopLevelItem(item)
        for child in device.get("children", []) or []:
            self._add_device(item, child)

    @staticmethod
    def _size(value: float) -> str:
        for unit in byte_units():
            if value < 1024 or unit in {"To", "TB"}:
                return f"{value:.1f} {unit}"
            value /= 1024
        return f"0 {byte_units()[0]}"


class NetworkPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.interfaces = QTableWidget(0, 6)
        self.interfaces.setHorizontalHeaderLabels(["Interface", "Réception", "Envoi", "Total reçu", "Total envoyé", "Adresses"])
        self.interfaces.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.interfaces.horizontalHeader().setStretchLastSection(True)
        layout = QVBoxLayout(self)
        layout.addWidget(self.interfaces)

    def update_interfaces(self, rows: list[dict[str, str]]) -> None:
        self.interfaces.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, key in enumerate(("name", "rx_rate", "tx_rate", "rx", "tx", "address")):
                self.interfaces.setItem(r, c, QTableWidgetItem(row.get(key, "")))
        self.interfaces.resizeColumnsToContents()
        self.interfaces.horizontalHeader().setStretchLastSection(True)


class SensorsPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.cards: dict[str, MetricCard] = {}
        self.grid_widget = QWidget()
        self.grid = QGridLayout(self.grid_widget)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.grid_widget)
        layout = QVBoxLayout(self)
        layout.addWidget(scroll)

    def update_sensors(self, sensors: list[dict], histories: dict[str, list[float]]) -> None:
        keys = [f"{row['chip']} — {row['label']}" for row in sensors]
        if set(keys) != set(self.cards):
            while self.grid.count():
                item = self.grid.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            self.cards.clear()
            for index, key in enumerate(keys):
                card = MetricCard(key)
                self.cards[key] = card
                self.grid.addWidget(card, index // 2, index % 2)
        for row in sensors:
            key = f"{row['chip']} — {row['label']}"
            value = float(row["value"])
            self.cards[key].set_metric(f"{value:.1f} °C", "Température actuelle", histories.get(key, []), 120)


class TextDetailsPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        layout = QVBoxLayout(self)
        layout.addWidget(self.text)

    def set_text(self, text: str) -> None:
        self.text.setPlainText(text or "Information non disponible sur ce serveur.")
