#!/usr/bin/env python3
import os
import sys
import json
import subprocess
import urllib.request
import shutil
import time
from textual.app import App, ComposeResult
from textual.screen import Screen
from textual.widgets import Header, Footer, Button, Static, Input, Select, Checkbox, RadioSet, RadioButton, RichLog
from textual.containers import Container, Vertical, Horizontal
from textual import work

# Configuration state
cfg = {
    "keyboard": "us",
    "network_type": "wired",
    "wifi_ssid": "",
    "wifi_pass": "",
    "toolkits": [],
    "disk_mode": "auto",
    "disk_drive": "",
    "disk_fs": "btrfs",
    "disk_encrypt": False,
    "disk_pass": "",
    "username": "addictive",
    "password": "",
    "hostname": "addictive",
    "root_privs": True,
    "timezone": "UTC"
}

try:
    import archinstall
    HAS_ARCHINSTALL = True
except ImportError:
    HAS_ARCHINSTALL = False

class InstallScreen(Screen):
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(classes="container"):
            yield Static("Installing Addictive OS...", classes="step-title")
            yield RichLog(id="install_log", wrap=True)
        yield Footer()

    async def on_mount(self) -> None:
        self.run_installation()

    @work(thread=True)
    def run_installation(self):
        log = self.query_one("#install_log", RichLog)
        self.app.call_from_thread(log.write, "[*] Preparing Addictive OS installation...")

        # 1. Generate archinstall config
        user_config = {
            "keyboard-layout": cfg["keyboard"],
            "bootloader": "systemd-boot",
            "swap": True,
            "hostname": cfg["hostname"],
            "timezone": cfg["timezone"],
            "profile": {
                "type": "desktop",
                "custom_settings": {"desktop_environment": "gnome"}
            },
            "audio": "pipewire",
            "network-management": "networkmanager",
            "packages": ["wget", "curl", "git", "vim", "btrfs-progs", "networkmanager"],
            "harddrives": [cfg["disk_drive"]] if cfg["disk_drive"] else []
        }

        if cfg["disk_encrypt"] and cfg["disk_drive"]:
            user_config["disk_encryption"] = {
                "encryption_password": cfg["disk_pass"],
                "encryption_type": "luks",
                "partitions": "all"
            }
        
        if cfg["disk_drive"]:
            user_config["disk_layouts"] = {
                cfg["disk_drive"]: {
                    "wipe": True,
                    "partitions": [
                        {
                            "boot": True,
                            "encrypted": False,
                            "filesystem": {
                                "format": "fat32"
                            },
                            "mountpoint": "/boot",
                            "size": "512MiB",
                            "start": "1MiB",
                            "type": "primary",
                            "wipe": True
                        },
                        {
                            "encrypted": cfg["disk_encrypt"],
                            "filesystem": {
                                "format": cfg["disk_fs"]
                            },
                            "mountpoint": "/",
                            "size": "100%",
                            "start": "513MiB",
                            "type": "primary",
                            "wipe": True
                        }
                    ]
                }
            }

        user_creds = [
            {
                "username": cfg["username"],
                "password": cfg["password"],
                "sudo": cfg["root_privs"]
            }
        ]

        # Save configuration
        try:
            with open("/tmp/user_configuration.json", "w") as f:
                json.dump(user_config, f)
            with open("/tmp/user_credentials.json", "w") as f:
                json.dump({"!users": user_creds}, f)
        except Exception as e:
            self.app.call_from_thread(log.write, f"[!] Error writing config: {e}")

        self.app.call_from_thread(log.write, "[*] Triggering archinstall backend...")
        try:
            if HAS_ARCHINSTALL:
                # We pipe the output to ensure we don't freeze the TUI. In a real scenario we'd stream it.
                subprocess.run(
                    ["archinstall", "--config", "/tmp/user_configuration.json", "--creds", "/tmp/user_credentials.json", "--silent"],
                    check=True, capture_output=True
                )
            else:
                self.app.call_from_thread(log.write, "[!] Mock Mode: archinstall not found, simulating process...")
                time.sleep(3)

            self.app.call_from_thread(log.write, "[*] Arch installation complete. Proceeding with Addictive customizations...")
            self.post_install(log)
            self.app.call_from_thread(log.write, "[+] Addictive OS has been successfully installed! You may now reboot.")
        except subprocess.CalledProcessError as e:
            self.app.call_from_thread(log.write, f"[!] Installation failed: {e.stderr.decode('utf-8', errors='ignore')}")
        except Exception as e:
            self.app.call_from_thread(log.write, f"[!] An unexpected error occurred: {e}")

    def post_install(self, log):
        target_mnt = "/mnt"
        
        if not os.path.exists(target_mnt):
            self.app.call_from_thread(log.write, "[!] Target /mnt not found, skipping post-install (Mock mode behavior).")
            return

        # Handle Wi-Fi if needed
        if cfg["network_type"] == "wifi" and cfg["wifi_ssid"]:
            self.app.call_from_thread(log.write, f"[*] Configuring Wi-Fi for {cfg['wifi_ssid']}...")
            nm_dir = f"{target_mnt}/etc/NetworkManager/system-connections"
            os.makedirs(nm_dir, exist_ok=True)
            nm_conf = f"""[connection]
id={cfg['wifi_ssid']}
type=wifi

[wifi]
ssid={cfg['wifi_ssid']}

[wifi-security]
key-mgmt=wpa-psk
psk={cfg['wifi_pass']}

[ipv4]
method=auto

[ipv6]
method=auto
"""
            conf_path = f"{nm_dir}/{cfg['wifi_ssid']}.nmconnection"
            with open(conf_path, "w") as f:
                f.write(nm_conf)
            os.chmod(conf_path, 0o600)

        # BlackArch Setup
        if cfg["toolkits"]:
            self.app.call_from_thread(log.write, "[*] Setting up BlackArch repositories... (This might take a while)")
            strap_script = f"{target_mnt}/tmp/strap.sh"
            try:
                urllib.request.urlretrieve("https://blackarch.org/strap.sh", strap_script)
                os.chmod(strap_script, 0o755)
                subprocess.run(["arch-chroot", target_mnt, "/tmp/strap.sh"], check=True, capture_output=True)
                
                self.app.call_from_thread(log.write, f"[*] Installing selected toolkits: {', '.join(cfg['toolkits'])}")
                subprocess.run(["arch-chroot", target_mnt, "pacman", "-Sy", "--noconfirm", *cfg["toolkits"]], check=True, capture_output=True)
            except Exception as e:
                self.app.call_from_thread(log.write, f"[!] Failed to setup BlackArch: {e}")

        # Theming & Wallpapers
        self.app.call_from_thread(log.write, "[*] Applying Addictive OS custom aesthetics...")
        wallpaper_src = "/etc/addictive/wallpapers"
        wallpaper_dest = f"{target_mnt}/usr/share/backgrounds/addictive"
        
        if os.path.exists(wallpaper_src):
            os.makedirs(wallpaper_dest, exist_ok=True)
            for file in os.listdir(wallpaper_src):
                shutil.copy(os.path.join(wallpaper_src, file), wallpaper_dest)
            self.app.call_from_thread(log.write, "[+] Copied custom wallpapers.")
            
            wallpapers = os.listdir(wallpaper_dest)
            if wallpapers:
                wp_path = f"/usr/share/backgrounds/addictive/{wallpapers[0]}"
                gschema_override = f"""
[org.gnome.desktop.background]
picture-uri='file://{wp_path}'
picture-uri-dark='file://{wp_path}'
"""
                override_dir = f"{target_mnt}/usr/share/glib-2.0/schemas"
                os.makedirs(override_dir, exist_ok=True)
                with open(f"{override_dir}/99-addictive.gschema.override", "w") as f:
                    f.write(gschema_override)
                subprocess.run(["arch-chroot", target_mnt, "glib-compile-schemas", "/usr/share/glib-2.0/schemas"], check=True)
        else:
            self.app.call_from_thread(log.write, f"[-] No custom wallpapers found in {wallpaper_src}.")

