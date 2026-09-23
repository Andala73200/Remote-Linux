from __future__ import annotations

import json


class SmartProbeMixin:
    def smart_health(self) -> dict[str, object]:
        code, _ = self.execute("command -v smartctl >/dev/null 2>&1", timeout=10)
        if code != 0:
            return {
                "installed": False,
                "install_command": self.tool_install_command("smartmontools"),
                "devices": [],
            }
        script = r'''
for device in $(lsblk -dnpo PATH,TYPE 2>/dev/null | awk '$2=="disk"{print $1}'); do
  printf '__SMART_DEVICE__%s\n' "$device"
  smartctl -a -j -- "$device" 2>&1 || true
  printf '__SMART_END__\n'
done
'''.strip()
        _, output, privileged = self._diagnostic_execute(script, timeout=180)
        devices = self._parse_smart_blocks(output)
        return {
            "installed": True, "privileged": privileged, "devices": devices,
            "healthy": bool(devices) and all(
                row.get("passed") is not False for row in devices
            ),
        }

    @classmethod
    def _parse_smart_blocks(cls, text: str) -> list[dict[str, object]]:
        devices = []
        current_path = ""
        lines: list[str] = []
        for line in text.splitlines():
            if line.startswith("__SMART_DEVICE__"):
                current_path = line.partition("__SMART_DEVICE__")[2]
                lines = []
            elif line == "__SMART_END__":
                if current_path:
                    devices.append(cls._smart_row(current_path, "\n".join(lines)))
                current_path, lines = "", []
            elif current_path:
                lines.append(line)
        return devices

    @classmethod
    def _smart_row(cls, path: str, raw: str) -> dict[str, object]:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {
                "path": path, "model": "—", "passed": None,
                "temperature": None, "wear": None, "errors": None,
                "hours": None, "message": raw.strip() or "Lecture SMART impossible.",
            }
        smartctl = data.get("smartctl") or {}
        messages = [
            str(item.get("string") or "")
            for item in smartctl.get("messages", [])
            if isinstance(item, dict) and item.get("string")
        ]
        status = data.get("smart_status") or {}
        passed = status.get("passed") if "passed" in status else None
        nvme = data.get("nvme_smart_health_information_log") or {}
        wear = cls._nested(data, "endurance_used", "current_percent")
        if wear is None:
            wear = nvme.get("percentage_used")
        errors = nvme.get("media_errors")
        attributes = (
            (data.get("ata_smart_attributes") or {}).get("table") or []
        )
        ata_errors = []
        for attribute in attributes:
            if not isinstance(attribute, dict):
                continue
            identifier = int(attribute.get("id") or 0)
            if identifier in {5, 187, 188, 197, 198}:
                raw_value = (attribute.get("raw") or {}).get("value")
                try:
                    if raw_value:
                        ata_errors.append(int(raw_value))
                except (TypeError, ValueError):
                    pass
            if wear is None and identifier in {177, 202, 231, 233}:
                value = attribute.get("value")
                try:
                    if value is not None:
                        wear = max(0, 100 - int(value))
                except (TypeError, ValueError):
                    pass
        if errors is None and ata_errors:
            errors = sum(ata_errors)
        return {
            "path": str((data.get("device") or {}).get("name") or path),
            "model": str(data.get("model_name") or data.get("product") or "—"),
            "serial": str(data.get("serial_number") or "—"),
            "passed": passed,
            "temperature": cls._nested(data, "temperature", "current"),
            "wear": wear, "errors": errors,
            "hours": cls._nested(data, "power_on_time", "hours"),
            "message": "\n".join(messages),
        }

    @staticmethod
    def _nested(data: dict, first: str, second: str):
        value = data.get(first) or {}
        return value.get(second) if isinstance(value, dict) else None
