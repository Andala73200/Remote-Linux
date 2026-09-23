import json


SNAPSHOT_COMMAND = r'''LC_ALL=C sh -c '
while read line; do echo "CPU|$line"; done < /proc/stat
printf "LOAD|"; cat /proc/loadavg
awk "/MemTotal/{mt=\$2}/MemAvailable/{ma=\$2}/Buffers/{bu=\$2}/^Cached:/{ca=\$2}/SwapTotal/{st=\$2}/SwapFree/{sf=\$2}END{print \"MEM|\"mt\"|\"ma\"|\"bu\"|\"ca\"|\"st\"|\"sf}" /proc/meminfo
df -B1 -PT | awk "NR>1{print \"MOUNT|\"\$1\"|\"\$2\"|\"\$3\"|\"\$4\"|\"\$5\"|\"\$6\"|\"\$7}"
awk "NR>2{gsub(/:/,\"\",\$1); print \"NET|\"\$1\"|\"\$2\"|\"\$10}" /proc/net/dev
printf "UP|"; cut -d. -f1 /proc/uptime
for c in /sys/devices/system/cpu/cpu[0-9]*; do n=${c##*cpu}; f=0; [ -r "$c/cpufreq/scaling_cur_freq" ] && f=$(cat "$c/cpufreq/scaling_cur_freq"); echo "FREQ|cpu$n|$f"; done
for h in /sys/class/hwmon/hwmon*; do [ -d "$h" ] || continue; chip=$(cat "$h/name" 2>/dev/null || echo hwmon); for f in "$h"/temp*_input; do [ -r "$f" ] || continue; b=${f%_input}; label=$(cat "${b}_label" 2>/dev/null || basename "$b"); echo "SENSOR|$chip|$label|$(cat "$f")"; done; done
if command -v nvidia-smi >/dev/null 2>&1; then nvidia-smi --query-gpu=index,name,temperature.gpu,utilization.gpu,memory.used,memory.total --format=csv,noheader,nounits | while IFS=, read i n t u mu mt; do echo "GPU|$i|$n|$t|$u|$mu|$mt"; done; fi
ps -eo pid=,user=,comm=,%cpu=,%mem= --sort=-%cpu | head -n 12 | sed "s/^/PROC|/"
ps -eo pid=,user=,comm=,%cpu=,%mem= --sort=-%mem | head -n 12 | sed "s/^/MEMPROC|/"
' '''

INVENTORY_COMMAND = r'''LC_ALL=C sh -c '
echo __CPU__; lscpu 2>/dev/null || cat /proc/cpuinfo
echo __LSBLK__; lsblk -J -b -o NAME,KNAME,PATH,PKNAME,TYPE,SIZE,FSTYPE,MOUNTPOINTS,MODEL,SERIAL,TRAN,VENDOR,ROTA,RO,RM,HOTPLUG,UUID,LABEL,PARTN 2>/dev/null || true
echo __GPU__; if command -v nvidia-smi >/dev/null 2>&1; then nvidia-smi --query-gpu=name,temperature.gpu,utilization.gpu,memory.used,memory.total,driver_version --format=csv,noheader,nounits; else lspci 2>/dev/null | grep -Ei "vga|3d|display" || true; fi
echo __NETWORK__; ip -brief address 2>/dev/null || ip addr 2>/dev/null || true
echo __PCI__; lspci 2>/dev/null || true
echo __USB__; lsusb 2>/dev/null || true
' '''


def parse_snapshot(output: str) -> dict[str, object]:
    data: dict[str, object] = {"cpus": {}, "freqs": {}, "sensors": [], "mounts": [], "nets": {}, "processes": [], "mem_processes": [], "gpus": []}
    for line in output.splitlines():
        if not line:
            continue
        parts = line.split("|")
        key = parts[0]
        if key == "CPU" and len(parts) > 1:
            values = parts[1].split()
            if len(values) >= 5 and values[0].startswith("cpu"):
                nums = [int(value) for value in values[1:]]
                data["cpus"][values[0]] = {"total": sum(nums), "idle": nums[3] + (nums[4] if len(nums) > 4 else 0)}
        elif key == "LOAD" and len(parts) > 1:
            data["load"] = [float(v) for v in parts[1].split()[:3]]
        elif key == "MEM" and len(parts) >= 7:
            data["mem"] = [int(v or 0) * 1024 for v in parts[1:7]]
        elif key == "MOUNT" and len(parts) >= 8:
            try:
                data["mounts"].append({
                    "device": parts[1], "fstype": parts[2], "total": int(parts[3]),
                    "used": int(parts[4]), "free": int(parts[5]), "percent": parts[6],
                    "mount": "|".join(parts[7:]),
                })
            except ValueError:
                pass
        elif key == "NET" and len(parts) >= 4:
            try:
                data["nets"][parts[1]] = [int(parts[2]), int(parts[3])]
            except ValueError:
                pass
        elif key == "UP" and len(parts) > 1:
            data["uptime"] = int(parts[1] or 0)
        elif key == "FREQ" and len(parts) >= 3:
            data["freqs"][parts[1]] = int(parts[2] or 0) * 1000
        elif key == "SENSOR" and len(parts) >= 4:
            try:
                value = float(parts[3]) / 1000.0
                if -50.0 <= value <= 200.0:
                    data["sensors"].append({
                        "chip": parts[1],
                        "label": parts[2],
                        "value": value,
                    })
            except ValueError:
                pass
        elif key == "GPU" and len(parts) >= 7:
            try:
                data["gpus"].append({
                    "index": parts[1].strip(), "name": parts[2].strip(),
                    "temperature": float(parts[3].strip()), "usage": float(parts[4].strip()),
                    "memory_used": float(parts[5].strip()) * 1024 * 1024,
                    "memory_total": float(parts[6].strip()) * 1024 * 1024,
                })
            except ValueError:
                pass
        elif key in {"PROC", "MEMPROC"} and len(parts) > 1:
            values = "|".join(parts[1:]).split(None, 4)
            if len(values) == 5:
                target = "processes" if key == "PROC" else "mem_processes"
                data[target].append({"pid": values[0], "user": values[1], "name": values[2], "cpu": values[3], "mem": values[4]})
    cpus = data.get("cpus", {})
    if "cpu" in cpus:
        data["cpu_total"] = cpus["cpu"]["total"]
        data["cpu_idle"] = cpus["cpu"]["idle"]
    root = next((item for item in data["mounts"] if item["mount"] == "/"), None)
    if root:
        data["disk"] = [root["total"], root["used"], root["free"], root["percent"]]
    total_rx = sum(value[0] for name, value in data["nets"].items() if name != "lo")
    total_tx = sum(value[1] for name, value in data["nets"].items() if name != "lo")
    data["net"] = [total_rx, total_tx]
    data["temp"] = max((sensor["value"] for sensor in data["sensors"]), default=0.0)
    return data


def parse_inventory(output: str) -> dict[str, object]:
    markers = ["CPU", "LSBLK", "GPU", "NETWORK", "PCI", "USB"]
    sections: dict[str, str] = {name.lower(): "" for name in markers}
    current = ""
    lines: dict[str, list[str]] = {name.lower(): [] for name in markers}
    for line in output.splitlines():
        if line.startswith("__") and line.endswith("__"):
            current = line.strip("_").lower()
        elif current in lines:
            lines[current].append(line)
    for name, values in lines.items():
        sections[name] = "\n".join(values).strip()
    try:
        sections["lsblk_json"] = json.loads(sections.get("lsblk", "") or "{}")
    except json.JSONDecodeError:
        sections["lsblk_json"] = {}
    return sections
