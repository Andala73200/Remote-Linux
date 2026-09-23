from pathlib import Path
import os

APP_NAME = "AndalaRemote"


def app_data_dir() -> Path:
    base = os.getenv("APPDATA")
    root = Path(base) if base else Path.home() / ".config"
    path = root / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


DATA_FILE = app_data_dir() / "data.json"
KNOWN_HOSTS_FILE = app_data_dir() / "known_hosts"
LOG_DIR = app_data_dir() / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "remote-linux.log"
ERROR_LOG = LOG_DIR / "errors.log"
NATIVE_CRASH_LOG = LOG_DIR / "native-crash.log"
