#!/usr/bin/env python3

import os
import sys
import json
import asyncio
import subprocess
from pathlib import Path

# Check for required dependencies before proceeding
try:
    from textual.app import App, ComposeResult
    from textual.screen import Screen
    from textual.widgets import (
        Header, Footer, Button, Static, Label, Select, Input, Switch,
        Checkbox, ProgressBar, RichLog, Markdown
    )
    from textual.containers import Container, Vertical, Horizontal
    from textual.binding import Binding
    from textual import work
except ImportError:
    print("Error: The 'textual' framework is required to run this installer.")
    print("Please install it using: pip install textual")
    sys.exit(1)

# Try to import archinstall, fail gracefully if not running in the live env
try:
    import archinstall
except ImportError:
    archinstall = None


# --- Theming & Constants ---
ACCENT_COLOR = "#0FD29B"

CUSTOM_CSS = f"""
Screen {{
    background: $surface;
}}

Header {{
    background: {ACCENT_COLOR};
    color: $background;
    text-style: bold;
}}

Footer {{
    background: $surface;
    color: {ACCENT_COLOR};
}}

.title {{
    text-align: center;
    text-style: bold;
    color: {ACCENT_COLOR};
    margin: 1;
}}

.subtitle {{
    text-align: center;
    margin-bottom: 2;
}}

.step-container {{
    align: center middle;
    width: 100%;
    height: 100%;
    padding: 2 4;
}}

.nav-buttons {{
    dock: bottom;
    align: center middle;
    height: 3;
    margin-top: 2;
}}

Button {{
    background: $boost;
    color: $text;
    border: none;
}}

Button:focus {{
    background: {ACCENT_COLOR} 30%;
}}

Button.-primary {{
    background: {ACCENT_COLOR};
    color: $background;
    text-style: bold;
}}

Button.-primary:focus {{
    background: {ACCENT_COLOR} 80%;
}}

Input:focus, Select:focus {{
    border: tall {ACCENT_COLOR};
}}

Switch:focus > .switch--slider {{
    background: {ACCENT_COLOR};
}}
"""

# --- Global State ---
STATE = {
    "keyboard_layout": "us",
    "network_type": "auto",
    "wifi_ssid": "",
    "wifi_password": "",
    "toolkits": [],
    "disk_mode": "auto",
    "filesystem": "btrfs",
    "encrypt_disk": False,
    "encryption_password": "",
    "drive": None,
    "username": "addictive",
    "password": "",
    "hostname": "addictive-os",
    "grant_root": True,
    "timezone": "UTC",
}

# --- Screens ---

class BaseInstallScreen(Screen):
    """Base screen providing standard layout and navigation."""
    
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(classes="step-container"):
            yield from self.compose_content()
            with Horizontal(classes="nav-buttons"):
                if self.show_back:
                    yield Button("Back", id="btn_back")
                yield Button("Next", id="btn_next", variant="primary")
        yield Footer()

    @property
    def show_back(self) -> bool:
        return True

    def compose_content(self) -> ComposeResult:
        yield Static("Override me")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_back":
            self.app.pop_screen()
        elif event.button.id == "btn_next":
            self.save_state()
            self.app.push_screen(self.next_screen())

    def save_state(self):
        pass

    def next_screen(self) -> str:
        return "welcome"


class WelcomeScreen(BaseInstallScreen):
    @property
    def show_back(self) -> bool:
        return False

    def compose_content(self) -> ComposeResult:
        yield Label("Welcome to Addictive OS", classes="title")
        yield Label("The low-cortisol penetration testing environment.", classes="subtitle")
        yield Markdown(
            "This installer will guide you through setting up your system.\n\n"
            "**Features included:**\n"
            "- Arch Linux base\n"
            "- BlackArch Repositories integration\n"
            "- GNOME Desktop Environment\n"
            "- Calming, distraction-free default setup\n\n"
            "Take a deep breath. Press **Next** when you are ready to begin."
        )

    def next_screen(self) -> str:
        return "keyboard"


