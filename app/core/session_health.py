from __future__ import annotations


class SessionHealthMixin:
    def health_summary(self) -> dict[str, object]:
        result: dict[str, object] = {}
        cpu_code, cpu_text = self.execute(
            "awk '/^cpu /{i=$5+$6;t=0;for(n=2;n<=NF;n++)t+=$n;print t,i;exit}' /proc/stat; "
            "sleep 0.25; awk '/^cpu /{i=$5+$6;t=0;for(n=2;n<=NF;n++)t+=$n;print t,i;exit}' /proc/stat",
            timeout=10,
        )
        cpu_percent = None
        if cpu_code == 0:
            values = [line.split() for line in cpu_text.splitlines() if len(line.split()) == 2]
            if len(values) >= 2:
                try:
                    total = int(values[1][0]) - int(values[0][0])
                    idle = int(values[1][1]) - int(values[0][1])
                    cpu_percent = 0.0 if total <= 0 else (total - idle) * 100 / total
                except ValueError:
                    pass
        try:
            snapshot = self.system_snapshot()
            total, available, *_ = snapshot.get("mem", [0, 0])
            disk_total, disk_used, disk_free, _ = snapshot.get(
                "disk", [0, 0, 0, "0%"]
            )
            result["system"] = {
                "memory_percent": 0 if not total else (total - available) * 100 / total,
                "memory_used": max(0, total - available), "memory_total": total,
                "disk_percent": 0 if not disk_total else disk_used * 100 / disk_total,
                "disk_free": disk_free, "load": snapshot.get("load", [0, 0, 0]),
                "temperature": snapshot.get("temp", 0),
                "cpu_percent": cpu_percent,
            }
        except Exception as exc:
            result["system_error"] = str(exc)
        code, output = self.execute(
            "systemctl --failed --type=service --no-legend --plain --no-pager 2>/dev/null",
            timeout=30,
        )
        failed_services = [line.split()[0] for line in output.splitlines() if line.split()]
        result["services"] = {
            "failed": failed_services, "accessible": code == 0
        }
        try:
            result["backups"] = self.backup_status()
        except Exception as exc:
            result["backups"] = {"installed": True, "healthy": False, "error": str(exc)}
        try:
            result["smart"] = self.smart_health()
        except Exception as exc:
            result["smart"] = {"installed": True, "healthy": False, "error": str(exc)}
        return result
