import time
from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QComboBox, QGridLayout, QHBoxLayout, QLabel, QPushButton, QTabWidget, QVBoxLayout, QWidget
from app.core.async_task import run_async
from app.core.session import RemoteSession
from app.i18n import byte_units, ntr, tr
from app.widgets.gpu_page import GpuPage
from app.widgets.system_pages import (
    CpuPage, MemoryPage, MetricCard, NetworkPage, SensorsPage, TextDetailsPage,
)
from app.widgets.storage_page import StoragePage
class SystemWidget(QWidget):
    alert_raised = Signal(str, str, str)
    alert_cleared = Signal(str)
    output_requested = Signal(str, str)
    storage_action_requested = Signal(str, dict)
    updates_requested = Signal()
    metrics_updated = Signal(object)
    def __init__(self, settings: dict[str, object], parent=None):
        super().__init__(parent)
        self.settings = settings
        self.session: RemoteSession | None = None
        self.previous: dict[str, object] | None = None
        self.previous_time = 0.0
        self.busy = False
        self.inventory_loaded = False
        self.addresses: dict[str, str] = {}
        self.alerted: set[str] = set()
        self.histories = {name: [] for name in ("cpu", "memory", "disk", "network", "temperature")}
        self.core_histories: dict[str, list[float]] = {}
        self.sensor_histories: dict[str, list[float]] = {}
        self.gpu_histories: dict[str, list[float]] = {}
        self.summary = QLabel("Déconnecté")
        self.update_label = QLabel("Mises à jour : —")
        self.update_button = QPushButton("Voir / installer")
        self.update_button.setToolTip("Voir les paquets disponibles puis installer la sélection")
        self.update_button.clicked.connect(self.updates_requested)
        self.update_button.hide()
        self.refresh_combo = QComboBox()
        for seconds in (1, 2, 5, 10, 30):
            self.refresh_combo.addItem(f"{seconds} s", seconds)
        self.refresh_combo.setCurrentIndex(1)
        self.refresh_combo.currentIndexChanged.connect(self._restart_timer)
        self.pause_button = QPushButton("Pause")
        self.pause_button.setCheckable(True)
        self.pause_button.toggled.connect(self._toggle_pause)
        top = QHBoxLayout()
        top.addWidget(self.summary, 1)
        top.addWidget(self.update_label)
        top.addWidget(self.update_button)
        top.addSpacing(16)
        top.addWidget(QLabel("Rafraîchissement :"))
        top.addWidget(self.refresh_combo)
        top.addWidget(self.pause_button)
        self.pages = QTabWidget()
        self.overview = QWidget()
        self.cards = {
            "cpu": MetricCard("CPU"), "memory": MetricCard("Mémoire"),
            "disk": MetricCard("Disque racine /"), "network": MetricCard("Réseau"),
            "temperature": MetricCard("Températures"),
        }
        overview_grid = QGridLayout(self.overview)
        overview_grid.addWidget(self.cards["cpu"], 0, 0)
        overview_grid.addWidget(self.cards["memory"], 0, 1)
        overview_grid.addWidget(self.cards["disk"], 1, 0)
        overview_grid.addWidget(self.cards["network"], 1, 1)
        overview_grid.addWidget(self.cards["temperature"], 2, 0, 1, 2)
        self.cpu_page = CpuPage()
        self.memory_page = MemoryPage()
        self.storage_page = StoragePage()
        self.storage_page.analyze_requested.connect(self._analyze_storage)
        self.storage_page.action_requested.connect(self.storage_action_requested)
        self.storage_page.refresh_requested.connect(self.refresh_inventory)
        self.storage_page.smart_refresh_requested.connect(self._refresh_smart)
        self.storage_page.smart_health_changed.connect(self._smart_health_changed)
        self.network_page = NetworkPage()
        self.sensors_page = SensorsPage()
        self.gpu_page = GpuPage()
        self.hardware_page = TextDetailsPage()
        for label, page in [
            ("Vue d’ensemble", self.overview), ("Processeur", self.cpu_page),
            ("Mémoire", self.memory_page), ("Stockage", self.storage_page),
            ("Réseau", self.network_page), ("Capteurs", self.sensors_page),
            ("GPU", self.gpu_page), ("Matériel", self.hardware_page),
        ]:
            self.pages.addTab(page, label)
        for key, index in {"cpu": 1, "memory": 2, "disk": 3, "network": 4, "temperature": 5}.items():
            self.cards[key].clicked.connect(lambda i=index: self.pages.setCurrentIndex(i))
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.pages, 1)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.apply_preferences()
        self._restart_timer()
    def apply_preferences(self) -> None:
        level = str(self.settings.get("system_level", "advanced"))
        for index in range(self.pages.count()):
            visible = index == 0 or level != "simple"
            if index == 7 and level != "expert":
                visible = False
            self.pages.setTabVisible(index, visible)
    def set_session(self, session: RemoteSession | None) -> None:
        self.session = session
        self.previous = None
        self.previous_time = 0.0
        self.inventory_loaded = False
        self.busy = False
        for key in list(self.alerted):
            self.alert_cleared.emit(key)
        self.alerted.clear()
        for history in self.histories.values():
            history.clear()
        self.core_histories.clear()
        self.sensor_histories.clear()
        self.gpu_histories.clear()
        self.summary.setText("Lecture des informations système…" if session else "Déconnecté")
        if session:
            self.refresh()
            run_async(
                session.system_inventory,
                self._inventory_ready,
                self._inventory_error,
                guard=lambda: self.session is session,
            )
        else:
            self._clear_display()
            self.metrics_updated.emit({})
    def set_update_count(self, count: int | None, error: str = "") -> None:
        if error:
            self.update_label.setText("Mises à jour : vérification impossible")
            self.update_button.hide()
        elif count is None:
            self.update_label.setText("Mises à jour : vérification…")
            self.update_button.hide()
        else:
            update_label = ntr(count, 'ui.available_update', 'ui.available_updates_e8cc9d')
            self.update_label.setText(f"{tr('ui.updates_f1e247')} {count} {update_label}")
            self.update_button.setText(f"{tr('ui.view_install')} ({count})")
            self.update_button.setVisible(count > 0)

    def _restart_timer(self) -> None:
        self.timer.start(int(self.refresh_combo.currentData() or 2) * 1000)

    def _toggle_pause(self, paused: bool) -> None:
        self.pause_button.setText("Reprendre" if paused else "Pause")
        self.timer.stop() if paused else self._restart_timer()

    def refresh(self) -> None:
        if not self.session or self.busy or self.pause_button.isChecked():
            return
        self.busy = True
        session = self.session
        run_async(
            session.system_snapshot,
            self._snapshot_ready,
            self._snapshot_error,
            guard=lambda: self.session is session,
        )

    def _snapshot_ready(self, snapshot: object) -> None:
        self.busy = False
        data = dict(snapshot)
        now = time.monotonic()
        cpu = self._cpu_percent(data)
        cores = self._core_percent(data)
        memory = self._memory_percent(data)
        disk = self._disk_percent(data)
        rx_rate, tx_rate = self._network_rates(data, now)
        sensors = list(data.get("sensors", []))
        gpus = list(data.get("gpus", []))
        gpu_usage = max((float(row.get("usage", 0)) for row in gpus), default=0.0)
        gpu_temps = [float(row.get("temperature", 0)) for row in gpus]
        temperature = max([float(row["value"]) for row in sensors] + gpu_temps, default=0.0)
        for name, value in (("cpu", cpu), ("memory", memory), ("disk", disk), ("network", (rx_rate + tx_rate) / 1024), ("temperature", temperature)):
            self._append(self.histories[name], value)
        for name, value in cores.items():
            self._append(self.core_histories.setdefault(name, []), value)
        for row in sensors:
            key = f"{row['chip']} — {row['label']}"
            self._append(self.sensor_histories.setdefault(key, []), float(row["value"]))
        for row in gpus:
            index = str(row.get("index", "0"))
            used = float(row.get("memory_used", 0))
            total = float(row.get("memory_total", 0))
            values = {
                f"gpu{index}:usage": float(row.get("usage", 0)),
                f"gpu{index}:temp": float(row.get("temperature", 0)),
                f"gpu{index}:mem": 0 if not total else used * 100 / total,
            }
            for key, value in values.items():
                self._append(self.gpu_histories.setdefault(key, []), value)
        load = list(data.get("load", [0, 0, 0]))
        uptime = self._duration(int(data.get("uptime", 0)))
        self.summary.setText(
            f"{tr('ui.uptime')} {uptime}    "
            f"{tr('ui.maximum_temperature')} {temperature:.1f} °C    "
            f"{tr('ui.load')} {load[0]:.2f} / {load[1]:.2f} / {load[2]:.2f}"
        )
        self.cards["cpu"].set_metric(f"{cpu:.1f} %", f"Charge : {load[0]:.2f} / {load[1]:.2f} / {load[2]:.2f}", self.histories["cpu"], 100)
        total, available, buffers, cached, swap_total, swap_free = data.get("mem", [0] * 6)
        used = max(0, total - available)
        detail = f"{self._bytes(used)} / {self._bytes(total)}  •  Disponible : {self._bytes(available)}  •  Cache : {self._bytes(cached)}"
        if swap_total:
            detail += f"  •  Swap : {self._bytes(swap_total - swap_free)} / {self._bytes(swap_total)}"
        self.cards["memory"].set_metric(f"{memory:.1f} %", detail, self.histories["memory"], 100)
        self.memory_page.card.set_metric(f"{memory:.1f} %", detail, self.histories["memory"], 100)
        self.memory_page.update_processes(list(data.get("mem_processes", [])))
        disk_total, disk_used, disk_free, _ = data.get("disk", [0, 0, 0, "0%"])
        self.cards["disk"].set_metric(f"{disk:.1f} %", f"{self._bytes(disk_used)} / {self._bytes(disk_total)}  •  Libre : {self._bytes(disk_free)}", self.histories["disk"], 100)
        net_max = max(self.histories["network"], default=1.0)
        self.cards["network"].set_metric(f"↓ {self._rate(rx_rate)}   ↑ {self._rate(tx_rate)}", "Débit actuel brut", self.histories["network"], net_max)
        sensor_detail = "  •  ".join(f"{row['label']} : {float(row['value']):.1f} °C" for row in sensors[:6]) or "Aucun capteur exposé"
        self.cards["temperature"].set_metric(
            f"{temperature:.1f} °C" if (sensors or gpus) else "—",
            sensor_detail,
            self.histories["temperature"],
            120,
        )
        self.cpu_page.update_cores(cores, dict(data.get("freqs", {})), self.core_histories, load)
        self.cpu_page.update_processes(list(data.get("processes", [])))
        self.storage_page.update_mounts(list(data.get("mounts", [])), self._bytes)
        self.network_page.update_interfaces(self._interface_rows(data, now))
        self.sensors_page.update_sensors(sensors, self.sensor_histories)
        self.gpu_page.update_gpus(gpus, self.gpu_histories, self._bytes)
        self._check_alerts(disk, temperature, list(data.get("mounts", [])))
        self.metrics_updated.emit({
            "cpu_percent": cpu,
            "memory_percent": memory,
            "memory_used": used,
            "memory_total": total,
            "gpu_percent": gpu_usage,
            "has_gpu": bool(gpus),
            "disk_percent": disk,
            "disk_used": disk_used,
            "disk_total": disk_total,
            "disk_free": disk_free,
            "rx_rate": rx_rate,
            "tx_rate": tx_rate,
            "temperature": temperature,
            "has_temperature": bool(sensors or gpus),
        })
        self.previous = data
        self.previous_time = now

    def _inventory_ready(self, inventory: object) -> None:
        data = dict(inventory)
        self.inventory_loaded = True
        self.storage_page.set_inventory(dict(data.get("lsblk_json", {})))
        self.cpu_page.set_cpu_info(str(data.get("cpu", "")))
        self.addresses = self._parse_addresses(str(data.get("network", "")))
        self.gpu_page.set_info(str(data.get("gpu", "")))
        hardware = "PROCESSEUR\n" + str(data.get("cpu", ""))
        hardware += "\n\nRÉSEAU\n" + str(data.get("network", ""))
        hardware += "\n\nPCI\n" + str(data.get("pci", ""))
        hardware += "\n\nUSB\n" + str(data.get("usb", ""))
        self.hardware_page.set_text(hardware)

    def _inventory_error(self, text: str) -> None:
        self.hardware_page.set_text(f"Inventaire indisponible : {text}")

    def _snapshot_error(self, text: str) -> None:
        self.busy = False
        self.summary.setText(f"{tr('ui.system_information_unavailable')} {text}")

    def _cpu_percent(self, data: dict[str, object]) -> float:
        if not self.previous:
            return 0.0
        total = int(data.get("cpu_total", 0)) - int(self.previous.get("cpu_total", 0))
        idle = int(data.get("cpu_idle", 0)) - int(self.previous.get("cpu_idle", 0))
        return 0.0 if total <= 0 else max(0.0, min(100.0, (total - idle) * 100 / total))

    def _core_percent(self, data: dict[str, object]) -> dict[str, float]:
        current = dict(data.get("cpus", {}))
        old = dict(self.previous.get("cpus", {})) if self.previous else {}
        result = {}
        for name, row in current.items():
            if name == "cpu" or name not in old:
                continue
            total = int(row["total"]) - int(old[name]["total"])
            idle = int(row["idle"]) - int(old[name]["idle"])
            result[name] = 0.0 if total <= 0 else max(0.0, min(100.0, (total - idle) * 100 / total))
        return result

    def _network_rates(self, data: dict[str, object], now: float) -> tuple[float, float]:
        if not self.previous or not self.previous_time:
            return 0.0, 0.0
        elapsed = max(0.001, now - self.previous_time)
        rx, tx = data.get("net", [0, 0])
        old_rx, old_tx = self.previous.get("net", [0, 0])
        return max(0, rx - old_rx) / elapsed, max(0, tx - old_tx) / elapsed

    def _interface_rows(self, data: dict[str, object], now: float) -> list[dict[str, str]]:
        elapsed = max(0.001, now - self.previous_time) if self.previous_time else 1.0
        old_nets = dict(self.previous.get("nets", {})) if self.previous else {}
        rows = []
        for name, values in sorted(dict(data.get("nets", {})).items()):
            old = old_nets.get(name, values)
            rows.append({"name": name, "rx_rate": self._rate(max(0, values[0] - old[0]) / elapsed), "tx_rate": self._rate(max(0, values[1] - old[1]) / elapsed), "rx": self._bytes(values[0]), "tx": self._bytes(values[1]), "address": self.addresses.get(name, "")})
        return rows

    @staticmethod
    def _parse_addresses(text: str) -> dict[str, str]:
        result = {}
        for line in text.splitlines():
            parts = line.split()
            if parts:
                result[parts[0]] = "  ".join(parts[2:]) if len(parts) > 2 else ""
        return result

    def refresh_inventory(self) -> None:
        if self.session:
            session = self.session
            run_async(
                session.system_inventory,
                self._inventory_ready,
                self._inventory_error,
                guard=lambda: self.session is session,
            )
            self.refresh()

    def show_storage_tab(self) -> None:
        self.pages.setCurrentWidget(self.storage_page)

    def _refresh_smart(self) -> None:
        if not self.session:
            return
        session = self.session
        self.storage_page.smart_page.set_loading(True)
        run_async(
            session.smart_health,
            self.storage_page.smart_page.set_result,
            self.storage_page.smart_page.set_error,
            guard=lambda: self.session is session,
        )

    def _smart_health_changed(self, state: str, message: str) -> None:
        if state == "error":
            self.alert_raised.emit("smart_health", "Santé des disques", message)
        else:
            self.alert_cleared.emit("smart_health")

    def _analyze_storage(self, mount: str) -> None:
        import shlex
        if not self.session:
            return
        command = f"du -xah -- {shlex.quote(mount)} 2>/dev/null | sort -rh | head -n 80"
        self.storage_page.show_analysis(f"Analyse de {mount} en cours…")
        session = self.session
        run_async(
            lambda: session.execute(command, timeout=120),
            lambda result: self.storage_page.show_analysis(str(result[1]) or "Aucun résultat."),
            lambda text: self.storage_page.show_analysis(f"Analyse impossible : {text}"),
            guard=lambda: self.session is session,
        )

    def _check_alerts(
        self,
        disk: float,
        temperature: float,
        mounts: list[dict[str, object]],
    ) -> None:
        disk_threshold = float(self.settings.get("disk_alert_percent", 85))
        self._threshold_alert(
            "disk:/",
            disk,
            disk_threshold,
            "Stockage",
            f"Le disque racine est utilisé à {disk:.1f} %.",
        )
        active_mount_keys = {"disk:/"}
        for mount in mounts:
            path = str(mount.get("mount") or "")
            if not path or path == "/":
                continue
            raw = str(mount.get("percent") or "0").rstrip("%")
            try:
                percent = float(raw)
            except ValueError:
                continue
            key = f"disk:{path}"
            active_mount_keys.add(key)
            self._threshold_alert(
                key,
                percent,
                disk_threshold,
                "Stockage",
                f"Le volume {path} est utilisé à {percent:.1f} %.",
            )
        for key in list(self.alerted):
            if key.startswith("disk:") and key not in active_mount_keys:
                self.alerted.discard(key)
                self.alert_cleared.emit(key)
        if temperature:
            self._threshold_alert(
                "temperature",
                temperature,
                float(self.settings.get("temperature_alert_c", 75)),
                "Température",
                f"Une sonde atteint {temperature:.1f} °C.",
            )
        elif "temperature" in self.alerted:
            self.alerted.discard("temperature")
            self.alert_cleared.emit("temperature")

    def _threshold_alert(
        self,
        key: str,
        value: float,
        threshold: float,
        title: str,
        message: str,
    ) -> None:
        if value >= threshold and key not in self.alerted:
            self.alerted.add(key)
            self.alert_raised.emit(key, title, message)
        elif value < threshold - 5 and key in self.alerted:
            self.alerted.discard(key)
            self.alert_cleared.emit(key)

    def _clear_display(self) -> None:
        for card in self.cards.values():
            card.set_metric("—", "Déconnecté", [], 100)
        self.memory_page.card.set_metric("—", "Déconnecté", [], 100)
        self.memory_page.update_processes([])
        self.cpu_page.update_cores({}, {}, {}, [0, 0, 0])
        self.cpu_page.update_processes([])
        self.storage_page.update_mounts([], self._bytes)
        self.storage_page.set_inventory({})
        self.network_page.update_interfaces([])
        self.sensors_page.update_sensors([], {})
        self.gpu_page.update_gpus([], {}, self._bytes)
        self.gpu_page.set_info("")
        self.hardware_page.set_text("Déconnecté")
        self.update_label.setText("Mises à jour : —")
        self.update_button.hide()

    @staticmethod
    def _memory_percent(data: dict[str, object]) -> float:
        total, available, *_ = data.get("mem", [0, 0])
        return 0.0 if not total else (total - available) * 100 / total

    @staticmethod
    def _disk_percent(data: dict[str, object]) -> float:
        total, used, *_ = data.get("disk", [0, 0])
        return 0.0 if not total else used * 100 / total

    @staticmethod
    def _append(history: list[float], value: float) -> None:
        history.append(float(value))
        del history[:-120]

    @staticmethod
    def _bytes(value: float) -> str:
        for unit in byte_units():
            if value < 1024 or unit in {"To", "TB"}:
                return f"{value:.0f} {unit}" if unit in {"o", "B"} else f"{value:.1f} {unit}"
            value /= 1024
        return f"0 {byte_units()[0]}"

    @classmethod
    def _rate(cls, value: float) -> str:
        return cls._bytes(value) + "/s"

    @staticmethod
    def _duration(seconds: int) -> str:
        days, seconds = divmod(seconds, 86400)
        hours, seconds = divmod(seconds, 3600)
        minutes, _ = divmod(seconds, 60)
        return f"{days} j {hours} h {minutes} min"
