from __future__ import annotations

import posixpath
import re
import shlex
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.session import RemoteSession

_SAFE_SHELL = re.compile(r"^[A-Za-z0-9_@%+=:,./~-]*$")
_COMMAND_WRAPPERS = {"command", "nohup", "sudo"}
_DIRECTORY_COMMANDS = {"cd", "pushd"}
_MAX_RESULTS = 80


@dataclass(frozen=True)
class CompletionCandidate:
    label: str
    line: str
    kind: str


def completion_candidates(
    session: RemoteSession, line: str, current_path: str
) -> list[CompletionCandidate]:
    head, raw_token = split_completion_line(line)
    token = decode_shell_token(raw_token)
    command_word = first_command_word(line)

    if _is_command_position(head) and not _looks_like_path(token):
        return _command_candidates(session, head, token)

    directories_only = command_word in _DIRECTORY_COMMANDS
    if not directories_only and not _looks_like_path(token):
        programmable = _programmable_candidates(session, line, head, token)
        if programmable:
            return programmable
    return _path_candidates(
        session, head, token, current_path, directories_only=directories_only
    )


def split_completion_line(line: str) -> tuple[str, str]:
    quote = ""
    escaped = False
    token_start = 0
    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote != "'":
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = ""
            continue
        if char in {"'", '"'}:
            quote = char
        elif char.isspace():
            token_start = index + 1
    return line[:token_start], line[token_start:]


def decode_shell_token(raw: str) -> str:
    if not raw:
        return ""
    try:
        values = shlex.split(raw, posix=True)
        if len(values) == 1:
            return values[0]
    except ValueError:
        pass
    if raw[:1] in {"'", '"'}:
        raw = raw[1:]
    return re.sub(r"\\(.)", r"\1", raw)


def shell_escape_token(value: str) -> str:
    if _SAFE_SHELL.fullmatch(value):
        return value
    return re.sub(r"([^A-Za-z0-9_@%+=:,./~-])", r"\\\1", value)


def first_command_word(line: str) -> str:
    try:
        words = shlex.split(line, posix=True)
    except ValueError:
        words = line.strip().split()
    return words[0] if words else ""


def _is_command_position(head: str) -> bool:
    try:
        words = shlex.split(head, posix=True)
    except ValueError:
        words = head.strip().split()
    if not words:
        return True
    return len(words) == 1 and words[0] in _COMMAND_WRAPPERS


def _looks_like_path(token: str) -> bool:
    return "/" in token or token.startswith((".", "~", "$HOME"))


def _command_candidates(
    session: RemoteSession, head: str, prefix: str
) -> list[CompletionCandidate]:
    command = (
        "bash --noprofile --norc -c "
        + shlex.quote('compgen -A command -- "$1"')
        + " _ "
        + shlex.quote(prefix)
    )
    code, output = session.execute(command, timeout=8.0)
    if code not in {0, 1}:
        return []
    names = sorted(
        {name.strip() for name in output.splitlines() if name.strip()},
        key=str.casefold,
    )
    return [
        CompletionCandidate(
            label=f"⌨ {name}",
            line=head + shell_escape_token(name) + " ",
            kind="command",
        )
        for name in names[:_MAX_RESULTS]
    ]