class SummaryScreen(Screen):
    def compose(self) -> ComposeResult:
        yield Header()
        with Container(classes="container"):
            yield Static("Review Installation Settings", classes="step-title")
            yield RichLog(id="summary_log", wrap=True)
            with Horizontal():
                yield Button("Begin Installation", variant="primary", id="btn_install")
                yield Button("Back", id="btn_back")
        yield Footer()

    def on_mount(self) -> None:
        log = self.query_one("#summary_log", RichLog)
        log.write(f"Keyboard: {cfg['keyboard']}")
        log.write(f"Network: {cfg['network_type'].upper()}")
        log.write(f"Drive: {cfg['disk_drive']} ({cfg['disk_fs']}) | Encrypted: {cfg['disk_encrypt']}")
        log.write(f"User: {cfg['username']} (Root: {cfg['root_privs']})")
        log.write(f"Hostname: {cfg['hostname']}")
        log.write(f"Timezone: {cfg['timezone']}")
        tk_str = ', '.join(cfg['toolkits']) if cfg['toolkits'] else 'None'
        log.write(f"Toolkits: {tk_str}")
        log.write(f"Desktop Environment: GNOME")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_install":
            self.app.push_screen(InstallScreen())
        elif event.button.id == "btn_back":
            self.app.pop_screen()

