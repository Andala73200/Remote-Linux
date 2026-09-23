from __future__ import annotations

import re
import shlex

_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")


def is_cd_command(command: str) -> bool:
    """Return True when the submitted shell command starts with a real cd command."""
    text = str(command or "").strip()
    if not text:
        return False
    try:
        tokens = shlex.split(text, posix=True)
    except ValueError:
        return False
    while tokens and _ASSIGNMENT_RE.fullmatch(tokens[0]):
        tokens.pop(0)
    if not tokens:
        return False
    if tokens[0] == "cd":
        return True
    return len(tokens) >= 2 and tokens[0] in {"builtin", "command"} and tokens[1] == "cd"