def _programmable_candidates(
    session: RemoteSession, line: str, head: str, token: str
) -> list[CompletionCandidate]:
    try:
        words = shlex.split(line, posix=True)
    except ValueError:
        return []
    if line and line[-1].isspace():
        words.append("")
    if not words:
        return []
    cword = len(words) - 1
    command_word = words[0]
    script = r'''line=$1
cur=$2
cmd=$3
cword=$4
shift 4
words=("$@")
if [ -r /usr/share/bash-completion/bash_completion ]; then
    . /usr/share/bash-completion/bash_completion >/dev/null 2>&1
elif [ -r /etc/bash_completion ]; then
    . /etc/bash_completion >/dev/null 2>&1
else
    exit 3
fi
if declare -F _completion_loader >/dev/null 2>&1; then
    _completion_loader "$cmd" >/dev/null 2>&1 || true
fi
spec=$(complete -p "$cmd" 2>/dev/null) || exit 3
func=""
eval "set -- $spec"
while [ "$#" -gt 0 ]; do
    if [ "$1" = "-F" ] && [ "$#" -ge 2 ]; then
        func=$2
        break
    fi
    shift
done
[ -n "$func" ] || exit 3
declare -F "$func" >/dev/null 2>&1 || exit 3
COMP_LINE=$line
COMP_POINT=${#COMP_LINE}
COMP_WORDS=("${words[@]}")
COMP_CWORD=$cword
COMP_TYPE=9
COMP_KEY=9
prev=""
[ "$cword" -gt 0 ] && prev=${COMP_WORDS[cword-1]}
COMPREPLY=()
"$func" "$cmd" "$cur" "$prev" >/dev/null 2>&1 || true
printf '%s\n' "${COMPREPLY[@]}"
'''
    args = [line, token, command_word, str(cword), *words]
    command = "bash --noprofile --norc -c " + shlex.quote(script) + " _"
    command += "".join(" " + shlex.quote(value) for value in args)
    code, output = session.execute(command, timeout=8.0)
    if code not in {0, 1} or not output:
        return []

    values: list[str] = []
    seen: set[str] = set()
    for raw in output.splitlines():
        value = raw.rstrip("\r")
        if not value or value in seen:
            continue
        seen.add(value)
        values.append(value)
        if len(values) >= _MAX_RESULTS:
            break

    return [
        CompletionCandidate(
            label=f"↳ {value}",
            line=head
            + shell_escape_token(value)
            + ("" if value.endswith(("/", "=", ":")) else " "),
            kind="shell",
        )
        for value in values
    ]


def _path_candidates(
    session: RemoteSession,
    head: str,
    token: str,
    current_path: str,
    directories_only: bool,
) -> list[CompletionCandidate]:
    home = session.remote_home()
    cwd = _expand_directory(current_path or "~", home, home)
    typed_prefix, base = _path_prefix(token)
    directory_text = typed_prefix.rstrip("/")
    remote_directory = _expand_directory(directory_text, cwd, home)

    try:
        entries = session.list_directory(remote_directory)
    except Exception:
        return []

    candidates: list[CompletionCandidate] = []
    for entry in entries:
        name = str(entry.get("name") or "")
        is_dir = bool(entry.get("is_dir"))
        if not name.startswith(base):
            continue
        if not base.startswith(".") and name.startswith("."):
            continue
        if directories_only and not is_dir:
            continue
        suffix = "/" if is_dir else ""
        completed_token = typed_prefix + name + suffix
        completed_line = head + shell_escape_token(completed_token)
        if not is_dir:
            completed_line += " "
        candidates.append(
            CompletionCandidate(
                label=("📁 " if is_dir else "📄 ") + name + suffix,
                line=completed_line,
                kind="directory" if is_dir else "file",
            )
        )
        if len(candidates) >= _MAX_RESULTS:
            break
    return candidates


def _path_prefix(token: str) -> tuple[str, str]:
    if token in {"~", "$HOME"}:
        return token + "/", ""
    if not token:
        return "", ""
    if token.endswith("/"):
        return token, ""
    slash = token.rfind("/")
    if slash < 0:
        return "", token
    return token[: slash + 1], token[slash + 1 :]


def _expand_directory(value: str, cwd: str, home: str) -> str:
    if not value or value == ".":
        return posixpath.normpath(cwd)
    if value in {"~", "$HOME"}:
        return home
    if value.startswith("~/"):
        return posixpath.normpath(posixpath.join(home, value[2:]))
    if value.startswith("$HOME/"):
        return posixpath.normpath(posixpath.join(home, value[6:]))
    if value.startswith("/"):
        return posixpath.normpath(value)
    return posixpath.normpath(posixpath.join(cwd, value))
