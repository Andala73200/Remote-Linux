from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QMenu, QProgressBar, QPushButton,
    QToolButton, QVBoxLayout, QWidget,
)

from app.i18n import byte_units, tr


def human_size(value: int | float) -> str:
    size = float(value or 0)
    for unit in byte_units():
        if size < 1024 or unit in {"To", "TB"}:
            return f"{size:.0f} {unit}" if unit in {"o", "B"} else f"{size:.1f} {unit}"
        size /= 1024
    return f"0 {byte_units()[0]}"


def usage_color(percent: int) -> str:
    if percent >= 95:
        return "#ff4d5a"
    if percent >= 85:
        return "#ff6b6b"
    if percent >= 70:
        return "#f6b94a"
    return "#55d187"


def device_icon(device: dict) -> str:
    transport = str(device.get("tran") or "").lower()
    rotational = int(device.get("rota") or 0)
    if transport == "usb" or int(device.get("rm") or 0):
        return "🔌"
    if transport == "nvme" or str(device.get("name", "")).startswith("nvme"):
        return "⚡"
    return "💽" if rotational else "▣"


class VolumeCard(QFrame):
    analyze_requested = Signal(str)

    def __init__(self, row: dict, parent=None):
        super().__init__(parent)
        self.setObjectName("storageCard")
        self.setFrameShape(QFrame.StyledPanel)
        mount = str(row.get("mount") or "—")
        device = str(row.get("device") or "")
        total = int(row.get("total") or 0)
        used = int(row.get("used") or 0)
        free = int(row.get("free") or 0)
        percent = self._percent(row.get("percent"), used, total)
        title = QLabel(f"📁  {mount}")
        title.setStyleSheet("font-size: 13pt; font-weight: 700;")
        badge = QLabel(f"{percent} %")
        badge.setStyleSheet(
            f"background:{usage_color(percent)}; color:#101216; border-radius:10px; "
            "padding:3px 8px; font-weight:700;"
        )
        header = QHBoxLayout()
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(badge)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(percent)
        self.progress.setTextVisible(False)
        self.progress.setStyleSheet(
            "QProgressBar{background:#0d0f12;border:1px solid #343840;border-radius:6px;height:13px;}"
            f"QProgressBar::chunk{{background:{usage_color(percent)};border-radius:5px;}}"
        )
        numbers = QLabel(
            f"<b>{human_size(used)}</b> {tr('ui.used_of')} {human_size(total)}"
            f"   •   <span style='color:#9aa4af'>{human_size(free)} {tr('ui.free_0ce019')}</span>"
        )
        detail = QLabel(f"{device}   •   {tr(row.get('fstype') or 'ui.unknown_file_system')}")
        detail.setStyleSheet("color:#aeb7c2;")
        analyze = QPushButton("📊  Analyser")
        analyze.clicked.connect(lambda: self.analyze_requested.emit(mount))
        actions = QHBoxLayout()
        actions.addWidget(detail, 1)
        actions.addWidget(analyze)
        layout = QVBoxLayout(self)
        layout.addLayout(header)
        layout.addWidget(self.progress)
        layout.addWidget(numbers)
        layout.addLayout(actions)
        self.setToolTip(
            f"{tr('ui.device_6f37ed')} {device}\n{tr('ui.mount_label')} {mount}\n"
            f"{tr('ui.total_45b5a7')} {human_size(total)}\n"
            f"{tr('fragment.used')} {human_size(used)}\n{tr('fragment.free')} {human_size(free)}"
        )

    @staticmethod
    def _percent(raw, used: int, total: int) -> int:
        try:
            return max(0, min(100, int(str(raw).replace("%", ""))))
        except ValueError:
            return 0 if not total else round(used * 100 / total)