class TimezoneScreen(Screen):
    def compose(self) -> ComposeResult:
        yield Header()
        with Container(classes="container"):
            yield Static("Timezone Selection", classes="step-title")
            tzs = ["UTC", "America/New_York", "America/Los_Angeles", "Europe/London", "Europe/Berlin", "Asia/Tokyo"]
            yield Select(((tz, tz) for tz in tzs), value="UTC", id="tz_select")
            yield Button("Next", variant="primary", id="btn_next")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_next":
            cfg["timezone"] = self.query_one("#tz_select", Select).value
            self.app.push_screen(SummaryScreen())

class AccountScreen(Screen):
    def compose(self) -> ComposeResult:
        yield Header()
        with Container(classes="container"):
            yield Static("Account & System Info", classes="step-title")
            yield Input(placeholder="Hostname (e.g., addictive)", value="addictive", id="hostname")
            yield Input(placeholder="Username", id="username")
            yield Input(placeholder="Password", password=True, id="password")
            yield Checkbox("Grant Root (sudo) Privileges", value=True, id="root_privs")
            yield Button("Next", variant="primary", id="btn_next")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_next":
            cfg["hostname"] = self.query_one("#hostname", Input).value
            cfg["username"] = self.query_one("#username", Input).value
            cfg["password"] = self.query_one("#password", Input).value
            cfg["root_privs"] = self.query_one("#root_privs", Checkbox).value
            
            if cfg["username"] and cfg["password"] and cfg["hostname"]:
                self.app.push_screen(TimezoneScreen())

class DiskScreen(Screen):
    def compose(self) -> ComposeResult:
        yield Header()
        with Container(classes="container"):
            yield Static("Disk Setup", classes="step-title")
            drives = []
            if os.path.exists("/sys/block"):
                for d in os.listdir("/sys/block"):
                    if not d.startswith("loop") and not d.startswith("ram") and not d.startswith("sr"):
                        drives.append(f"/dev/{d}")
            if not drives: drives = ["/dev/sda", "/dev/nvme0n1"]

            yield Select(((d, d) for d in drives), prompt="Select Installation Drive", id="disk_drive")
            yield Select((("btrfs", "btrfs"), ("ext4", "ext4")), value="btrfs", id="disk_fs")
            yield Checkbox("Encrypt Disk (LUKS)", id="disk_encrypt")
            yield Input(placeholder="Encryption Password", password=True, id="disk_pass", classes="hidden")
            yield Button("Next", variant="primary", id="btn_next")
        yield Footer()

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if event.checkbox.id == "disk_encrypt":
            pass_input = self.query_one("#disk_pass", Input)
            if event.value:
                pass_input.remove_class("hidden")
            else:
                pass_input.add_class("hidden")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_next":
            cfg["disk_drive"] = self.query_one("#disk_drive", Select).value
            if not cfg["disk_drive"]:
                # Require drive selection
                return
            cfg["disk_fs"] = self.query_one("#disk_fs", Select).value
            cfg["disk_encrypt"] = self.query_one("#disk_encrypt", Checkbox).value
            cfg["disk_pass"] = self.query_one("#disk_pass", Input).value
            
            if cfg["disk_encrypt"] and not cfg["disk_pass"]:
                return # Prevent encrypt without password
            self.app.push_screen(AccountScreen())

