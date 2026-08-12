import json
import subprocess
from dataclasses import dataclass


@dataclass
class StorageDevice:
    name: str
    path: str
    mountpoint: str
    label: str
    size: str
    removable: bool
    disk_path: str


def list_storage_devices() -> list[StorageDevice]:
    try:
        output = subprocess.run(
            ["lsblk", "-J", "-o", "NAME,PATH,MOUNTPOINT,RM,TYPE,SIZE,LABEL,HOTPLUG"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []

    data = json.loads(output)
    devices: list[StorageDevice] = []
    for disk_node in data.get("blockdevices", []):
        _collect(disk_node, devices, disk_path=disk_node.get("path", ""))
    return devices


def _collect(node: dict, devices: list[StorageDevice], disk_path: str) -> None:
    mountpoint = node.get("mountpoint")
    node_type = node.get("type")
    removable = bool(node.get("rm")) or bool(node.get("hotplug"))
    if mountpoint and node_type in ("part", "disk") and removable:
        devices.append(
            StorageDevice(
                name=node.get("name", ""),
                path=node.get("path", ""),
                mountpoint=mountpoint,
                label=node.get("label") or "",
                size=node.get("size") or "",
                removable=removable,
                disk_path=disk_path,
            )
        )
    for child in node.get("children", []) or []:
        _collect(child, devices, disk_path=disk_path)


def eject_device(device: StorageDevice) -> tuple[bool, str]:
    """Safely unmount the partition and power off the underlying disk,
    equivalent to a desktop environment's "Eject" action."""
    try:
        result = subprocess.run(
            ["udisksctl", "unmount", "-b", device.path],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return False, "udisksctl not found"
    if result.returncode != 0:
        return False, result.stderr.strip() or result.stdout.strip() or "unmount failed"

    try:
        result = subprocess.run(
            ["udisksctl", "power-off", "-b", device.disk_path],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return True, ""
    if result.returncode != 0:
        return True, result.stderr.strip() or result.stdout.strip()

    return True, ""
