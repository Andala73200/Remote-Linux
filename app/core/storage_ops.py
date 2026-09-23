from __future__ import annotations

from pathlib import PurePosixPath
import re
import shlex


class StorageOperationsMixin:
    def sudo_execute(
        self,
        script: str,
        password: str | None = None,
        timeout: float = 180.0,
    ) -> tuple[int, str]:
        wrapped = shlex.quote(script)
        command = (
            f"sudo -S -p '' sh -c {wrapped}"
            if password
            else f"sudo -n sh -c {wrapped}"
        )
        with self._exec_lock:
            stdin, stdout, stderr = self.client.exec_command(
                command,
                timeout=timeout,
            )
            if password:
                stdin.write(password + "\n")
                stdin.flush()
            out = stdout.read().decode("utf-8", errors="replace")
            err = stderr.read().decode("utf-8", errors="replace")
            code = stdout.channel.recv_exit_status()
            return code, (out + err).strip()

    def storage_smart(
        self,
        device: str,
        password: str | None = None,
    ) -> tuple[int, str]:
        device = self._safe_device(device)
        quoted = shlex.quote(device)
        script = (
            self._device_guard(device)
            + "if ! command -v smartctl >/dev/null 2>&1; then "
            "echo 'smartmontools n’est pas installé.'; exit 127; fi; "
            f"smartctl -a -- {quoted}"
        )
        return self.sudo_execute(script, password, timeout=90)

    def storage_action(
        self,
        action: str,
        values: dict,
        password: str | None = None,
    ) -> tuple[int, str]:
        device = self._safe_device(str(values.get("path") or ""))
        mountpoint = self._safe_mountpoint(
            str(values.get("mountpoint") or values.get("mount") or ""),
            allow_empty=True,
        )
        filesystem = str(
            values.get("filesystem") or values.get("fstype") or "ext4"
        ).lower()
        label = self._safe_label(str(values.get("label") or ""))

        if action == "mount":
            mountpoint = self._safe_mountpoint(mountpoint)
            script = (
                self._protected_mount_guard(device)
                + f"mkdir -p -- {shlex.quote(mountpoint)}; "
                + f"mount -- {shlex.quote(device)} {shlex.quote(mountpoint)}"
            )
            code, output = self.sudo_execute(script, password)
            if code == 0 and bool(values.get("automount")):
                return self._set_automount(
                    device,
                    mountpoint,
                    filesystem,
                    password,
                )
            return code, output
        if action == "unmount":
            target = mountpoint or device
            return self.sudo_execute(
                self._protected_mount_guard(device)
                + f"umount -- {shlex.quote(target)}",
                password,
            )
        if action == "automount":
            return self._set_automount(
                device,
                self._safe_mountpoint(mountpoint),
                filesystem,
                password,
            )
        if action == "format":
            return self._format_partition(
                device,
                filesystem,
                label,
                password,
            )
        if action == "initialize":
            return self._initialize_disk(
                device,
                filesystem,
                label,
                mountpoint,
                bool(values.get("automount")),
                password,
            )
        if action == "remove_partition":
            parent = self._safe_device(
                str(values.get("parent_path") or self._parent_device(values))
            )
            partn = int(values.get("partn") or self._partition_number(device))
            script = (
                self._destructive_guard(device)
                + self._device_guard(parent, require_disk=True)
                + self._require_type(device, "part")
                + self._unmount_if_needed(device)
                + self._fstab_backup_script()
                + self._prepare_fstab_cleanup_script(device)
                + f"parted -s -- {shlex.quote(parent)} rm {partn}; "
                + f"partprobe -- {shlex.quote(parent)} || true; "
                + self._commit_fstab_cleanup_script()
                + "systemctl daemon-reload"
            )
            return self.sudo_execute(script, password)
        raise ValueError("Action de stockage inconnue.")

    def _set_automount(
        self,
        device: str,
        mountpoint: str,
        filesystem: str,
        password: str | None,
    ) -> tuple[int, str]:
        mountpoint = self._safe_mountpoint(mountpoint)
        fs = filesystem if re.fullmatch(r"[A-Za-z0-9._+-]+", filesystem) else "auto"
        device_q = shlex.quote(device)
        mount_q = shlex.quote(mountpoint)
        fs_q = shlex.quote(fs)
        script = (
            self._protected_mount_guard(device)
            + f"mountpoint={mount_q}; filesystem={fs_q}; "
            + f"uuid=$(blkid -s UUID -o value -- {device_q}); "
            + '[ -n "$uuid" ] || { echo "UUID introuvable."; exit 1; }; '
            + self._fstab_backup_script()
            + "tmp=$(mktemp); "
            + "awk -v uuid=\"$uuid\" "
            + '-v mp="$mountpoint" '
            + "'BEGIN{u=\"UUID=\" uuid} /^[[:space:]]*#/ || NF==0 {print; next} "
            + "$1==u || $2==mp {next} {print}' /etc/fstab > \"$tmp\"; "
            + "printf 'UUID=%s %s %s defaults,nofail,x-systemd.device-timeout=10s 0 2\\n' "
            + '"$uuid" "$mountpoint" "$filesystem" >> "$tmp"; '
            + "findmnt --verify --tab-file \"$tmp\" >/dev/null || { rm -f \"$tmp\"; echo 'Configuration fstab invalide.'; exit 1; }; "
            + f"mkdir -p -- {mount_q}; cp -- \"$tmp\" /etc/fstab; rm -f \"$tmp\"; "
            + "systemctl daemon-reload; "
            + f"mount -- {mount_q} || {{ cp -a -- \"$fstab_backup\" /etc/fstab; systemctl daemon-reload; echo 'Échec : /etc/fstab restauré.'; exit 1; }}; "
            + "echo \"Montage automatique enregistré. Sauvegarde : $fstab_backup\""
        )
        return self.sudo_execute(script, password)

    def _format_partition(
        self,
        device: str,
        filesystem: str,
        label: str,
        password: str | None,
    ) -> tuple[int, str]:
        commands = self._mkfs_commands(device, label)
        if filesystem not in commands:
            raise ValueError("Système de fichiers non pris en charge.")
        script = (
            self._destructive_guard(device)
            + self._unmount_if_needed(device)
            + self._fstab_backup_script()
            + self._prepare_fstab_cleanup_script(device)
            + f"{commands[filesystem]}; udevadm settle || true; "
            + self._commit_fstab_cleanup_script()
            + "systemctl daemon-reload; "
            + 'echo "Formatage terminé. Sauvegarde fstab : $fstab_backup"'
        )
        return self.sudo_execute(script, password, timeout=300)

    def _initialize_disk(
        self,
        device: str,
        filesystem: str,
        label: str,
        mountpoint: str,
        automount: bool,
        password: str | None,
    ) -> tuple[int, str]:
        suffix = "p1" if re.search(r"(?:nvme|mmcblk)\d+$", device) else "1"
        partition = device + suffix
        mkfs = self._mkfs_commands(partition, label).get(filesystem)
        if not mkfs:
            raise ValueError("Système de fichiers non pris en charge.")
        quoted = shlex.quote(device)
        partition_q = shlex.quote(partition)
        script = (
            self._destructive_guard(device, require_disk=True)
            + f"if lsblk -nrpo MOUNTPOINTS -- {quoted} | grep -q '[^[:space:]]'; then "
            + "echo 'Démontez toutes les partitions avant l’initialisation.'; exit 91; fi; "
            + f"wipefs -a -- {quoted}; parted -s -- {quoted} mklabel gpt mkpart primary 1MiB 100%; "
            + f"partprobe -- {quoted} || true; udevadm settle || true; "
            + f"i=0; while [ ! -b {partition_q} ] && [ $i -lt 20 ]; do sleep 0.5; i=$((i+1)); done; "
            + f"[ -b {partition_q} ] || {{ echo 'La nouvelle partition n’est pas apparue.'; exit 1; }}; "
            + mkfs
        )
        code, output = self.sudo_execute(script, password, timeout=300)
        if code != 0 or not mountpoint:
            return code, output
        values = {
            "path": partition,
            "mountpoint": mountpoint,
            "filesystem": filesystem,
            "automount": automount,
        }
        mount_code, mount_output = self.storage_action(
            "mount",
            values,
            password,
        )
        return mount_code, (output + "\n" + mount_output).strip()

    @staticmethod
    def _mkfs_commands(device: str, label: str) -> dict[str, str]:
        quoted = shlex.quote(device)
        return {
            "ext4": f"mkfs.ext4 -F {'-L ' + shlex.quote(label) if label else ''} -- {quoted}",
            "xfs": f"mkfs.xfs -f {'-L ' + shlex.quote(label) if label else ''} -- {quoted}",
            "btrfs": f"mkfs.btrfs -f {'-L ' + shlex.quote(label) if label else ''} -- {quoted}",
            "exfat": f"mkfs.exfat {'-n ' + shlex.quote(label) if label else ''} {quoted}",
            "ntfs": f"mkfs.ntfs -F -Q {'-L ' + shlex.quote(label) if label else ''} -- {quoted}",
        }

    @classmethod
    def _destructive_guard(
        cls,
        device: str,
        require_disk: bool = False,
    ) -> str:
        quoted = shlex.quote(device)
        script = cls._device_guard(device, require_disk=require_disk)
        script += (
            f"resolved=$(readlink -f -- {quoted}); "
            + "if lsblk -nrpo MOUNTPOINTS -- \"$resolved\" | "
            + "grep -Eq '(^|[[:space:]])(/|/boot|/boot/efi)([[:space:]]|$)|/boot/'; then "
            + "echo 'Opération refusée : disque système ou de démarrage.'; exit 90; fi; "
            + "for node in $(lsblk -nrpo NAME -- \"$resolved\"); do "
            + "if swapon --show=NAME --noheadings | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' | grep -Fxq \"$node\"; then "
            + "echo 'Opération refusée : swap actif sur le disque ou une partition.'; exit 90; fi; done; "
            + "if lsblk -nrpo TYPE -- \"$resolved\" | grep -Eq '^(crypt|lvm|raid.*|md|mpath)$'; then "
            + "echo 'Opération refusée : LVM, chiffrement, RAID ou multipath détecté.'; exit 90; fi; "
            + "for node in $(lsblk -nrpo NAME -- \"$resolved\"); do "
            + "base=$(basename \"$node\"); holders=/sys/class/block/$base/holders; "
            + "if [ -d \"$holders\" ] && [ -n \"$(ls -A \"$holders\" 2>/dev/null)\" ]; then "
            + "echo 'Opération refusée : le périphérique est utilisé par une autre couche.'; exit 90; fi; done; "
        )
        return script

    @classmethod
    def _protected_mount_guard(cls, device: str) -> str:
        quoted = shlex.quote(device)
        return (
            cls._device_guard(device)
            + f"resolved=$(readlink -f -- {quoted}); "
            + "for node in $(lsblk -nrpo NAME -- \"$resolved\"); do "
            + "for target in $(findmnt -rn -S \"$node\" -o TARGET 2>/dev/null || true); do "
            + "case \"$target\" in /|/boot|/boot/*|/dev|/dev/*|/etc|/etc/*|"
            + "/proc|/proc/*|/run|/run/*|/sys|/sys/*|/usr|/usr/*|/var|/var/*) "
            + "echo 'Opération refusée : volume système protégé.'; exit 90;; esac; "
            + "done; done; "
        )

    @staticmethod
    def _device_guard(device: str, require_disk: bool = False) -> str:
        quoted = shlex.quote(device)
        type_check = (
            'type=$(lsblk -ndo TYPE -- "$resolved" 2>/dev/null | head -n1); '
            '[ "$type" = disk ] || { echo "La cible n’est pas un disque entier."; exit 89; }; '
            if require_disk
            else ""
        )
        return (
            "set -eu; "
            + f"resolved=$(readlink -f -- {quoted}); "
            + '[ -n "$resolved" ] && [ "${resolved#/dev/}" != "$resolved" ] && [ -b "$resolved" ] || '
            + "{ echo 'Périphérique bloc invalide.'; exit 88; }; "
            + type_check
        )

    @staticmethod
    def _require_type(device: str, expected: str) -> str:
        return (
            f"resolved=$(readlink -f -- {shlex.quote(device)}); "
            + 'type=$(lsblk -ndo TYPE -- "$resolved" 2>/dev/null | head -n1); '
            + f'[ "$type" = {shlex.quote(expected)} ] || '
            + f"{{ echo 'Type de périphérique incorrect : {expected} requis.'; exit 89; }}; "
        )

    @staticmethod
    def _unmount_if_needed(device: str) -> str:
        quoted = shlex.quote(device)
        return (
            f"resolved=$(readlink -f -- {quoted}); "
            + "if findmnt -rn -S \"$resolved\" >/dev/null; then "
            + "umount -- \"$resolved\" || { echo 'Démontage impossible : opération annulée.'; exit 92; }; fi; "
        )

    @staticmethod
    def _fstab_backup_script() -> str:
        return (
            "stamp=$(date +%Y%m%d-%H%M%S); "
            "mkdir -p /var/backups/remote-linux/fstab; "
            "fstab_backup=/var/backups/remote-linux/fstab/fstab-$stamp; "
            "cp -a -- /etc/fstab \"$fstab_backup\"; "
        )

    @staticmethod
    def _prepare_fstab_cleanup_script(device: str) -> str:
        quoted = shlex.quote(device)
        return (
            f"old_uuid=$(blkid -s UUID -o value -- {quoted} 2>/dev/null || true); "
            + "fstab_tmp=$(mktemp); "
            + f'awk -v dev={quoted} -v uuid="$old_uuid" '
            + '\'BEGIN{u=(uuid=="" ? "__NONE__" : "UUID=" uuid)} '
            + '/^[[:space:]]*#/ || NF==0 {print; next} '
            + '$1==dev || $1==u {next} {print}\' /etc/fstab > "$fstab_tmp"; '
        )

    @staticmethod
    def _commit_fstab_cleanup_script() -> str:
        return (
            'cp -- "$fstab_tmp" /etc/fstab; '
            'rm -f -- "$fstab_tmp"; '
        )

    @staticmethod
    def _safe_device(value: str) -> str:
        path = PurePosixPath(value)
        if not path.is_absolute() or len(path.parts) < 3 or path.parts[1] != "dev":
            raise ValueError("Périphérique Linux invalide.")
        for part in path.parts[2:]:
            if part in {"", ".", ".."} or not re.fullmatch(
                r"[A-Za-z0-9._:+-]+",
                part,
            ):
                raise ValueError("Périphérique Linux invalide.")
        return str(path)

    @staticmethod
    def _safe_mountpoint(value: str, allow_empty: bool = False) -> str:
        if allow_empty and not value:
            return ""
        path = PurePosixPath(value)
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError("Point de montage invalide.")
        normalized = str(path)
        protected_roots = {
            "boot", "dev", "etc", "proc", "run", "sys", "usr", "var"
        }
        if (
            normalized == "/"
            or len(path.parts) < 2
            or path.parts[1] in protected_roots
            or any(
                part in {"", ".", ".."}
                or not re.fullmatch(r"[A-Za-z0-9._+-]+", part)
                for part in path.parts[1:]
            )
        ):
            raise ValueError("Point de montage invalide.")
        return normalized

    @staticmethod
    def _safe_label(value: str) -> str:
        if len(value) > 32 or any(char in value for char in "\r\n\0"):
            raise ValueError("Nom de volume invalide.")
        return value

    @staticmethod
    def _partition_number(device: str) -> int:
        match = re.search(r"(?:p)?(\d+)$", device)
        if not match:
            raise ValueError("Numéro de partition introuvable.")
        return int(match.group(1))

    @staticmethod
    def _parent_device(values: dict) -> str:
        pkname = str(values.get("pkname") or "")
        if pkname:
            return f"/dev/{pkname}"
        path = str(values.get("path") or "")
        return re.sub(r"p?\d+$", "", path)
