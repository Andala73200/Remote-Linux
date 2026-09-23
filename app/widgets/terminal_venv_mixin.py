from __future__ import annotations

import posixpath
import shlex

from PySide6.QtCore import QTimer

from app.core.async_task import run_async
from app.i18n import tr


class TerminalVenvMixin:
    def _init_venv_support(self) -> None:
        self.virtual_env = ""
        self.venv_prompt_enabled = True
        self._venv_last_checked_path = ""
        self._environment_name = "Linux —"

    def set_venv_prompt_enabled(self, enabled: bool) -> None:
        changed = self.venv_prompt_enabled != bool(enabled)
        self.venv_prompt_enabled = bool(enabled)
        if changed and self.venv_prompt_enabled:
            self._venv_last_checked_path = ""
            self._check_project_venv()

    def set_virtual_env(self, path: str) -> None:
        value = str(path or "").strip()
        if value == self.virtual_env:
            return
        self.virtual_env = value
        self._refresh_environment_badge()

    def _venv_path_changed(self) -> None:
        # Let the prompt's VIRTUAL_ENV marker arrive before probing the folder.
        QTimer.singleShot(250, self._check_project_venv)

    def _check_project_venv(self) -> None:
        path = str(self.current_path or "")
        session = self.session
        if (
            not self.venv_prompt_enabled
            or not session
            or not path.startswith("/")
            or path == self._venv_last_checked_path
        ):
            return
        self._venv_last_checked_path = path
        run_async(
            lambda: session.project_venv(path),
            lambda candidate: self._project_venv_ready(
                session, path, str(candidate or "")
            ),
            guard=lambda: (
                self.session is session
                and self.current_path == path
                and self.venv_prompt_enabled
            ),
        )

    def _project_venv_ready(self, session, path: str, candidate: str) -> None:
        if self.session is not session or self.current_path != path or not candidate:
            return
        current = posixpath.normpath(self.virtual_env) if self.virtual_env else ""
        if current == posixpath.normpath(candidate):
            return
        self.venv_activation_requested.emit(self, candidate)

    def activate_project_venv(self, venv_path: str) -> None:
        activate = posixpath.join(str(venv_path), "bin", "activate")
        self.send_command(f". {shlex.quote(activate)}")

    def _venv_session_detached(self) -> None:
        self.virtual_env = ""
        self._venv_last_checked_path = ""
        self._refresh_environment_badge()

    def _refresh_environment_badge(self) -> None:
        if not hasattr(self, "environment_label"):
            return
        name = self._environment_name or "Linux"
        suffix = "  •  .venv" if self.virtual_env else ""
        target = name + suffix
        if self.environment_label.text() != target:
            self.environment_label.setText(target)
        details = f"{tr('ui.venv_active')}: {self.virtual_env}" if self.virtual_env else ""
        tooltip = f"{tr('ui.detected_distribution')} {name}"
        self.environment_label.setToolTip(
            tooltip + (f"\n{details}" if details else "")
        )
