import json
import logging
import subprocess
from dataclasses import dataclass


logger = logging.getLogger(__name__)


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

    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        # An lsblk too old for -J (or one that printed a warning ahead of the
        # JSON) must leave the app running with no devices listed, not take
        # the window down with it.
        logger.warning("lsblk did not return usable JSON; no devices listed")
        return []
    if not isinstance(data, dict):
        return []

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


EJECT_TIMEOUT_SECONDS = 60


def eject_device(device: StorageDevice) -> tuple[bool, str]:
    """Safely unmount the partition and power off the underlying disk,
    equivalent to a desktop environment's "Eject" action."""
    try:
        result = subprocess.run(
            ["udisksctl", "unmount", "-b", device.path],
            capture_output=True,
            text=True,
            timeout=EJECT_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        return False, "udisksctl not found"
    except subprocess.TimeoutExpired:
        return False, "unmount timed out (a polkit prompt may be waiting for authorization)"
    if result.returncode != 0:
        return False, result.stderr.strip() or result.stdout.strip() or "unmount failed"

    # The unmount above already succeeded -- the device is safe to remove
    # even if power-off below stalls or fails, so those cases still report
    # overall success (with a warning message) instead of blocking forever.
    try:
        result = subprocess.run(
            ["udisksctl", "power-off", "-b", device.disk_path],
            capture_output=True,
            text=True,
            timeout=EJECT_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        return True, ""
    except subprocess.TimeoutExpired:
        return True, "power-off timed out (a polkit prompt may be waiting for authorization)"
    if result.returncode != 0:
        return True, result.stderr.strip() or result.stdout.strip()

    return True, ""
