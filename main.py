from __future__ import annotations

import faulthandler
import logging
from logging.handlers import RotatingFileHandler
import os
import sys
import threading
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtGui import QIcon  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from app.config import ERROR_LOG, LOG_FILE, NATIVE_CRASH_LOG  # noqa: E402
from app.i18n import configure, tr  # noqa: E402
from app.main_window import MainWindow  # noqa: E402
from app.version import APP_VERSION  # noqa: E402
from app.storage import Storage  # noqa: E402
from app.styles import DARK_STYLE  # noqa: E402
from app.visible_checkbox_style import VisibleCheckBoxStyle  # noqa: E402


_FAULT_LOG = None


def _resource_path(*parts: str) -> Path:
    """Return a resource path in source and frozen application builds."""
    bundle_root = Path(getattr(sys, "_MEIPASS", PROJECT_ROOT))
    return bundle_root.joinpath(*parts)


def _configure_windows_identity() -> None:
    """Keep the custom icon grouped correctly on the Windows taskbar."""
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "Andala.RemoteLinux"
        )
    except (AttributeError, OSError):
        logging.getLogger(__name__).debug(
            "Unable to configure the Windows application identifier",
            exc_info=True,
        )


def _configure_logging() -> None:
    handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=2_000_000,
        backupCount=4,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
        )
    )
    error_handler = RotatingFileHandler(
        ERROR_LOG,
        maxBytes=2_000_000,
        backupCount=4,
        encoding="utf-8",
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(handler.formatter)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    root.addHandler(handler)
    root.addHandler(error_handler)


def _configure_fault_logging() -> None:
    """Keep a Python stack trace when Qt or an extension aborts natively."""
    global _FAULT_LOG
    try:
        _FAULT_LOG = NATIVE_CRASH_LOG.open(
            "a", encoding="utf-8", buffering=1
        )
        faulthandler.enable(file=_FAULT_LOG, all_threads=True)
    except (OSError, RuntimeError):
        logging.getLogger(__name__).exception(
            "Unable to enable native crash logging"
        )


def _fatal_exception(exc_type, exc_value, exc_traceback) -> None:
    logging.getLogger(__name__).critical(
        "Unhandled error",
        exc_info=(exc_type, exc_value, exc_traceback),
    )
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    QMessageBox.critical(
        None,
        tr('ui.fatal_error'),
        f"{exc_value}\n\n{tr('ui.details_saved_to')}\n{ERROR_LOG}",
    )


def _thread_exception(args) -> None:
    logging.getLogger(__name__).critical(
        "Unhandled thread error",
        exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
    )


def _unraisable_exception(args) -> None:
    logging.getLogger(__name__).error(
        "Unraisable error in %r: %s",
        args.object,
        args.exc_value,
        exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
    )


def main() -> int:
    _configure_logging()
    _configure_fault_logging()
    _configure_windows_identity()
    sys.excepthook = _fatal_exception
    threading.excepthook = _thread_exception
    sys.unraisablehook = _unraisable_exception
    logging.getLogger(__name__).info("Remote Linux V%s started", APP_VERSION)
    qt_app = QApplication(sys.argv)
    qt_app.setApplicationName("Remote Linux")
    qt_app.setOrganizationName("Andala")
    qt_app.setWindowIcon(
        QIcon(str(_resource_path("app", "assets", "remote_linux.png")))
    )
    storage = Storage()
    configure(qt_app, str(storage.settings.get("language", "auto")))
    qt_app.setStyle(VisibleCheckBoxStyle())
    qt_app.setStyleSheet(DARK_STYLE)

    try:
        window = MainWindow(storage)
        window.show()
        return qt_app.exec()
    except Exception:
        logging.getLogger(__name__).exception("Startup failed")
        QMessageBox.critical(
            None,
            tr('ui.fatal_error'),
            f"{tr('ui.startup_failed')}\n\n{tr('ui.details_saved_to')}\n{ERROR_LOG}",
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