class PartitionRow(QWidget):
    action_requested = Signal(str, dict)

    def __init__(self, device: dict, protected: bool, parent=None):
        super().__init__(parent)
        self.device = device
        name = str(device.get("name") or "partition")
        path = str(device.get("path") or f"/dev/{name}")
        mounts = [str(x) for x in (device.get("mountpoints") or []) if x]
        fs = str(device.get("fstype") or "Non formaté")
        label = str(device.get("label") or "")
        icon = "🧩"
        title = QLabel(f"{icon}  {name}" + (f"  —  {label}" if label else ""))
        title.setStyleSheet("font-weight:600;")
        detail = QLabel(f"{human_size(int(device.get('size') or 0))}   •   {tr(fs)}   •   {', '.join(mounts) or tr('ui.not_mounted')}")
        detail.setStyleSheet("color:#aeb7c2;")
        manage = QToolButton()
        manage.setText("Gérer  ▾")
        manage.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(manage)
        protected_actions = []
        if mounts:
            protected_actions.append(
                menu.addAction(
                    "⏏  Démonter",
                    lambda: self.action_requested.emit(
                        "unmount", self._payload(path, mounts)
                    ),
                )
            )
        elif fs not in {"", "Non formaté"}:
            menu.addAction("➕  Monter…", lambda: self.action_requested.emit("mount", self._payload(path, mounts)))
        if fs not in {"", "Non formaté"}:
            protected_actions.append(
                menu.addAction(
                    "⚙  Montage automatique…",
                    lambda: self.action_requested.emit(
                        "automount", self._payload(path, mounts)
                    ),
                )
            )
        menu.addSeparator()
        format_action = menu.addAction("🗑  Formater…", lambda: self.action_requested.emit("format", self._payload(path, mounts)))
        delete_action = menu.addAction("⚠  Supprimer la partition…", lambda: self.action_requested.emit("remove_partition", self._payload(path, mounts)))
        format_action.setEnabled(not protected and not int(device.get("ro") or 0))
        delete_action.setEnabled(not protected and not int(device.get("ro") or 0))
        if protected:
            for action in protected_actions:
                action.setEnabled(False)
                action.setToolTip("Partition système protégée")
            format_action.setToolTip("Partition système protégée")
            delete_action.setToolTip("Partition système protégée")
        manage.setMenu(menu)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 3, 4, 3)
        layout.addWidget(title)
        layout.addWidget(detail, 1)
        layout.addWidget(manage)

    def _payload(self, path: str, mounts: list[str]) -> dict:
        result = dict(self.device)
        result["path"] = path
        result["mount"] = mounts[0] if mounts else ""
        return result


class DiskCard(QFrame):
    action_requested = Signal(str, dict)

    def __init__(self, device: dict, parent=None):
        super().__init__(parent)
        self.device = device
        self.setObjectName("storageCard")
        self.setFrameShape(QFrame.StyledPanel)
        protected = self._is_protected(device)
        path = str(device.get("path") or f"/dev/{device.get('name', '')}")
        model = " ".join(str(device.get(key) or "").strip() for key in ("vendor", "model")).strip() or path
        transport = str(device.get("tran") or "inconnu").upper()
        title = QLabel(f"{device_icon(device)}  {model}")
        title.setStyleSheet("font-size:13pt;font-weight:700;")
        state = QLabel(tr('ui.protected_system' if protected else 'ui.available'))
        state.setStyleSheet(f"color:{'#f6b94a' if protected else '#55d187'};font-weight:600;")
        header = QHBoxLayout()
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(state)
        details = QLabel(
            f"{path}   •   {human_size(int(device.get('size') or 0))}   •   {transport}"
            f"   •   {'HDD' if int(device.get('rota') or 0) else 'SSD / Flash'}"
        )
        details.setStyleSheet("color:#aeb7c2;")
        serial = str(device.get("serial") or "Non communiqué")
        serial_label = QLabel(f"{tr('ui.serial_number')} {serial}")
        serial_label.setStyleSheet("color:#7f8994;")
        tools = QHBoxLayout()
        smart = QPushButton("🩺  Santé SMART")
        smart.clicked.connect(lambda: self.action_requested.emit("smart", {**device, "path": path}))
        initialize = QPushButton("⚠  Initialiser le disque…")
        initialize.clicked.connect(lambda: self.action_requested.emit("initialize", {**device, "path": path}))
        initialize.setEnabled(not protected and not int(device.get("ro") or 0))
        tools.addWidget(smart)
        tools.addStretch(1)
        tools.addWidget(initialize)
        body = QVBoxLayout()
        children = list(device.get("children") or [])
        if children:
            for child in children:
                child_payload = {**device, **child, "parent_path": path}
                row = PartitionRow(child_payload, protected or self._is_protected(child))
                row.action_requested.connect(self.action_requested.emit)
                body.addWidget(row)
        else:
            empty = QLabel("Aucune partition détectée")
            empty.setStyleSheet("color:#8d97a3;font-style:italic;padding:8px;")
            body.addWidget(empty)
        layout = QVBoxLayout(self)
        layout.addLayout(header)
        layout.addWidget(details)
        layout.addWidget(serial_label)
        layout.addLayout(body)
        layout.addLayout(tools)

    @classmethod
    def _is_protected(cls, device: dict) -> bool:
        mounts = device.get("mountpoints") or []
        if isinstance(mounts, str):
            mounts = [mounts]
        critical_roots = (
            "/boot", "/dev", "/etc", "/proc", "/run", "/sys", "/usr", "/var"
        )
        if any(
            str(mount) in {"/", "[SWAP]"}
            or any(
                str(mount) == root or str(mount).startswith(root + "/")
                for root in critical_roots
            )
            for mount in mounts
            if mount
        ):
            return True
        if str(device.get("fstype") or "").lower() == "swap":
            return True
        return any(cls._is_protected(child) for child in device.get("children", []) or [])