class ToolkitsScreen(Screen):
    def compose(self) -> ComposeResult:
        yield Header()
        with Container(classes="container"):
            yield Static("Select BlackArch Toolkits", classes="step-title")
            yield Checkbox("Web Applications (blackarch-webapp)", id="tk_webapp")
            yield Checkbox("Reverse Engineering (blackarch-reversing)", id="tk_reversing")
            yield Checkbox("Exploitation (blackarch-exploitation)", id="tk_exploitation")
            yield Checkbox("OSINT (blackarch-osint)", id="tk_osint")
            yield Checkbox("Forensics (blackarch-forensic)", id="tk_forensic")
            yield Button("Next", variant="primary", id="btn_next")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_next":
            cfg["toolkits"] = []
            if self.query_one("#tk_webapp", Checkbox).value: cfg["toolkits"].append("blackarch-webapp")
            if self.query_one("#tk_reversing", Checkbox).value: cfg["toolkits"].append("blackarch-reversing")
            if self.query_one("#tk_exploitation", Checkbox).value: cfg["toolkits"].append("blackarch-exploitation")
            if self.query_one("#tk_osint", Checkbox).value: cfg["toolkits"].append("blackarch-osint")
            if self.query_one("#tk_forensic", Checkbox).value: cfg["toolkits"].append("blackarch-forensic")
            self.app.push_screen(DiskScreen())

class NetworkScreen(Screen):
    def compose(self) -> ComposeResult:
        yield Header()
        with Container(classes="container"):
            yield Static("Internet Connection", classes="step-title")
            yield RadioSet(
                RadioButton("Wired Connection (Skip Wi-Fi)", id="net_wired", value=True),
                RadioButton("Wi-Fi Setup", id="net_wifi"),
                id="net_radios"
            )
            with Container(id="wifi_inputs", classes="hidden"):
                yield Input(placeholder="SSID", id="wifi_ssid")
                yield Input(placeholder="Password", password=True, id="wifi_pass")
            yield Button("Next", variant="primary", id="btn_next")
        yield Footer()

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        wifi_inputs = self.query_one("#wifi_inputs")
        if event.pressed.id == "net_wifi":
            wifi_inputs.remove_class("hidden")
        else:
            wifi_inputs.add_class("hidden")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_next":
            radios = self.query_one("#net_radios", RadioSet)
            cfg["network_type"] = "wifi" if radios.pressed_button.id == "net_wifi" else "wired"
            if cfg["network_type"] == "wifi":
                cfg["wifi_ssid"] = self.query_one("#wifi_ssid", Input).value
                cfg["wifi_pass"] = self.query_one("#wifi_pass", Input).value
                if not cfg["wifi_ssid"]:
                    return # Require SSID
            self.app.push_screen(ToolkitsScreen())

class KeyboardScreen(Screen):
    def compose(self) -> ComposeResult:
        yield Header()
        with Container(classes="container"):
            yield Static("Select Keyboard Layout", classes="step-title")
            yield Select(
                ((layout, layout) for layout in ["us", "uk", "de", "fr", "es", "it", "se", "no", "dk"]),
                value="us", id="kbd_select"
            )
            yield Button("Next", variant="primary", id="btn_next")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_next":
            cfg["keyboard"] = self.query_one("#kbd_select", Select).value
            self.app.push_screen(NetworkScreen())

class WelcomeScreen(Screen):
    def compose(self) -> ComposeResult:
        yield Header()
        with Container(classes="container"):
            yield Static("Welcome to Addictive OS", id="logo")
            yield Static("A low-cortisol, GNOME-based penetration testing environment powered by BlackArch.", classes="step-title")
            yield Static("Let's get this deployment rolling. Relax, we've got the hard parts covered.")
            yield Button("Start Installation", variant="primary", id="btn_start")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_start":
            self.app.push_screen(KeyboardScreen())

class AddictiveInstaller(App):
    CSS_PATH = "addictive.css"
    BINDINGS = [("q", "quit", "Quit Installer")]
    TITLE = "Addictive OS Installer"

    def on_mount(self) -> None:
        self.push_screen(WelcomeScreen())

if __name__ == "__main__":
    app = AddictiveInstaller()
    app.run()
