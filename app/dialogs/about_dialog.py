from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel,
    QVBoxLayout, QWidget,
)

from app.i18n import tr
from app.version import APP_VERSION


ANDALWARE_URL = "https://andalware.andala-terra.fr/"


class AboutDialog(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(tr("ui.about_remote_linux"))
        self.setMinimumSize(580, 330)
        self.resize(620, 360)

        logo = QLabel()
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setFixedSize(132, 132)
        app = QApplication.instance()
        if app is not None:
            pixmap = app.windowIcon().pixmap(QSize(112, 112))
            if not pixmap.isNull():
                logo.setPixmap(pixmap)

        name = QLabel("Remote Linux")
        name.setStyleSheet("font-size:24pt;font-weight:700;")

        version = QLabel(f"{tr('ui.version')} {APP_VERSION}")
        version.setStyleSheet("color:#aab2bd;font-size:11pt;")

        description = QLabel(
            f"{tr('ui.visual_ssh_administration_for_linux')}\n\n"
            f"{tr('ui.terminal_systemd_services_sftp_files_monitoring_and_secure_storage')}"
        )
        description.setWordWrap(True)
        description.setStyleSheet("font-size:10.5pt;line-height:1.4;")

        website = QLabel(
            f'<a href="{ANDALWARE_URL}" style="color:#73a7e6;">{ANDALWARE_URL}</a>'
        )
        website.setTextFormat(Qt.TextFormat.RichText)
        website.setTextInteractionFlags(
            Qt.TextInteractionFlag.LinksAccessibleByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByKeyboard
        )
        website.setOpenExternalLinks(True)
        website.setToolTip(ANDALWARE_URL)

        details = QVBoxLayout()
        details.setContentsMargins(0, 8, 0, 0)
        details.setSpacing(8)
        details.addWidget(name)
        details.addWidget(version)
        details.addSpacing(4)
        details.addWidget(description)
        details.addStretch(1)
        details.addWidget(website)

        header = QHBoxLayout()
        header.setSpacing(22)
        header.addWidget(logo, 0, Qt.AlignmentFlag.AlignTop)
        header.addLayout(details, 1)

        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setFrameShadow(QFrame.Shadow.Sunken)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText(tr("ui.close"))
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 20)
        layout.setSpacing(18)
        layout.addLayout(header)
        layout.addWidget(separator)
        layout.addWidget(buttons)
