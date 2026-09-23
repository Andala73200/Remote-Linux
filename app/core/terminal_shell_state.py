from __future__ import annotations


class ShellCommandTracker:
    def __init__(self) -> None:
        self.at_prompt = False
        self.reliable = True
        self.line = ""
        self.pending: list[str] = []
        self.completion_line: str | None = None

    def input(
        self, data: bytes, managers: set[str]
    ) -> tuple[bytes, str | None, str | None]:
        if not self.at_prompt:
            return data, None, None
        if data in {b"\r", b"\n"}:
            command = self.line.strip() if self.reliable else ""
            self.completion_line = None
            if command in managers:
                self.line = ""
                self.reliable = True
                return b"\x15", command, None
            self.pending.append(command)
            self.at_prompt = False
            self.line = ""
            self.reliable = True
            return data, None, command
        if data in {b"\x7f", b"\x08"}:
            self.completion_line = None
            self.line = self.line[:-1]
            return data, None, None
        if data.startswith(b"\x1b[200~") and data.endswith(b"\x1b[201~"):
            content = data[6:-6]
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError:
                self.reliable = False
                return data, None, None
            self.line += text.replace("\r\n", "\n").replace("\r", "\n")
            self.completion_line = None
            return data, None, None
        if data == b"\x15":
            self.line = ""
            self.reliable = True
            self.completion_line = None
            return data, None, None
        if data in {b"\x03", b"\x04"}:
            self.line = ""
            self.reliable = True
            self.completion_line = None
            return data, None, None
        if data == b"\t":
            self.completion_line = self.line if self.reliable else self.completion_line
            self.reliable = False
            return data, None, None
        if b"\r" in data or b"\n" in data or data.startswith(b"\x1b"):
            self.completion_line = None
            self.reliable = False
            return data, None, None
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            self.reliable = False
            return data, None, None
        if text and all(ord(char) >= 32 for char in text):
            self.completion_line = None
            self.line += text
        elif data:
            self.completion_line = None
            self.reliable = False
        return data, None, None

    def completion_source(self) -> str | None:
        if not self.at_prompt:
            return None
        if self.reliable:
            return self.line
        return self.completion_line

    def replace_line(self, line: str) -> None:
        self.at_prompt = True
        self.line = line
        self.reliable = True
        self.completion_line = None

    def submitted(self, command: str) -> None:
        self.pending.append(command.strip())
        self.at_prompt = False
        self.line = ""
        self.reliable = True
        self.completion_line = None

    def prompt(self, code: int) -> tuple[str, int] | None:
        self.at_prompt = True
        self.line = ""
        self.reliable = True
        self.completion_line = None
        if not self.pending:
            return None
        return self.pending.pop(0), code

    def reset(self) -> None:
        self.at_prompt = False
        self.reliable = True
        self.line = ""
        self.completion_line = None
        self.pending.clear()