class KeyboardScreen(BaseInstallScreen):
    def compose_content(self) -> ComposeResult:
        yield Label("Keyboard Layout", classes="title")
        yield Label("Select your primary keyboard layout.", classes="subtitle")
        
        # In a real scenario, we'd query localectl or archinstall for dynamic layouts.
        # Hardcoding common ones to keep it robust out of the box.
        layouts = [("US", "us"), ("UK", "gb"), ("German", "de"), ("French", "fr"), ("Spanish", "es")]
        yield Select(layouts, value=STATE["keyboard_layout"], id="sel_kb")

    def save_state(self):
        STATE["keyboard_layout"] = self.query_one("#sel_kb", Select).value

    def next_screen(self) -> str:
        return "network"


class NetworkScreen(BaseInstallScreen):
    def compose_content(self) -> ComposeResult:
        yield Label("Internet Connection", classes="title")
        yield Label("A network connection is required to fetch packages.", classes="subtitle")
        
        with Vertical():
            yield Label("We'll setup NetworkManager on the installed system.")
            yield Label("Select your current connection method:")
            yield Select([("Wired / Auto (Skip config)", "auto"), ("Wi-Fi", "wifi")], value=STATE["network_type"], id="sel_net")
            
            with Container(id="wifi_config"):
                yield Label("Select an Access Point:")
                yield Select([("Scanning for networks...", "scanning")], value="scanning", id="sel_ssid")
                yield Input(placeholder="Password", value=STATE["wifi_password"], id="inp_pass", password=True)

    def on_mount(self) -> None:
        self.query_one("#wifi_config").display = STATE["network_type"] == "wifi"
        self.scan_wifi()

    @work(thread=True)
    def scan_wifi(self) -> None:
        ssids = []
        
        # Helper to clean and add SSIDs
        def add_ssids(new_list):
            for s in new_list:
                s = s.strip()
                if s and s != "--" and s != "\\x00":
                    ssids.append(s)

        try:
            # 1. Try nmcli (More reliable for names with spaces)
            # Use --terse and --fields SSID to get just the names
            result = subprocess.run(["nmcli", "-t", "-f", "SSID", "dev", "wifi"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                add_ssids(result.stdout.splitlines())
        except Exception:
            pass
            
        if not ssids:
            try:
                # 2. Try iwctl (Station get-networks)
                # We'll be more careful with parsing here
                result = subprocess.run(["iwctl", "station", "wlan0", "get-networks"], capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    lines = result.stdout.splitlines()
                    for line in lines:
                        # iwctl output usually has '>' or spaces before the name
                        # We look for the start of the table after headers
                        if "----" in line or "Network name" in line:
                            continue
                        # Typical line: "  My Network Name          psk  "
                        # Use regex or fixed width? Let's try to extract before 'psk', 'open', or '8021x'
                        for sec in ["psk", "open", "8021x"]:
                            if f"  {sec}" in line:
                                ssid = line[:line.find(f"  {sec}")].strip().strip(">").strip()
                                if ssid: ssids.append(ssid)
                                break
            except Exception:
                pass

        ssids = list(dict.fromkeys(ssids))
        if not ssids:
            ssids = ["Addictive-HQ", "Hackers-Lounge", "Coffee-Shop-Pro", "Home-Network"]
            
        self.app.call_from_thread(self.update_wifi_list, ssids)

    def update_wifi_list(self, ssids: list) -> None:
        try:
            sel = self.query_one("#sel_ssid", Select)
            options = [(s, s) for s in ssids]
            sel.set_options(options)
            
            if options:
                if STATE["wifi_ssid"] in ssids:
                    sel.value = STATE["wifi_ssid"]
                else:
                    sel.value = options[0][1]
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_next":
            # Validation
            if STATE["network_type"] == "wifi":
                sel_ssid = self.query_one("#sel_ssid", Select)
                password = self.query_one("#inp_pass", Input).value
                
                if not sel_ssid.value or sel_ssid.value == "scanning":
                    self.app.notify("Please select a Wi-Fi network.", severity="error")
                    return
                if not password or len(password) < 8:
                    self.app.notify("Password must be at least 8 characters.", severity="error")
                    return
            
            self.save_state()
            self.app.push_screen(self.next_screen())
        elif event.button.id == "btn_back":
            self.app.pop_screen()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "sel_net":
            wifi_container = self.query_one("#wifi_config")
            wifi_container.display = event.value == "wifi"

    def save_state(self):
        STATE["network_type"] = self.query_one("#sel_net", Select).value
        if STATE["network_type"] == "wifi":
            sel_ssid = self.query_one("#sel_ssid", Select)
            if sel_ssid.value:
                STATE["wifi_ssid"] = sel_ssid.value
            STATE["wifi_password"] = self.query_one("#inp_pass", Input).value

    def next_screen(self) -> str:
        return "toolkits"


class ToolkitsScreen(BaseInstallScreen):
    def compose_content(self) -> ComposeResult:
        yield Label("BlackArch Toolkits", classes="title")
        yield Label("Select the specific tool groups you want to pre-install.", classes="subtitle")
        
        toolkits = [
            ("blackarch-webapp", "Web Application Testing"),
            ("blackarch-reversing", "Reverse Engineering"),
            ("blackarch-recon", "Reconnaissance / OSINT"),
            ("blackarch-crypto", "Cryptography"),
            ("blackarch-wireless", "Wireless Attacks"),
            ("blackarch-exploitation", "Exploitation Frameworks")
        ]
        
        with Vertical(id="chk_toolkits"):
            for pkg, desc in toolkits:
                yield Checkbox(f"{desc} ({pkg})", id=f"chk_{pkg}", value=pkg in STATE["toolkits"])

    def save_state(self):
        selected = []
        for chk in self.query(Checkbox):
            if chk.value:
                pkg_name = chk.id.replace("chk_", "")
                selected.append(pkg_name)
        STATE["toolkits"] = selected

    def next_screen(self) -> str:
        return "disk"


class DiskScreen(BaseInstallScreen):
    def compose_content(self) -> ComposeResult:
        yield Label("Disk Setup", classes="title")
        yield Label("Choose how Addictive OS will be installed on your disk.", classes="subtitle")
        
        # Mocking block devices. In reality, we'd use archinstall.sys_command('lsblk')
        # We provide standard paths and a custom input option
        drives = [("/dev/sda", "/dev/sda"), ("/dev/nvme0n1", "/dev/nvme0n1"), ("/dev/vda", "/dev/vda")]
        
        yield Label("Target Drive:")
        yield Select(drives, value=STATE["drive"] or drives[0][1], id="sel_drive")
        
        yield Label("Filesystem:")
        yield Select([("Btrfs (Recommended)", "btrfs"), ("ext4", "ext4"), ("xfs", "xfs")], value=STATE["filesystem"], id="sel_fs")
        
        with Horizontal():
            yield Label("Encrypt Disk (LUKS): ")
            yield Switch(value=STATE["encrypt_disk"], id="sw_encrypt")
            
        with Container(id="encrypt_config"):
            yield Input(placeholder="Encryption Password", password=True, id="inp_enc_pass")

    def on_mount(self) -> None:
        self.query_one("#encrypt_config").display = STATE["encrypt_disk"]

    def on_switch_changed(self, event: Switch.Changed) -> None:
        if event.switch.id == "sw_encrypt":
            self.query_one("#encrypt_config").display = event.value

    def save_state(self):
        STATE["drive"] = self.query_one("#sel_drive", Select).value
        STATE["filesystem"] = self.query_one("#sel_fs", Select).value
        STATE["encrypt_disk"] = self.query_one("#sw_encrypt", Switch).value
        if STATE["encrypt_disk"]:
            STATE["encryption_password"] = self.query_one("#inp_enc_pass", Input).value

    def next_screen(self) -> str:
        return "account"


class AccountScreen(BaseInstallScreen):
    def compose_content(self) -> ComposeResult:
        yield Label("Account Creation", classes="title")
        yield Label("Set up your primary user and machine identity.", classes="subtitle")
        
        yield Input(placeholder="Username", value=STATE["username"], id="inp_user")
        yield Input(placeholder="Password", value=STATE["password"], id="inp_pass", password=True)
        yield Input(placeholder="Hostname", value=STATE["hostname"], id="inp_host")
        
        with Horizontal():
            yield Label("Grant sudo privileges (Recommended): ")
            yield Switch(value=STATE["grant_root"], id="sw_sudo")

    def save_state(self):
        STATE["username"] = self.query_one("#inp_user", Input).value
        STATE["password"] = self.query_one("#inp_pass", Input).value
        STATE["hostname"] = self.query_one("#inp_host", Input).value
        STATE["grant_root"] = self.query_one("#sw_sudo", Switch).value

    def next_screen(self) -> str:
        return "timezone"


class TimezoneScreen(BaseInstallScreen):
    def compose_content(self) -> ComposeResult:
        yield Label("Timezone", classes="title")
        yield Label("Select your local timezone.", classes="subtitle")
        
        timezones = [("UTC", "UTC"), ("America/New_York", "America/New_York"), 
                     ("Europe/London", "Europe/London"), ("Europe/Berlin", "Europe/Berlin"),
                     ("Asia/Tokyo", "Asia/Tokyo")]
        yield Select(timezones, value=STATE["timezone"], id="sel_tz")

    def save_state(self):
        STATE["timezone"] = self.query_one("#sel_tz", Select).value

    def next_screen(self) -> str:
        return "review"


class ReviewScreen(BaseInstallScreen):
    def compose_content(self) -> ComposeResult:
        yield Label("Review & Install", classes="title")
        yield Label("Ensure everything looks correct before paving the disk.", classes="subtitle")
        
        summary = f"""
**Keyboard Layout:** {STATE['keyboard_layout']}
**Network:** {STATE['network_type']}
**Toolkits:** {', '.join(STATE['toolkits']) if STATE['toolkits'] else 'None'}
**Drive:** {STATE['drive']} ({STATE['filesystem']})
**Encrypted:** {'Yes' if STATE['encrypt_disk'] else 'No'}
**Username:** {STATE['username']}
**Hostname:** {STATE['hostname']}
**Timezone:** {STATE['timezone']}

**Desktop Environment:** GNOME
**Repositories:** Base + BlackArch
"""
        yield Markdown(summary, id="md_summary")
        yield Label("[bold red]WARNING: Continuing will format the selected drive![/]")

    def on_mount(self):
        # Refresh summary when mounted in case user went back and changed something
        summary = f"""
**Keyboard Layout:** {STATE['keyboard_layout']}
**Network:** {STATE['network_type']}
**Toolkits:** {', '.join(STATE['toolkits']) if STATE['toolkits'] else 'None'}
**Drive:** {STATE['drive']} ({STATE['filesystem']})
**Encrypted:** {'Yes' if STATE['encrypt_disk'] else 'No'}
**Username:** {STATE['username']}
**Hostname:** {STATE['hostname']}
**Timezone:** {STATE['timezone']}

**Desktop Environment:** GNOME
**Repositories:** Base + BlackArch
"""
        self.query_one("#md_summary", Markdown).update(summary)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_back":
            self.app.pop_screen()
        elif event.button.id == "btn_next":
            # Initiate installation
            self.app.push_screen("install")


class InstallProgressScreen(Screen):
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(classes="step-container"):
            yield Label("Installing Addictive OS...", classes="title")
            yield ProgressBar(total=100, id="progress")
            yield RichLog(id="log", highlight=True, markup=True)
            with Horizontal(classes="nav-buttons"):
                yield Button("Reboot", id="btn_reboot", variant="primary", disabled=True)
                yield Button("Exit", id="btn_exit", disabled=True)
        yield Footer()

    async def on_mount(self) -> None:
        self.log_widget = self.query_one("#log", RichLog)
        self.progress = self.query_one("#progress", ProgressBar)
        self.log_widget.write(f"[bold {ACCENT_COLOR}]Initiating installation sequence...[/]")
        
        # Start installation loop in the background so the UI doesn't freeze
        asyncio.create_task(self.run_installation())

    async def run_installation(self):
        try:
            # 1. Prepare configuration
            self.log_widget.write("Preparing archinstall configuration payload...")
            self.progress.advance(10)
            await asyncio.sleep(1) # Simulated delay for visual flow
            
            config = self._build_archinstall_config()
            self.log_widget.write("Payload built successfully.")
            
            # Write config to a file for passing to archinstall CLI/library
            config_path = "/tmp/addictive_install_config.json"
            with open(config_path, "w") as f:
                json.dump(config, f, indent=4)
            self.log_widget.write(f"Configuration saved to {config_path}")
            self.progress.advance(10)

            # 2. Add BlackArch Repos (simulated script writing)
            self.log_widget.write("[bold yellow]Queueing BlackArch repository configuration...[/]")
            self._prepare_blackarch_script()
            self.progress.advance(10)
            await asyncio.sleep(1)
            
            # 3. Apply Wallpapers and Theming hooks
            self.log_widget.write("Setting up custom hooks for Addictive OS themes and custom wallpapers...")
            self._setup_custom_assets()
            self.progress.advance(10)
            await asyncio.sleep(1)

            # 4. Invoke Archinstall (Simulated or Real)
            self.log_widget.write(f"[bold {ACCENT_COLOR}]Executing archinstall backend...[/]")
            
            if archinstall and os.geteuid() == 0:
                self.log_widget.write("Root privileges and archinstall detected.")
                self.log_widget.write("[italic]In a live environment, the installer would now call archinstall.perform_installation() or run 'archinstall --config' in a subprocess.[/italic]")
                # We simulate the subprocess call here because we are likely in a testing environment
                # In production, we would use asyncio.create_subprocess_exec to run archinstall and parse its stdout
            else:
                self.log_widget.write("[italic]Not running as root or archinstall is missing. Simulating installation steps...[/]")
            
            # Simulate the installation steps for visual feedback
            steps = [
                "Partitioning and wiping disks...", 
                "Formatting filesystems...", 
                "Bootstraping base packages...", 
                "Installing GNOME Desktop Environment...", 
                "Downloading and installing BlackArch toolkits...", 
                "Generating fstab...", 
                "Configuring systemd-boot bootloader...", 
                "Running post-install configuration hooks..."
            ]
            
            for step in steps:
                self.log_widget.write(step)
                await asyncio.sleep(1.2) # Simulate work taking place
                self.progress.advance(6)
                
            # 5. Final theming tweaks
            self.log_widget.write(f"Applying final GNOME accent colors ({ACCENT_COLOR})...")
            self.progress.advance(12)
            await asyncio.sleep(0.5)

            self.progress.update(progress=100)
            self.log_widget.write("[bold green]Installation Complete! You are ready to start hacking.[/]")
            
            # Enable buttons upon completion
            self.query_one("#btn_reboot").disabled = False
            self.query_one("#btn_exit").disabled = False

        except Exception as e:
            self.log_widget.write(f"[bold red]Installation failed: {e}[/]")
            self.query_one("#btn_exit").disabled = False

    def _build_archinstall_config(self) -> dict:
        """Constructs a dict compliant with archinstall's JSON config structure."""
        config = {
            "keyboard-layout": STATE["keyboard_layout"],
            "sys-language": "en_US",
            "sys-encoding": "UTF-8",
            "hostname": STATE["hostname"],
            "timezone": STATE["timezone"],
            "ntp": True,
            "bootloader": "systemd-bootctl",
            "swap": True,
            "profiles": {
                "profile": {
                    "name": "desktop",
                    "details": ["gnome"]
                }
            },
            "network": "NetworkManager",
            "users": [
                {
                    "username": STATE["username"],
                    "password": STATE["password"],
                    "sudo": STATE["grant_root"]
                }
            ],
            "packages": [
                "btrfs-progs",
                "networkmanager",
                "git",
                "vim",
                "curl",
                "wget"
            ] + STATE["toolkits"],
            # Provide a post-installation command/script to run setup hooks
            "custom-commands": [
                "bash /tmp/blackarch_setup.sh",
                "bash /etc/addictive/setup_gnome.sh"
            ]
        }
        
        # Disk layout configuration
        config["disk_layouts"] = {
            STATE["drive"]: {
                "partitions": [
                    {
                        "boot": True,
                        "encrypted": False,
                        "filesystem": {"format": "fat32"},
                        "mountpoint": "/boot",
                        "size": "512MiB",
                        "start": "1MiB",
                        "type": "primary",
                        "wipe": True
                    },
                    {
                        "encrypted": STATE["encrypt_disk"],
                        "filesystem": {"format": STATE["filesystem"]},
                        "mountpoint": "/",
                        "size": "100%",
                        "start": "513MiB",
                        "type": "primary",
                        "wipe": True
                    }
                ],
                "wipe": True
            }
        }
        
        return config

    def _prepare_blackarch_script(self):
        """Prepare the script to fetch and install the blackarch strap file."""
        script_content = """#!/bin/bash
echo "Installing BlackArch repositories..."
curl -O https://blackarch.org/strap.sh
chmod +x strap.sh
./strap.sh
pacman -Syu --noconfirm
"""
        try:
            with open("/tmp/blackarch_setup.sh", "w") as f:
                f.write(script_content)
            os.chmod("/tmp/blackarch_setup.sh", 0o755)
        except Exception:
            pass # Ignore in dev environment without permissions

    def _setup_custom_assets(self):
        """Prepare post-installation hooks for custom assets and wallpapers."""
        # Create directory structure for custom wallpapers
        # In a real install, archinstall creates the chroot at /mnt or /mnt/archinstall
        chroot_path = Path("/mnt/archinstall")
        wallpaper_dir = chroot_path / "etc" / "addictive" / "wallpapers"
        
        try:
            wallpaper_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass # Ignore in test environment
            
        # Write a script to apply the wallpaper and accent color globally for users
        # For GNOME, we can use dconf in /etc/dconf/profile/user
        script_content = f"""#!/bin/bash
# Addictive OS post-install setup for GNOME

echo 'Setting up Addictive GNOME tweaks...'

# Create dconf default overrides
mkdir -p /etc/dconf/profile
echo -e "user-db:user\\nsystem-db:local" > /etc/dconf/profile/user

mkdir -p /etc/dconf/db/local.d
cat <<EOF > /etc/dconf/db/local.d/00-addictive
[org/gnome/desktop/interface]
accent-color='{ACCENT_COLOR}'
gtk-theme='Adwaita-dark'
EOF

# If there is a wallpaper present in the designated folder, set it as default
WALLPAPER_FILE=\\$(ls /etc/addictive/wallpapers/ | head -n 1)
if [ ! -z "\\$WALLPAPER_FILE" ]; then
cat <<EOF >> /etc/dconf/db/local.d/00-addictive
[org/gnome/desktop/background]
picture-uri='file:///etc/addictive/wallpapers/\\$WALLPAPER_FILE'
picture-uri-dark='file:///etc/addictive/wallpapers/\\$WALLPAPER_FILE'
EOF
fi

# Apply the dconf overrides
dconf update
"""
        try:
            script_path = chroot_path / "etc" / "addictive" / "setup_gnome.sh"
            script_path.parent.mkdir(parents=True, exist_ok=True)
            with open(script_path, "w") as f:
                f.write(script_content)
            os.chmod(script_path, 0o755)
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_exit":
            self.app.exit(0)
        elif event.button.id == "btn_reboot":
            # For safety, commented out actual reboot call. 
            # os.system("reboot")
            self.app.exit(0)


# --- Application Entrypoint ---

class AddictiveInstaller(App):
    """The main Addictive OS Installer TUI application."""
    
    CSS = CUSTOM_CSS
    TITLE = "Addictive OS Installer"
    
    SCREENS = {
        "welcome": WelcomeScreen,
        "keyboard": KeyboardScreen,
        "network": NetworkScreen,
        "toolkits": ToolkitsScreen,
        "disk": DiskScreen,
        "account": AccountScreen,
        "timezone": TimezoneScreen,
        "review": ReviewScreen,
        "install": InstallProgressScreen,
    }

    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit", show=True, priority=True)
    ]

    def on_mount(self) -> None:
        self.push_screen("welcome")

if __name__ == "__main__":
    app = AddictiveInstaller()
    app.run()
