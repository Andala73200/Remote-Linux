import shlex

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QLabel, QLineEdit, QPlainTextEdit, QSpinBox, QVBoxLayout,
)


class ServiceLogsDialog(QDialog):
    def __init__(self, services: list[str], favorites: set[str], params: dict | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Journaux d’un service")
        self.resize(680, 500)
        self.services = sorted(services, key=str.lower)
        self.favorites = set(favorites)
        self.params = dict(params or {})
        self.favorite_only = QCheckBox("Services favoris uniquement")
        self.favorite_only.setChecked(bool(self.params.get("favorite_only", True)))
        self.favorite_only.toggled.connect(self._reload_services)
        self.service = QComboBox()
        self.service.setEditable(True)
        self.period = QComboBox()
        periods = [
            ("10 dernières minutes", "10 minutes ago"),
            ("30 dernières minutes", "30 minutes ago"),
            ("1 dernière heure", "1 hour ago"),
            ("Aujourd’hui", "today"),
            ("Depuis le démarrage", "boot"),
            ("Tout", "all"),
        ]
        for label, value in periods:
            self.period.addItem(label, value)
        self.lines = QSpinBox()
        self.lines.setRange(10, 10000)
        self.lines.setValue(int(self.params.get("lines", 200)))
        self.priority = QComboBox()
        for label, value in [
            ("Tous les niveaux", ""), ("Information et plus grave", "info"),
            ("Avertissement et plus grave", "warning"), ("Erreur et plus grave", "err"),
            ("Critique uniquement", "crit"),
        ]:
            self.priority.addItem(label, value)
        self.search = QLineEdit(str(self.params.get("search", "")))
        self.search.setPlaceholderText("Texte facultatif")
        self.follow = QCheckBox("Suivre les nouveaux journaux en direct")
        self.follow.setChecked(bool(self.params.get("follow", False)))
        self.reverse = QCheckBox("Afficher les plus récents en premier")
        self.reverse.setChecked(bool(self.params.get("reverse", False)))
        self.save_preset = QCheckBox("Enregistrer ces paramètres comme nouveau favori")
        self.preset_name = QLineEdit("Logs personnalisés")
        self.preset_name.setEnabled(False)
        self.save_preset.toggled.connect(self.preset_name.setEnabled)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setMaximumHeight(100)
        form = QFormLayout()
        form.addRow("", self.favorite_only)
        form.addRow("Service :", self.service)
        form.addRow("Période :", self.period)
        form.addRow("Nombre de lignes :", self.lines)
        form.addRow("Niveau minimum :", self.priority)
        form.addRow("Contient :", self.search)
        form.addRow("", self.follow)
        form.addRow("", self.reverse)
        form.addRow("", self.save_preset)
        form.addRow("Nom du favori :", self.preset_name)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(QLabel("Commande générée :"))
        layout.addWidget(self.preview)
        layout.addWidget(buttons)
        for widget in (self.service, self.period, self.lines, self.priority, self.search, self.follow, self.reverse):
            signal = getattr(widget, "currentIndexChanged", None) or getattr(widget, "valueChanged", None) or getattr(widget, "textChanged", None) or getattr(widget, "toggled", None)
            signal.connect(self._update_preview)
        self._load_params()
        self._reload_services()

    def _load_params(self) -> None:
        period = str(self.params.get("period", "1 hour ago"))
        index = self.period.findData(period)
        self.period.setCurrentIndex(index if index >= 0 else 2)
        priority = str(self.params.get("priority", ""))
        index = self.priority.findData(priority)
        self.priority.setCurrentIndex(max(0, index))
        self.reverse.setChecked(bool(self.params.get("reverse", False)))

    def _reload_services(self) -> None:
        selected = self.params.get("service") or self.service.currentText()
        candidates = [name for name in self.services if not self.favorite_only.isChecked() or name in self.favorites]
        if not candidates:
            candidates = self.services
        self.service.blockSignals(True)
        self.service.clear()
        self.service.addItems(candidates)
        if selected:
            index = self.service.findText(str(selected))
            if index >= 0:
                self.service.setCurrentIndex(index)
            else:
                self.service.setEditText(str(selected))
        self.service.blockSignals(False)
        self._update_preview()

    def command(self) -> str:
        service = self.service.currentText().strip()
        command = ["journalctl", "-u", shlex.quote(service), "--no-pager"]
        period = self.period.currentData()
        if period == "boot":
            command.append("-b")
        elif period != "all":
            command.extend(["--since", shlex.quote(str(period))])
        if self.lines.value() and not self.follow.isChecked():
            command.extend(["-n", str(self.lines.value())])
        priority = self.priority.currentData()
        if priority:
            command.extend(["-p", str(priority)])
        if self.reverse.isChecked():
            command.append("-r")
        if self.follow.isChecked():
            command.extend(["-f", "--no-tail"])
        search = self.search.text().strip()
        text = " ".join(command)
        if search:
            text += " | grep --line-buffered -i -- " + shlex.quote(search)
        return text

    def values(self) -> dict[str, object]:
        return {
            "service": self.service.currentText().strip(), "favorite_only": self.favorite_only.isChecked(),
            "period": self.period.currentData(), "lines": self.lines.value(),
            "priority": self.priority.currentData(), "search": self.search.text().strip(),
            "follow": self.follow.isChecked(), "reverse": self.reverse.isChecked(),
        }

    def _update_preview(self, *_args) -> None:
        self.preview.setPlainText(self.command())


class SimpleParametersDialog(QDialog):
    def __init__(self, title: str, fields: list[tuple[str, str, str]], parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.inputs: dict[str, QLineEdit] = {}
        form = QFormLayout()
        for key, label, default in fields:
            edit = QLineEdit(default)
            self.inputs[key] = edit
            form.addRow(label, edit)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def values(self) -> dict[str, str]:
        return {key: edit.text().strip() for key, edit in self.inputs.items()}
