from PySide6.QtWidgets import QGridLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from app.widgets.system_pages import MetricCard


class GpuPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.info = QLabel("Aucune métrique GPU temps réel disponible.")
        self.info.setWordWrap(True)
        self.cards: dict[str, MetricCard] = {}
        self.grid_widget = QWidget()
        self.grid = QGridLayout(self.grid_widget)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.grid_widget)
        layout = QVBoxLayout(self)
        layout.addWidget(self.info)
        layout.addWidget(scroll, 1)

    def set_info(self, text: str) -> None:
        self.info.setText(text or "Aucune carte graphique détectée.")

    def update_gpus(self, rows: list[dict], histories: dict[str, list[float]], formatter) -> None:
        keys = []
        for row in rows:
            index = str(row.get("index", "0"))
            keys.extend([f"gpu{index}:usage", f"gpu{index}:temp", f"gpu{index}:mem"])
        if set(keys) != set(self.cards):
            while self.grid.count():
                item = self.grid.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            self.cards.clear()
            for index, key in enumerate(keys):
                card = MetricCard(key)
                self.cards[key] = card
                self.grid.addWidget(card, index // 3, index % 3)
        for row in rows:
            index = str(row.get("index", "0"))
            name = str(row.get("name", f"GPU {index}"))
            usage = float(row.get("usage", 0))
            temp = float(row.get("temperature", 0))
            used = float(row.get("memory_used", 0))
            total = float(row.get("memory_total", 0))
            usage_key, temp_key, mem_key = f"gpu{index}:usage", f"gpu{index}:temp", f"gpu{index}:mem"
            self.cards[usage_key].setTitle(f"{name} — Charge")
            self.cards[usage_key].set_metric(f"{usage:.1f} %", "Utilisation GPU", histories.get(usage_key, []), 100)
            self.cards[temp_key].setTitle(f"{name} — Température")
            self.cards[temp_key].set_metric(f"{temp:.1f} °C", "Température actuelle", histories.get(temp_key, []), 120)
            percent = 0 if not total else used * 100 / total
            self.cards[mem_key].setTitle(f"{name} — Mémoire")
            self.cards[mem_key].set_metric(f"{percent:.1f} %", f"{formatter(used)} / {formatter(total)}", histories.get(mem_key, []), 100)

