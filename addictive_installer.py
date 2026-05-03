#!/usr/bin/env python3
"""Addictive OS installer (Textual + archinstall).

This is a real installer that drives archinstall directly. It is designed
for the Arch ISO environment and will modify disks.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import (
    Button,
    Checkbox,
    Footer,
    Header,
    Input,
    Log,
    ProgressBar,
    Select,
    SelectionList,
    Static,
)

ACCENT = "#0FD29B"
WALLPAPER_SOURCE_DIR = Path("/etc/addictive/wallpapers")
WALLPAPER_TARGET_DIR = Path("/etc/addictive/wallpapers")

BLACKARCH_TOOLSETS = {
    "Web": "blackarch-webapp",
    "Reverse Engineering": "blackarch-reversing",
    "OSINT": "blackarch-osint",
    "Crypto": "blackarch-crypto",
    "Exploitation": "blackarch-exploitation",
    "Forensics": "blackarch-forensic",
    "Networking": "blackarch-networking",
    "Wireless": "blackarch-wireless",
    "Malware": "blackarch-malware",
    "Scanner": "blackarch-scanner",
}

FILESYSTEM_OPTIONS = ["btrfs", "ext4", "xfs"]

KEYBOARD_LAYOUTS = [
    "us",
    "uk",
    "de",
    "fr",
    "es",
    "it",
    "pt",
    "pt-br",
    "ru",
    "pl",
    "tr",
    "nl",
    "sv",
    "no",
    "fi",
]


@dataclass
class InstallerState:
    keyboard_layouts: list[str] = field(default_factory=lambda: ["us"])
    wifi_ssid: str = ""
    wifi_password: str = ""
    wired_connected: bool = False
    wired_ifaces: list[str] = field(default_factory=list)
    use_ethernet: bool | None = None
    toolkits: list[str] = field(default_factory=list)
    disk_mode: str = "auto"
    disk_device: str = ""
    filesystem: str = "btrfs"
    encrypt: bool = False
    luks_password: str = ""
    manual_root: str = ""
    manual_boot: str = ""
    manual_home: str = ""
    manual_format_root: bool = True
    manual_format_boot: bool = False
    manual_format_home: bool = False
    hostname: str = "addictive"
    username: str = "addict"
    password: str = ""
    sudo: bool = True
    timezone: str = "UTC"


@dataclass
class DiskInfo:
    path: str
    size: str
    model: str


@dataclass
class PartitionInfo:
    path: str
    size: str
    fstype: str
    mountpoint: str


@dataclass
class WifiNetwork:
    ssid: str
    security: str
    signal: str


def run_cmd(
    cmd: list[str],
    check: bool = True,
    timeout: int | None = None,
) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, text=True, capture_output=True, check=check, timeout=timeout)


def is_uefi() -> bool:
    return Path("/sys/firmware/efi").exists()


def nmcli_available() -> bool:
    return shutil.which("nmcli") is not None


def wired_connected() -> bool:
    return bool(wired_ifaces())


def wired_ifaces() -> list[str]:
    if not nmcli_available():
        return wired_ifaces_sysfs()
    try:
        result = run_cmd(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "dev"], check=False)
    except Exception:
        return wired_ifaces_sysfs()
    ifaces: list[str] = []
    for line in result.stdout.splitlines():
        parts = line.split(":")
        if len(parts) != 3:
            continue
        dev, dev_type, state = parts
        if dev_type == "ethernet" and state == "connected":
            ifaces.append(dev)
    ifaces += [i for i in wired_ifaces_sysfs() if i not in ifaces]
    return ifaces


def wired_ifaces_sysfs() -> list[str]:
    sys_class = Path("/sys/class/net")
    if not sys_class.exists():
        return []
    ifaces: list[str] = []
    for iface in sorted(sys_class.iterdir()):
        name = iface.name
        if name == "lo":
            continue
        if (iface / "wireless").exists():
            continue
        carrier = iface / "carrier"
        operstate = iface / "operstate"
        type_path = iface / "type"
        try:
            if type_path.exists() and type_path.read_text().strip() != "1":
                continue
            has_carrier = carrier.exists() and carrier.read_text().strip() == "1"
            is_up = operstate.exists() and operstate.read_text().strip() == "up"
            if has_carrier or is_up:
                ifaces.append(name)
        except OSError:
            continue
    return ifaces


def list_wifi_networks() -> list[WifiNetwork]:
    if not nmcli_available():
        return []
    result = run_cmd(
        ["nmcli", "-t", "-f", "SSID,SECURITY,SIGNAL", "dev", "wifi", "list", "--rescan", "yes"],
        check=False,
    )
    networks: dict[str, WifiNetwork] = {}
    for line in result.stdout.splitlines():
        parts = line.split(":")
        if len(parts) < 3:
            continue
        ssid = parts[0].strip()
        security = parts[1].strip() or "open"
        signal = parts[2].strip()
        if not ssid:
            continue
        if ssid not in networks:
            networks[ssid] = WifiNetwork(ssid=ssid, security=security, signal=signal)
    return sorted(networks.values(), key=lambda n: n.signal, reverse=True)


def connect_wifi(ssid: str, password: str) -> tuple[bool, str]:
    if not nmcli_available():
        return False, "nmcli is not available"
    cmd = ["nmcli", "dev", "wifi", "connect", ssid]
    if password:
        cmd += ["password", password]
    result = run_cmd(cmd, check=False)
    if result.returncode == 0:
        return True, "Connected"
    err = result.stderr.strip() or result.stdout.strip() or "Connection failed"
    return False, err


def list_disks() -> list[DiskInfo]:
    result = run_cmd(["lsblk", "-J", "-o", "NAME,PATH,SIZE,TYPE,MODEL"], check=False)
    if result.returncode != 0:
        return []
    data = json.loads(result.stdout)
    disks: list[DiskInfo] = []
    for dev in data.get("blockdevices", []):
        if dev.get("type") != "disk":
            continue
        disks.append(
            DiskInfo(
                path=dev.get("path", ""),
                size=dev.get("size", ""),
                model=(dev.get("model") or "").strip() or "Unknown",
            )
        )
    return disks


def list_partitions(disk_path: str) -> list[PartitionInfo]:
    result = run_cmd(["lsblk", "-J", "-o", "NAME,PATH,SIZE,TYPE,FSTYPE,MOUNTPOINT", disk_path], check=False)
    if result.returncode != 0:
        return []
    data = json.loads(result.stdout)
    parts: list[PartitionInfo] = []
    for dev in data.get("blockdevices", []):
        for child in dev.get("children", []) or []:
            if child.get("type") != "part":
                continue
            parts.append(
                PartitionInfo(
                    path=child.get("path", ""),
                    size=child.get("size", ""),
                    fstype=child.get("fstype") or "unknown",
                    mountpoint=child.get("mountpoint") or "",
                )
            )
    return parts


def lsblk_tree(disk_path: str) -> dict:
    columns = "NAME,PATH,SIZE,TYPE,FSTYPE,MOUNTPOINT,MOUNTPOINTS"
    result = run_cmd(["lsblk", "-J", "-o", columns, disk_path], check=False)
    if result.returncode != 0:
        result = run_cmd(["lsblk", "-J", "-o", "NAME,PATH,SIZE,TYPE,FSTYPE,MOUNTPOINT", disk_path], check=False)
    if result.returncode != 0:
        return {}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}


def iter_lsblk_nodes(node: dict) -> list[dict]:
    nodes = [node]
    for child in node.get("children", []) or []:
        nodes.extend(iter_lsblk_nodes(child))
    return nodes


def node_mountpoints(node: dict) -> list[str]:
    mountpoints = []
    if isinstance(node.get("mountpoints"), list):
        mountpoints.extend([m for m in node.get("mountpoints") if m])
    if node.get("mountpoint"):
        mountpoints.append(node["mountpoint"])
    return mountpoints


def force_wipefs_for_disk(disk_path: str, log: Callable[[str], None]) -> None:
    log(f"Force wiping signatures on {disk_path}")
    run_cmd(["wipefs", "--all", "--force", disk_path], check=False)
    for part in list_partitions(disk_path):
        run_cmd(["wipefs", "--all", "--force", part.path], check=False)
    settle_block_devices(disk_path, log)


def prepare_disk_for_installation(state: InstallerState, log: Callable[[str], None]) -> None:
    if state.disk_mode != "auto":
        targets = [p for p in [state.manual_root, state.manual_boot, state.manual_home] if p]
        for path in targets:
            run_cmd(["swapoff", path], check=False)
            safe_unmount(path, log)
        settle_block_devices(state.disk_device, log)
        return

    tree = lsblk_tree(state.disk_device)
    if not tree:
        return

    critical_mounts = {"/", "/boot", "/boot/efi", "/efi"}

    for dev in tree.get("blockdevices", []):
        if dev.get("path") != state.disk_device:
            continue
        for node in iter_lsblk_nodes(dev):
            for mountpoint in node_mountpoints(node):
                if mountpoint in critical_mounts:
                    raise RuntimeError(
                        "Selected disk appears to be in use by the running system. "
                        "Boot from the live ISO and try again."
                    )
                if mountpoint:
                    log(f"Unmounting {mountpoint}")
                    safe_unmount(mountpoint, log)

            if node.get("fstype") in ("swap", "linux-swap"):
                path = node.get("path") or node.get("name")
                if path:
                    log(f"Swapoff {path}")
                    run_cmd(["swapoff", path], check=False)

            if node.get("type") == "crypt" and node.get("name"):
                log(f"Closing LUKS mapping {node['name']}")
                run_cmd(["cryptsetup", "close", node["name"]], check=False)
    settle_block_devices(state.disk_device, log)


def settle_block_devices(disk_path: str, log: Callable[[str], None]) -> None:
    log(f"Settling block devices on {disk_path}")
    run_cmd(["partprobe", disk_path], check=False)
    run_cmd(["udevadm", "settle"], check=False)
    run_cmd(["lsblk", "-f", disk_path], check=False)


def safe_unmount(mountpoint: str, log: Callable[[str], None]) -> None:
    try:
        result = run_cmd(["umount", "-R", mountpoint], check=False, timeout=3)
        if result.returncode == 0:
            return
        err = (result.stderr or result.stdout or "").strip()
        if err:
            log(f"Unmount failed for {mountpoint}: {err}")
    except subprocess.TimeoutExpired:
        log(f"Unmount timed out for {mountpoint}; trying lazy unmount")

    try:
        result = run_cmd(["umount", "-R", "-l", mountpoint], check=False, timeout=3)
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "").strip()
            if err:
                log(f"Lazy unmount failed for {mountpoint}: {err}")
    except subprocess.TimeoutExpired:
        log(f"Lazy unmount timed out for {mountpoint}; continuing")


def ensure_device_link(arch_path: Path, selected_path: Path | None, log: Callable[[str], None]) -> None:
    if arch_path.exists():
        return

    source = selected_path if selected_path and selected_path.exists() else None
    if source is None:
        disks = list_disks()
        if len(disks) == 1:
            source = Path(disks[0].path)

    if source and source.exists():
        try:
            if arch_path.is_symlink() or arch_path.exists():
                arch_path.unlink(missing_ok=True)
            log(f"Creating device alias {arch_path} -> {source}")
            os.symlink(source, arch_path)
        except OSError as exc:
            log(f"Failed to create device alias: {exc}")


def list_timezones() -> dict[str, list[str]]:
    zone_root = Path("/usr/share/zoneinfo")
    if not zone_root.exists():
        return {"UTC": ["UTC"]}
    regions: dict[str, list[str]] = {}
    for region in sorted(zone_root.iterdir()):
        if not region.is_dir():
            continue
        if region.name in {"posix", "right", "SystemV", "Etc"}:
            continue
        city_list: list[str] = []
        for city in sorted(region.rglob("*")):
            if city.is_dir():
                continue
            rel = city.relative_to(region)
            if rel.name.startswith("."):
                continue
            city_list.append(str(rel))
        if city_list:
            regions[region.name] = city_list
    if not regions:
        regions["UTC"] = ["UTC"]
    return regions


def sanitize_username(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "", name)


def selection_values(selection: SelectionList) -> list[str]:
    values: list[str] = []
    for item in selection.selected:
        if hasattr(item, "value"):
            values.append(str(item.value))
        else:
            values.append(str(item))
    return values


class WizardScreen(Screen):
    screen_title = ""
    show_back = True
    next_label = "Next"

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with VerticalScroll(id="body"):
            yield Static(self.screen_title or "", id="screen-title")
            yield from self.compose_body()
            yield Static("", id="status")
        with Horizontal(id="nav"):
            if self.show_back:
                yield Button("Back", id="back")
            yield Button(self.next_label, id="next", classes="primary")
        yield Footer()

    def compose_body(self) -> ComposeResult:
        yield Static("")

    def set_status(self, message: str) -> None:
        status = self.query_one("#status", Static)
        status.update(message)

    def validate(self) -> str | None:
        return None

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "back":
            self.app.pop_screen()
            return
        if event.button.id == "next":
            error = self.validate()
            if error:
                self.set_status(error)
                return
            self.set_status("")
            self.on_next()

    def on_next(self) -> None:
        self.app.push_screen(WelcomeScreen())


class WelcomeScreen(WizardScreen):
    screen_title = "Welcome to Addictive"
    show_back = False
    next_label = "Let\'s go"

    def compose_body(self) -> ComposeResult:
        yield Static(
            "Addictive is a low-cortisol, no-drama penetration testing OS built on Arch with BlackArch and GNOME."
        )
        yield Static("This installer is fully functional and will install to disk. Take a breath, read the prompts, and you\'ll be fine.")

    def on_next(self) -> None:
        self.app.push_screen(KeyboardScreen())


class KeyboardScreen(WizardScreen):
    screen_title = "Keyboard layouts"

    def compose_body(self) -> ComposeResult:
        yield Static("Pick one or more keyboard layouts. You can add extras below if needed.")
        layouts = SelectionList(*[(layout, layout) for layout in KEYBOARD_LAYOUTS])
        layouts.id = "layouts"
        yield layouts
        yield Input(placeholder="Extra layouts (comma-separated, e.g. us,ru,de)", id="extra")

    def validate(self) -> str | None:
        selected = selection_values(self.query_one("#layouts", SelectionList))
        extra = self.query_one("#extra", Input).value.strip()
        if not selected and not extra:
            return "Select at least one layout."
        return None

    def on_next(self) -> None:
        selected = selection_values(self.query_one("#layouts", SelectionList))
        extra_raw = self.query_one("#extra", Input).value.strip()
        extra = [e.strip() for e in extra_raw.split(",") if e.strip()] if extra_raw else []
        layouts = []
        for layout in selected + extra:
            if layout not in layouts:
                layouts.append(layout)
        self.app.state.keyboard_layouts = layouts or ["us"]
        self.app.push_screen(NetworkScreen())


class NetworkScreen(WizardScreen):
    screen_title = "Network"

    def on_mount(self) -> None:
        self.app.state.wired_ifaces = wired_ifaces()
        self.app.state.wired_connected = bool(self.app.state.wired_ifaces)
        if self.app.state.use_ethernet is None:
            self.app.state.use_ethernet = self.app.state.wired_connected
        self.refresh_wifi()

    def compose_body(self) -> ComposeResult:
        yield Static("If you\'re on Ethernet, tick the checkbox and skip Wi-Fi. If not, connect below.")
        yield Static("", id="wired")
        yield Checkbox("Use Ethernet (skip Wi-Fi)", id="use_ethernet")
        yield Select([], prompt="Select Wi-Fi network", id="wifi_list")
        yield Input(placeholder="Wi-Fi SSID", id="ssid")
        yield Input(placeholder="Wi-Fi password (leave blank for open networks)", password=True, id="wifi_pass")
        yield Button("Rescan Wi-Fi", id="rescan")

    def refresh_wifi(self) -> None:
        self.app.state.wired_ifaces = wired_ifaces()
        self.app.state.wired_connected = bool(self.app.state.wired_ifaces)
        wifi_list = self.query_one("#wifi_list", Select)
        networks = list_wifi_networks()
        options = []
        for net in networks:
            label = f"{net.ssid}  ({net.security}, {net.signal}%)"
            options.append((label, net.ssid))
        wifi_list.options = options
        wired_label = self.query_one("#wired", Static)
        if self.app.state.wired_connected:
            iface_list = ", ".join(self.app.state.wired_ifaces)
            wired_label.update(f"Wired connection detected on: {iface_list}.")
        else:
            wired_label.update("No wired connection detected. If this is wrong, tick 'Use Ethernet' and continue.")

        use_ethernet = self.query_one("#use_ethernet", Checkbox)
        use_ethernet.value = bool(self.app.state.use_ethernet)
        self.set_wifi_enabled(not use_ethernet.value)

    def set_wifi_enabled(self, enabled: bool) -> None:
        self.query_one("#wifi_list", Select).disabled = not enabled
        self.query_one("#ssid", Input).disabled = not enabled
        self.query_one("#wifi_pass", Input).disabled = not enabled
        self.query_one("#rescan", Button).disabled = not enabled

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "wifi_list" and event.value:
            self.query_one("#ssid", Input).value = str(event.value)

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if event.checkbox.id == "use_ethernet":
            self.app.state.use_ethernet = event.value
            self.set_wifi_enabled(not event.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "rescan":
            self.refresh_wifi()
            return
        super().on_button_pressed(event)

    def validate(self) -> str | None:
        use_ethernet = self.query_one("#use_ethernet", Checkbox).value
        if use_ethernet:
            return None
        ssid = self.query_one("#ssid", Input).value.strip()
        if not ssid:
            return "Enter an SSID or connect Ethernet."
        return None

    def on_next(self) -> None:
        use_ethernet = self.query_one("#use_ethernet", Checkbox).value
        if not use_ethernet:
            ssid = self.query_one("#ssid", Input).value.strip()
            password = self.query_one("#wifi_pass", Input).value
            ok, msg = connect_wifi(ssid, password)
            if not ok:
                self.set_status(msg)
                return
            self.app.state.wifi_ssid = ssid
            self.app.state.wifi_password = password
        self.app.push_screen(ToolkitsScreen())


class ToolkitsScreen(WizardScreen):
    screen_title = "BlackArch toolkits"

    def compose_body(self) -> ComposeResult:
        yield Static("Pick the toolsets you want installed. You can always add more later.")
        toolsets = SelectionList(*[(name, name) for name in BLACKARCH_TOOLSETS.keys()])
        toolsets.id = "toolsets"
        yield toolsets

    def on_next(self) -> None:
        selected = selection_values(self.query_one("#toolsets", SelectionList))
        self.app.state.toolkits = selected
        self.app.push_screen(DiskScreen())


class DiskScreen(WizardScreen):
    screen_title = "Disk setup"

    def compose_body(self) -> ComposeResult:
        yield Static("Choose a disk and partitioning style. Auto will wipe the disk.")
        disks = list_disks()
        disk_options = [(f"{d.path}  ({d.size}, {d.model})", d.path) for d in disks]
        yield Select([("Automatic (recommended)", "auto"), ("Manual", "manual")], value="auto", id="mode")
        yield Select(disk_options, prompt="Select disk", id="disk")
        yield Select([(fs, fs) for fs in FILESYSTEM_OPTIONS], value="btrfs", id="fs")
        yield Checkbox("Enable disk encryption (LUKS)", id="encrypt")
        yield Input(placeholder="Encryption password", password=True, id="luks")
        yield Input(placeholder="Confirm encryption password", password=True, id="luks_confirm")

    def validate(self) -> str | None:
        disk = self.query_one("#disk", Select).value
        if not disk:
            return "Select a disk to install to."
        encrypt = self.query_one("#encrypt", Checkbox).value
        if encrypt:
            p1 = self.query_one("#luks", Input).value
            p2 = self.query_one("#luks_confirm", Input).value
            if not p1:
                return "Encryption password is required."
            if p1 != p2:
                return "Encryption passwords do not match."
        return None

    def on_next(self) -> None:
        self.app.state.disk_mode = str(self.query_one("#mode", Select).value)
        self.app.state.disk_device = str(self.query_one("#disk", Select).value)
        self.app.state.filesystem = str(self.query_one("#fs", Select).value)
        self.app.state.encrypt = bool(self.query_one("#encrypt", Checkbox).value)
        self.app.state.luks_password = self.query_one("#luks", Input).value
        if self.app.state.disk_mode == "manual":
            self.app.push_screen(ManualPartitionScreen())
        else:
            self.app.push_screen(AccountScreen())


class ManualPartitionScreen(WizardScreen):
    screen_title = "Manual partitions"

    def compose_body(self) -> ComposeResult:
        yield Static("Select existing partitions. Formatting will wipe the chosen partition.")
        parts = list_partitions(self.app.state.disk_device)
        options = [(f"{p.path}  ({p.size}, {p.fstype})", p.path) for p in parts]
        yield Select(options, prompt="Root partition", id="root")
        yield Select([("(none)", ""), *options], prompt="Boot/ESP partition", id="boot")
        yield Select([("(none)", ""), *options], prompt="Home partition", id="home")
        yield Checkbox("Format root partition", value=True, id="fmt_root")
        yield Checkbox("Format boot partition", value=False, id="fmt_boot")
        yield Checkbox("Format home partition", value=False, id="fmt_home")

    def validate(self) -> str | None:
        root = self.query_one("#root", Select).value
        if not root:
            return "Root partition is required."
        boot = self.query_one("#boot", Select).value
        if is_uefi() and not boot:
            return "UEFI system detected. Boot/ESP partition is required."
        if self.app.state.encrypt:
            fmt_root = self.query_one("#fmt_root", Checkbox).value
            if not fmt_root:
                return "Encryption requires formatting the root partition."
        return None

    def on_next(self) -> None:
        self.app.state.manual_root = str(self.query_one("#root", Select).value)
        self.app.state.manual_boot = str(self.query_one("#boot", Select).value)
        self.app.state.manual_home = str(self.query_one("#home", Select).value)
        self.app.state.manual_format_root = bool(self.query_one("#fmt_root", Checkbox).value)
        self.app.state.manual_format_boot = bool(self.query_one("#fmt_boot", Checkbox).value)
        self.app.state.manual_format_home = bool(self.query_one("#fmt_home", Checkbox).value)
        self.app.push_screen(AccountScreen())


class AccountScreen(WizardScreen):
    screen_title = "Account creation"

    def compose_body(self) -> ComposeResult:
        yield Static("Set up your main user and hostname.")
        yield Input(placeholder="Hostname", value=self.app.state.hostname, id="hostname")
        yield Input(placeholder="Username", value=self.app.state.username, id="username")
        yield Input(placeholder="Password", password=True, id="password")
        yield Input(placeholder="Confirm password", password=True, id="password_confirm")
        yield Checkbox("Grant this user sudo (recommended)", value=True, id="sudo")

    def validate(self) -> str | None:
        hostname = self.query_one("#hostname", Input).value.strip()
        username = self.query_one("#username", Input).value.strip()
        password = self.query_one("#password", Input).value
        confirm = self.query_one("#password_confirm", Input).value
        if not hostname:
            return "Hostname is required."
        if not username:
            return "Username is required."
        if username != sanitize_username(username):
            return "Username contains invalid characters."
        if not password:
            return "Password is required."
        if password != confirm:
            return "Passwords do not match."
        return None

    def on_next(self) -> None:
        self.app.state.hostname = self.query_one("#hostname", Input).value.strip()
        self.app.state.username = self.query_one("#username", Input).value.strip()
        self.app.state.password = self.query_one("#password", Input).value
        self.app.state.sudo = bool(self.query_one("#sudo", Checkbox).value)
        self.app.push_screen(TimezoneScreen())


class TimezoneScreen(WizardScreen):
    screen_title = "Timezone"

    def on_mount(self) -> None:
        self.regions = list_timezones()
        self._init_timezone_selects()

    def compose_body(self) -> ComposeResult:
        yield Static("Pick your region and timezone.")
        yield Select([], prompt="Region", id="region")
        yield Select([], prompt="Timezone", id="city")

    def _init_timezone_selects(self) -> None:
        region_select = self.query_one("#region", Select)
        region_options = [(r, r) for r in sorted(self.regions.keys())]
        region_select.set_options(region_options)
        if region_options:
            region_select.value = region_options[0][1]
            self.refresh_cities(str(region_select.value))

    def refresh_cities(self, region: str) -> None:
        city_select = self.query_one("#city", Select)
        cities = self.regions.get(region, ["UTC"])
        city_options = [(c, c) for c in cities]
        city_select.set_options(city_options)
        if city_options:
            city_select.value = city_options[0][1]

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "region" and event.value:
            self.refresh_cities(str(event.value))

    def on_next(self) -> None:
        region = str(self.query_one("#region", Select).value)
        city = str(self.query_one("#city", Select).value)
        self.app.state.timezone = f"{region}/{city}" if region != "UTC" else "UTC"
        self.app.push_screen(ReviewScreen())


class ReviewScreen(WizardScreen):
    screen_title = "Review & install"
    next_label = "Install"

    def compose_body(self) -> ComposeResult:
        summary = self.build_summary()
        yield Static(summary, id="summary")
        yield Static("When you hit Install, disks will be modified. This is your last calm moment.")

    def build_summary(self) -> str:
        s = self.app.state
        lines = [
            f"Hostname: {s.hostname}",
            f"User: {s.username} (sudo: {'yes' if s.sudo else 'no'})",
            f"Keyboard layouts: {', '.join(s.keyboard_layouts)}",
            f"Timezone: {s.timezone}",
            f"Disk mode: {s.disk_mode}",
            f"Disk: {s.disk_device}",
            f"Filesystem: {s.filesystem}",
            f"Encryption: {'enabled' if s.encrypt else 'disabled'}",
            f"Toolkits: {', '.join(s.toolkits) if s.toolkits else 'none'}",
        ]
        if s.disk_mode == "manual":
            lines.append(f"Manual root: {s.manual_root}")
            lines.append(f"Manual boot: {s.manual_boot or '(none)'}")
            lines.append(f"Manual home: {s.manual_home or '(none)'}")
        return "\n".join(lines)

    def on_next(self) -> None:
        self.app.push_screen(InstallScreen())


class InstallScreen(Screen):
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container():
            yield Static("Installing Addictive. This can take a while.")
            yield Static("Step 0/0: starting", id="progress_label")
            yield ProgressBar(total=1, id="progress")
            yield Log(id="install_log")
        yield Footer()

    def on_mount(self) -> None:
        self.run_install()

    def write_log(self, message: str) -> None:
        log_widget = self.query_one("#install_log", Log)
        if not message.endswith("\n"):
            message += "\n"
        self.app.call_from_thread(log_widget.write, message)

    def set_progress(self, current: int, total: int, label: str) -> None:
        def _update() -> None:
            progress_label = self.query_one("#progress_label", Static)
            progress_label.update(f"Step {current}/{total}: {label}")
            progress_bar = self.query_one("#progress", ProgressBar)
            progress_bar.update(total=total, progress=current)

        self.app.call_from_thread(_update)

    @work(thread=True, exclusive=True)
    def run_install(self) -> None:
        def log(msg: str) -> None:
            self.write_log(msg)

        try:
            perform_installation(self.app.state, log, self.set_progress)
            log("Install complete. You can reboot when ready.")
        except Exception as exc:
            log(f"Install failed: {exc}")


class AddictiveInstallerApp(App):
    CSS = f"""
    Screen {{
        background: #0B0F0E;
        color: #E6F6F2;
    }}

    #screen-title {{
        color: {ACCENT};
        text-style: bold;
        margin: 1 0 1 0;
    }}

    #body {{
        padding: 1 2;
    }}

    #nav {{
        height: 3;
        padding: 0 2;
        background: #0E1413;
    }}

    Button.primary {{
        background: {ACCENT};
        color: #0B0F0E;
    }}

    #status {{
        color: #FFB86C;
        margin-top: 1;
    }}

    #summary {{
        background: #0E1413;
        padding: 1 2;
        border: tall #1C2C28;
    }}
    """

    TITLE = "Addictive Installer"

    def __init__(self) -> None:
        super().__init__()
        self.state = InstallerState()

    def on_mount(self) -> None:
        self.push_screen(WelcomeScreen())


def build_disk_config(state: InstallerState, log: Callable[[str], None]):
    from archinstall.lib.disk.device_handler import device_handler
    from archinstall.lib.models.device import (
        DeviceModification,
        DiskEncryption,
        DiskLayoutConfiguration,
        DiskLayoutType,
        EncryptionType,
        FilesystemType,
        ModificationStatus,
        PartitionFlag,
        PartitionModification,
        PartitionType,
        Size,
        SubvolumeModification,
        Unit,
    )
    from archinstall.lib.models.users import Password

    device_path = Path(state.disk_device)
    if device_path.exists():
        settle_block_devices(state.disk_device, log)
    device_handler.load_devices()
    log("archinstall detected disks: " + ", ".join(str(d.device_info.path) for d in device_handler.devices))
    device = device_handler.get_device(device_path)
    if not device:
        log("Selected disk not found by archinstall, rescanning devices")
        settle_block_devices(state.disk_device, log)
        device_handler.load_devices()
        device = device_handler.get_device(device_path)
    if not device and device_handler.devices:
        device = next(
            (d for d in device_handler.devices if d.device_info.path.name == device_path.name),
            None,
        )
    if not device and len(device_handler.devices) == 1:
        only_device = device_handler.devices[0]
        log(f"Falling back to detected device {only_device.device_info.path}")
        device = only_device
    if not device:
        available = ", ".join(str(d.device_info.path) for d in device_handler.devices)
        raise ValueError(
            f"Device not found: {state.disk_device}. Detected: {available or 'none'}"
        )

    ensure_device_link(device.device_info.path, device_path if device_path.exists() else None, log)

    fs_type = FilesystemType[state.filesystem.upper()]

    if state.disk_mode == "auto":
        log("Using automatic partition layout")
        mod = DeviceModification(device=device, wipe=True)
        sector_size = device.device_info.sector_size

        boot_start = Size(1, Unit.MiB, sector_size)
        boot_length = Size(512, Unit.MiB, sector_size)
        boot_flags = [PartitionFlag.BOOT]
        if is_uefi():
            boot_flags.append(PartitionFlag.ESP)

        boot_partition = PartitionModification(
            status=ModificationStatus.CREATE,
            type=PartitionType.PRIMARY,
            start=boot_start,
            length=boot_length,
            mountpoint=Path("/boot"),
            fs_type=FilesystemType.FAT32,
            flags=boot_flags,
        )
        mod.add_partition(boot_partition)

        root_start = Size(513, Unit.MiB, sector_size)
        root_length = device.device_info.total_size - root_start

        btrfs_subvols = []
        mount_options = []
        root_mountpoint = Path("/")
        if fs_type == FilesystemType.BTRFS:
            btrfs_subvols = [
                SubvolumeModification("@", mountpoint=Path("/")),
                SubvolumeModification("@home", mountpoint=Path("/home")),
            ]
            mount_options = ["compress=zstd"]
            root_mountpoint = None

        root_partition = PartitionModification(
            status=ModificationStatus.CREATE,
            type=PartitionType.PRIMARY,
            start=root_start,
            length=root_length,
            mountpoint=root_mountpoint,
            fs_type=fs_type,
            mount_options=mount_options,
            btrfs_subvols=btrfs_subvols,
        )
        mod.add_partition(root_partition)

        disk_config = DiskLayoutConfiguration(
            config_type=DiskLayoutType.Default,
            device_modifications=[mod],
        )

        if state.encrypt:
            disk_config.disk_encryption = DiskEncryption(
                encryption_type=EncryptionType.LUKS,
                encryption_password=Password(plaintext=state.luks_password),
                partitions=[root_partition],
            )

        return disk_config

    log("Using manual partition layout")
    mod = DeviceModification(device=device, wipe=False)

    def build_existing_part(path: str, mountpoint: str, fmt: bool, target_fs: FilesystemType):
        part_info = device_handler.find_partition(Path(path))
        if not part_info:
            raise ValueError(f"Partition not found: {path}")
        part_mod = PartitionModification.from_existing_partition(part_info)
        part_mod.mountpoint = Path(mountpoint) if mountpoint else None
        if fmt:
            part_mod.status = ModificationStatus.MODIFY
            part_mod.fs_type = target_fs
        else:
            part_mod.status = ModificationStatus.EXIST
        return part_mod

    root_part = build_existing_part(
        state.manual_root,
        "/",
        state.manual_format_root,
        fs_type,
    )
    mod.add_partition(root_part)

    if state.manual_boot:
        boot_part = build_existing_part(
            state.manual_boot,
            "/boot",
            state.manual_format_boot,
            FilesystemType.FAT32,
        )
        if PartitionFlag.BOOT not in boot_part.flags:
            boot_part.flags.append(PartitionFlag.BOOT)
        if is_uefi() and PartitionFlag.ESP not in boot_part.flags:
            boot_part.flags.append(PartitionFlag.ESP)
        mod.add_partition(boot_part)

    if state.manual_home:
        home_part = build_existing_part(
            state.manual_home,
            "/home",
            state.manual_format_home,
            fs_type,
        )
        mod.add_partition(home_part)

    disk_config = DiskLayoutConfiguration(
        config_type=DiskLayoutType.Manual,
        device_modifications=[mod],
    )

    if state.encrypt:
        disk_config.disk_encryption = DiskEncryption(
            encryption_type=EncryptionType.LUKS,
            encryption_password=Password(plaintext=state.luks_password),
            partitions=[root_part],
        )

    return disk_config


def write_keyboard_config(target: Path, layouts: list[str]) -> None:
    conf_dir = target / "etc" / "X11" / "xorg.conf.d"
    conf_dir.mkdir(parents=True, exist_ok=True)
    layout_list = ",".join(layouts)
    conf = (
        "Section \"InputClass\"\n"
        "    Identifier \"system-keyboard\"\n"
        "    MatchIsKeyboard \"on\"\n"
        f"    Option \"XkbLayout\" \"{layout_list}\"\n"
        "EndSection\n"
    )
    (conf_dir / "00-keyboard.conf").write_text(conf)


def apply_wallpapers(target: Path, log: Callable[[str], None]) -> str | None:
    target_dir = target / WALLPAPER_TARGET_DIR.relative_to("/")
    target_dir.mkdir(parents=True, exist_ok=True)
    if WALLPAPER_SOURCE_DIR.exists():
        log("Copying custom wallpapers")
        shutil.copytree(WALLPAPER_SOURCE_DIR, target_dir, dirs_exist_ok=True)

    candidates = []
    for ext in ("*.png", "*.jpg", "*.jpeg", "*.webp"):
        candidates.extend(sorted(target_dir.glob(ext)))
    if not candidates:
        return None

    wallpaper = candidates[0]
    return f"file://{wallpaper}"


def apply_gnome_theming(target: Path, username: str, log: Callable[[str], None]) -> None:
    accent_css = (
        "/* Addictive accent tweaks */\n"
        f"@define-color accent_color {ACCENT};\n"
        f"@define-color accent_bg_color {ACCENT};\n"
        "@define-color accent_fg_color #0B0F0E;\n"
    )

    for base in [
        target / "etc" / "skel",
        target / "home" / username,
    ]:
        gtk4 = base / ".config" / "gtk-4.0"
        gtk3 = base / ".config" / "gtk-3.0"
        gtk4.mkdir(parents=True, exist_ok=True)
        gtk3.mkdir(parents=True, exist_ok=True)
        (gtk4 / "gtk.css").write_text(accent_css)
        (gtk3 / "gtk.css").write_text(accent_css)

    dconf_dir = target / "etc" / "dconf" / "db" / "local.d"
    dconf_dir.mkdir(parents=True, exist_ok=True)
    wallpaper_uri = apply_wallpapers(target, log)

    dconf_lines = [
        "[org/gnome/desktop/interface]",
        "accent-color='teal'",
        "",
    ]
    if wallpaper_uri:
        dconf_lines += [
            "[org/gnome/desktop/background]",
            f"picture-uri='{wallpaper_uri}'",
            f"picture-uri-dark='{wallpaper_uri}'",
            "",
            "[org/gnome/desktop/screensaver]",
            f"picture-uri='{wallpaper_uri}'",
            "",
        ]
    (dconf_dir / "00-addictive").write_text("\n".join(dconf_lines))


def enable_blackarch(installation, log: Callable[[str], None]) -> None:
    strap_url = "https://blackarch.org/strap.sh"
    target_root = installation.target / "root"
    target_root.mkdir(parents=True, exist_ok=True)
    strap_path = target_root / "strap.sh"

    if shutil.which("curl"):
        cmd = ["curl", "-fsSL", strap_url, "-o", str(strap_path)]
    elif shutil.which("wget"):
        cmd = ["wget", "-qO", str(strap_path), strap_url]
    else:
        raise RuntimeError("Neither curl nor wget is available to download BlackArch strap.sh")

    result = run_cmd(cmd, check=False)
    if result.returncode != 0 or not strap_path.exists():
        err = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"Failed to download BlackArch strap.sh: {err or 'unknown error'}")

    strap_path.chmod(0o755)
    installation.arch_chroot("/root/strap.sh")


def install_blackarch_toolkits(installation, toolkits: list[str], log: Callable[[str], None]) -> None:
    if not toolkits:
        return
    pkgs = [BLACKARCH_TOOLSETS[name] for name in toolkits if name in BLACKARCH_TOOLSETS]
    if not pkgs:
        return
    log(f"Installing BlackArch toolkits: {', '.join(pkgs)}")
    installation.arch_chroot("pacman -S --needed --noconfirm " + " ".join(pkgs))


def perform_installation(
    state: InstallerState,
    log: Callable[[str], None],
    progress: Callable[[int, int, str], None] | None = None,
) -> None:
    if os.geteuid() != 0:
        raise PermissionError("Run this installer as root.")

    from archinstall.lib.disk.filesystem import FilesystemHandler
    from archinstall.lib.installer import Installer
    from archinstall.lib.models.bootloader import Bootloader
    from archinstall.lib.models.locale import LocaleConfiguration
    from archinstall.lib.models.profile import ProfileConfiguration
    from archinstall.lib.models.users import Password, User
    from archinstall.lib.profile.profiles_handler import profile_handler

    def build_steps() -> list[str]:
        steps = [
            "Building disk configuration",
            "Preparing disk",
            "Applying disk changes",
            "Mounting filesystems",
        ]
        if state.encrypt:
            steps.append("Generating encryption key files")
        steps += [
            "Installing base system",
            "Setting timezone",
            "Installing GNOME profile",
            "Installing core packages",
            "Creating user",
            "Adding bootloader",
            "Writing keyboard configuration",
            "Applying GNOME theming",
            "Updating dconf database",
            "Enabling BlackArch repositories",
        ]
        if state.toolkits:
            steps.append("Installing BlackArch toolkits")
        steps.append("Generating fstab")
        return steps

    steps = build_steps()
    total = len(steps)
    current = 0

    def step(label: str) -> None:
        nonlocal current
        current += 1
        if progress:
            progress(current, total, label)
        log(label)

    step("Building disk configuration")
    disk_config = build_disk_config(state, log)

    step("Preparing disk")
    prepare_disk_for_installation(state, log)

    step("Applying disk changes")
    fs_handler = FilesystemHandler(disk_config)
    try:
        fs_handler.perform_filesystem_operations()
    except Exception as exc:
        error_text = str(exc)
        if state.disk_mode == "auto" and "wipefs" in error_text:
            log("wipefs failed; forcing cleanup and retrying once")
            force_wipefs_for_disk(state.disk_device, log)
            fs_handler = FilesystemHandler(disk_config)
            fs_handler.perform_filesystem_operations()
        elif "Unable to determine new partition" in error_text or "Unable to determine new uuid" in error_text:
            log("Partition info not ready; settling devices and retrying once")
            settle_block_devices(state.disk_device, log)
            fs_handler = FilesystemHandler(disk_config)
            fs_handler.perform_filesystem_operations()
        else:
            raise

    mountpoint = Path("/mnt")

    with Installer(mountpoint, disk_config, kernels=["linux"]) as installation:
        step("Mounting filesystems")
        installation.mount_ordered_layout()

        if disk_config.disk_encryption and state.encrypt:
            step("Generating encryption key files")
            installation.generate_key_files()

        locale_config = LocaleConfiguration(
            sys_lang="en_US",
            sys_enc="UTF-8",
            kb_layout=state.keyboard_layouts[0],
        )

        step("Installing base system")
        installation.minimal_installation(hostname=state.hostname, locale_config=locale_config)

        step("Setting timezone")
        installation.set_timezone(state.timezone)

        step("Installing GNOME profile")
        gnome_profile = profile_handler.get_profile_by_name("GNOME")
        profile_config = ProfileConfiguration(profile=gnome_profile)
        profile_handler.install_profile_config(installation, profile_config)

        step("Installing core packages")
        installation.add_additional_packages(["networkmanager", "sudo", "dconf"])
        installation.enable_service("NetworkManager")

        step("Creating user")
        user = User(
            username=state.username,
            password=Password(plaintext=state.password),
            sudo=state.sudo,
            groups=["audio", "video", "storage", "input", "network"],
        )
        installation.create_users(user)

        if profile_config.profile:
            profile_config.profile.post_install(installation)
            profile_config.profile.provision(installation, [user])

        step("Adding bootloader")
        bootloader = Bootloader.Systemd if is_uefi() else Bootloader.Grub
        installation.add_bootloader(bootloader)

        step("Writing keyboard configuration")
        write_keyboard_config(installation.target, state.keyboard_layouts)

        step("Applying GNOME theming")
        apply_gnome_theming(installation.target, state.username, log)
        installation.chown(
            f"{state.username}:{state.username}",
            f"/home/{state.username}/.config",
            options=["-R"],
        )

        step("Updating dconf database")
        dconf_check = installation.arch_chroot(
            "sh -lc 'command -v dconf >/dev/null 2>&1'",
            peek_output=True,
        )
        if dconf_check.exit_code == 0:
            try:
                installation.arch_chroot("dconf update")
            except Exception as exc:
                log(f"dconf update failed; continuing: {exc}")
        else:
            log("dconf not available yet; skipping dconf update")

        step("Enabling BlackArch repositories")
        enable_blackarch(installation, log)
        if state.toolkits:
            step("Installing BlackArch toolkits")
            install_blackarch_toolkits(installation, state.toolkits, log)

        step("Generating fstab")
        installation.genfstab()


def main() -> None:
    if os.geteuid() != 0:
        print("This installer must be run as root.", file=sys.stderr)
        sys.exit(1)
    app = AddictiveInstallerApp()
    app.run()


if __name__ == "__main__":
    main()
