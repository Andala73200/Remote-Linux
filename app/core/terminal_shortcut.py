from __future__ import annotations

DEFAULT_COMPLETION_SHORTCUT = "²"

_FORBIDDEN_EXACT = {
    "Tab",
    "Backtab",
    "Shift+Tab",
    "Return",
    "Enter",
    "Esc",
    "Escape",
    "Up",
    "Down",
    "Left",
    "Right",
    "Home",
    "End",
    "Ins",
    "Insert",
    "Del",
    "Delete",
    "PgUp",
    "PgDown",
    "PageUp",
    "PageDown",
    "Alt+F4",
    "Alt+Tab",
    "Alt+Shift+Tab",
    "Ctrl+Esc",
    "Ctrl+Alt+Del",
    "Ctrl+Alt+Delete",
    "Ctrl+C",
    "Ctrl+D",
    "Ctrl+Q",
    "Ctrl+S",
    "Ctrl+V",
    "Ctrl+Z",
    "Ctrl+Shift+C",
}


def validate_completion_shortcut(value: object) -> tuple[bool, str]:
    text = str(value or "").strip()
    if not text:
        return False, "empty"
    if "," in text:
        return False, "multi_step"
    compact = text.replace(" ", "")
    forbidden = {item.replace(" ", "").casefold() for item in _FORBIDDEN_EXACT}
    if compact.casefold() in forbidden:
        return False, "reserved"
    lowered = compact.casefold()
    parts = lowered.split("+")
    if (
        len(parts) == 2
        and parts[0] in {"ctrl", "control"}
        and len(parts[1]) == 1
        and parts[1].isalpha()
    ):
        return False, "terminal"
    if lowered.startswith(("meta+", "win+", "super+")) or "+meta+" in lowered:
        return False, "system"
    if "+win+" in lowered or "+super+" in lowered:
        return False, "system"
    # Never steal ordinary typing from the terminal. Rare printable symbols such
    # as the French AZERTY ² key remain valid and are ideal defaults.
    if len(text) == 1 and text.isascii() and text.isprintable():
        return False, "typing"
    if "unknown" in lowered:
        return False, "unsupported"
    return True, ""
