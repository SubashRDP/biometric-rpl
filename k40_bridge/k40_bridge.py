#!/usr/bin/env python3
"""
K40 Bridge — multi-device, multi-site sync from ZKTeco K40 to ERPNext.

Run: python3 k40_bridge.py        (or k40_bridge.exe on Windows)
On first run, a setup wizard appears. Configuration is saved to config.json
next to the executable.
"""

import json
import logging
import logging.handlers
import os
import platform
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import date, datetime
from tkinter import StringVar, Tk, Toplevel, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import requests
from zk import ZK

try:
    import pystray
    from PIL import Image, ImageDraw
    HAS_TRAY = True
except ImportError:
    HAS_TRAY = False

IS_WINDOWS = platform.system() == "Windows"
TASK_NAME = "K40 Bridge"

# ============================================
# CONSTANTS
# ============================================
WEBHOOK_PATH = (
    "/api/method/biometric_integration.biometric_integration."
    "biometric_integration.zkteco_push_attendance"
)

APP_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))
CONFIG_FILE = os.path.join(APP_DIR, "config.json")
SYNCED_RECORDS_FILE = os.path.join(APP_DIR, "k40_synced.json")
NEXT_SYNC_FILE = os.path.join(APP_DIR, "next_sync.json")
LOG_FILE = os.path.join(APP_DIR, "k40_bridge.log")

DEFAULT_CONFIG = {
    "sync_interval_minutes": 1440,
    "log_level": "INFO",
    "log_max_size_mb": 10,
    "log_backup_count": 5,
    "device_timeout_seconds": 5,
    "network_probe_retries": 3,
    "devices": [],
}

INTERVAL_OPTIONS = [
    ("1 day", 1440),
    ("1 hour", 60),
    ("30 minutes", 30),
    ("15 minutes", 15),
    ("10 minutes", 10),
    ("5 minutes", 5),
    ("2 minutes", 2),
]


# ============================================
# WINDOWS AUTO-START (Task Scheduler integration)
# ============================================
def _is_admin():
    if not IS_WINDOWS:
        return False
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def autostart_status():
    """Return True if the K40 Bridge scheduled task exists."""
    if not IS_WINDOWS:
        return False
    try:
        result = subprocess.run(
            ["schtasks", "/Query", "/TN", TASK_NAME],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def autostart_install():
    """Register the bridge as a Windows scheduled task.
    Auto-start at boot, restart on failure, highest privilege.
    Returns (ok, message)."""
    if not IS_WINDOWS:
        return False, "Auto-start is only supported on Windows."
    if not _is_admin():
        return False, (
            "Administrator rights required.\n\n"
            "Close the bridge, then right-click k40_bridge.exe → "
            "Run as administrator, then click Enable Auto-Start again."
        )

    exe_path = os.path.abspath(sys.argv[0])
    work_dir = os.path.dirname(exe_path)

    xml = (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        '<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
        '  <Triggers>\n'
        '    <BootTrigger><Enabled>true</Enabled></BootTrigger>\n'
        '    <LogonTrigger><Enabled>true</Enabled></LogonTrigger>\n'
        '  </Triggers>\n'
        '  <Principals>\n'
        '    <Principal id="Author">\n'
        '      <RunLevel>HighestAvailable</RunLevel>\n'
        '      <LogonType>InteractiveToken</LogonType>\n'
        '    </Principal>\n'
        '  </Principals>\n'
        '  <Settings>\n'
        '    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n'
        '    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n'
        '    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>\n'
        '    <RestartOnFailure>\n'
        '      <Interval>PT1M</Interval>\n'
        '      <Count>999</Count>\n'
        '    </RestartOnFailure>\n'
        '    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n'
        '    <AllowHardTerminate>true</AllowHardTerminate>\n'
        '    <StartWhenAvailable>true</StartWhenAvailable>\n'
        '  </Settings>\n'
        '  <Actions>\n'
        f'    <Exec>\n'
        f'      <Command>{exe_path}</Command>\n'
        f'      <WorkingDirectory>{work_dir}</WorkingDirectory>\n'
        '    </Exec>\n'
        '  </Actions>\n'
        '</Task>\n'
    )

    fd, xml_path = tempfile.mkstemp(suffix=".xml")
    try:
        os.close(fd)
        with open(xml_path, "w", encoding="utf-16") as f:
            f.write(xml)
        result = subprocess.run(
            ["schtasks", "/Create", "/XML", xml_path, "/TN", TASK_NAME, "/F"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return True, "Auto-start enabled. Bridge will launch at every boot."
        return False, (result.stderr or result.stdout or "Unknown error").strip()
    except Exception as e:
        return False, str(e)
    finally:
        try:
            os.remove(xml_path)
        except Exception:
            pass


def autostart_uninstall():
    """Remove the K40 Bridge scheduled task."""
    if not IS_WINDOWS:
        return False, "Auto-start is only supported on Windows."
    if not _is_admin():
        return False, "Administrator rights required."
    try:
        result = subprocess.run(
            ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return True, "Auto-start disabled."
        return False, (result.stderr or result.stdout or "Unknown error").strip()
    except Exception as e:
        return False, str(e)


# ============================================
# CONFIG I/O
# ============================================
def load_config():
    if not os.path.exists(CONFIG_FILE):
        return None
    try:
        with open(CONFIG_FILE) as f:
            cfg = json.load(f)
        for k, v in DEFAULT_CONFIG.items():
            cfg.setdefault(k, v)
        return cfg
    except Exception:
        return None


def save_config(config):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


# ============================================
# LOGGING
# ============================================
class GuiLogHandler(logging.Handler):
    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    def emit(self, record):
        try:
            self.callback(self.format(record))
        except Exception:
            pass


def setup_logging(config, gui_callback=None):
    logger = logging.getLogger("k40_bridge")
    logger.setLevel(getattr(logging, config.get("log_level", "INFO"), logging.INFO))
    logger.handlers.clear()

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-5s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    max_bytes = int(config.get("log_max_size_mb", 10)) * 1024 * 1024
    backup_count = int(config.get("log_backup_count", 5))
    fh = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    if gui_callback:
        gh = GuiLogHandler(gui_callback)
        gh.setFormatter(fmt)
        logger.addHandler(gh)

    return logger


# ============================================
# DEDUP STATE (one file shared across all devices)
# ============================================
class DedupStore:
    def __init__(self):
        self.synced = set()
        if os.path.exists(SYNCED_RECORDS_FILE):
            try:
                with open(SYNCED_RECORDS_FILE) as f:
                    self.synced = set(json.load(f))
            except Exception:
                self.synced = set()

    def is_synced(self, key):
        return key in self.synced

    def mark(self, key):
        self.synced.add(key)

    def save(self):
        try:
            with open(SYNCED_RECORDS_FILE, "w") as f:
                json.dump(list(self.synced), f)
        except Exception:
            pass


# ============================================
# DEVICE CLIENT
# ============================================
class DeviceClient:
    def __init__(self, device, timeout=5, retries=3):
        self.device = device
        self.timeout = timeout
        self.retries = retries

    def probe_network(self):
        """Fast TCP probe. Returns True if reachable on port within retries."""
        host = self.device["ip"]
        port = int(self.device.get("port", 4370))
        for attempt in range(self.retries):
            try:
                sock = socket.create_connection((host, port), timeout=self.timeout)
                sock.close()
                return True
            except (socket.timeout, socket.error, OSError):
                if attempt < self.retries - 1:
                    time.sleep(min(2 ** attempt, 4))
        return False

    def fetch_attendance(self, date_filter=None):
        """Pull attendance records. Returns (records, status_str)."""
        if not self.probe_network():
            return [], "UNREACHABLE"

        try:
            conn = ZK(
                self.device["ip"],
                port=int(self.device.get("port", 4370)),
                timeout=self.timeout,
            )
            zk = conn.connect()
            zk.disable_device()
            try:
                attendances = zk.get_attendance()
            finally:
                try:
                    zk.enable_device()
                except Exception:
                    pass
                try:
                    zk.disconnect()
                except Exception:
                    pass

            if date_filter:
                attendances = [a for a in attendances if a.timestamp.date() == date_filter]
            return attendances, "OK"
        except Exception as e:
            return [], f"ERROR: {e}"


# ============================================
# ERPNEXT CLIENT (with API token auth)
# ============================================
class ErpnextClient:
    def __init__(self, device):
        self.device = device
        base = device["erpnext_url"].rstrip("/")
        self.url = base + WEBHOOK_PATH
        self.auth_header = f"token {device['api_key']}:{device['api_secret']}"

    def test_connection(self):
        """Verify auth using a built-in Frappe method. Returns (ok, message)."""
        base = self.device["erpnext_url"].rstrip("/")
        try:
            r = requests.get(
                base + "/api/method/frappe.auth.get_logged_user",
                headers={"Authorization": self.auth_header},
                timeout=10,
            )
            if r.status_code == 200:
                user = r.json().get("message", "?")
                return True, f"authenticated as {user}"
            return False, f"HTTP {r.status_code}: {r.text[:200]}"
        except Exception as e:
            return False, str(e)

    def push_punch(self, user_id, timestamp_str):
        """Send a single punch. Returns (result, message).
        Result is one of: 'synced', 'skipped', 'auth_fail', 'error'."""
        payload = {
            "device_id": self.device.get("serial", ""),
            "employee_id": str(user_id),
            "punch_time": timestamp_str,
            "punch_type": "IN",
        }
        try:
            r = requests.post(
                self.url,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": self.auth_header,
                },
                timeout=15,
            )
        except Exception as e:
            return "error", str(e)

        if r.status_code in (401, 403):
            return "auth_fail", f"HTTP {r.status_code}"
        if r.status_code != 200:
            return "error", f"HTTP {r.status_code}: {r.text[:200]}"

        try:
            msg = r.json().get("message", {})
            if isinstance(msg, dict):
                details = msg.get("error_details") or []
                for d in details:
                    if "already has a log with the same timestamp" in str(d):
                        return "skipped", "already exists"
                if msg.get("errors", 0) > 0 and msg.get("synced", 0) == 0:
                    return "error", "; ".join(str(d) for d in details[:2])
        except Exception:
            pass

        return "synced", None


# ============================================
# SYNC ENGINE (background thread)
# ============================================
class SyncEngine:
    def __init__(self, config, logger, status_callback):
        self.config = config
        self.logger = logger
        self.status_callback = status_callback  # fn(device_name, state, msg)
        self.dedup = DedupStore()
        self.paused = False
        self.stop_event = threading.Event()
        self.force_event = threading.Event()
        self.force_subset = None  # None=all, list=specific
        self.thread = None
        self.last_sync_per_device = {}
        self.next_sync_at = None

    def start(self):
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.force_event.set()

    def force_sync(self, device_names=None):
        self.force_subset = device_names
        self.force_event.set()

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    def _load_next_sync(self):
        if not os.path.exists(NEXT_SYNC_FILE):
            return None
        try:
            with open(NEXT_SYNC_FILE) as f:
                return float(json.load(f).get("next_sync_at", 0))
        except Exception:
            return None

    def _save_next_sync(self, ts):
        try:
            with open(NEXT_SYNC_FILE, "w") as f:
                json.dump({"next_sync_at": ts, "saved_at": time.time()}, f)
        except Exception:
            pass

    def _loop(self):
        interval = int(self.config.get("sync_interval_minutes", 1440)) * 60
        saved_next = self._load_next_sync()
        now = time.time()

        # If we have a previously scheduled time and it's in the past
        # (computer was off when sync was due) → catch up immediately.
        # If it's in the future → resume that schedule without re-syncing.
        # If no saved state (first run) → sync immediately.
        if saved_next is None:
            self.logger.info("First run — running initial sync")
            self.run_sync()
            self.next_sync_at = time.time() + interval
            self._save_next_sync(self.next_sync_at)
        elif saved_next <= now:
            overdue_min = int((now - saved_next) / 60)
            self.logger.info(
                f"Catch-up sync — scheduled time was {overdue_min} min ago (computer was off?)"
            )
            self.run_sync()
            self.next_sync_at = time.time() + interval
            self._save_next_sync(self.next_sync_at)
        else:
            self.next_sync_at = saved_next
            wait_min = int((saved_next - now) / 60)
            self.logger.info(f"Resuming schedule — next sync in {wait_min} min")

        while not self.stop_event.is_set():
            interval = int(self.config.get("sync_interval_minutes", 1440)) * 60
            wait_time = max(0.0, self.next_sync_at - time.time())
            woken = self.force_event.wait(timeout=wait_time)
            if self.stop_event.is_set():
                break
            if woken:
                self.force_event.clear()
                subset = self.force_subset
                self.force_subset = None
                self.run_sync(subset)
                # Force-sync does NOT reset the scheduled time —
                # the regular cycle stays on its rhythm.
            elif not self.paused:
                self.run_sync()
                self.next_sync_at = time.time() + interval
                self._save_next_sync(self.next_sync_at)

    def run_sync(self, device_names=None):
        devices = self.config.get("devices", [])
        if device_names is not None:
            devices = [d for d in devices if d["name"] in device_names]
        for device in devices:
            if self.stop_event.is_set():
                return
            self._sync_one(device)

    def _sync_one(self, device):
        name = device["name"]
        self.status_callback(name, "syncing", None)
        self.logger.info(f"[{name}] sync_cycle: starting")

        client = DeviceClient(
            device,
            timeout=int(self.config.get("device_timeout_seconds", 5)),
            retries=int(self.config.get("network_probe_retries", 3)),
        )

        today = date.today()
        attendances, status = client.fetch_attendance(date_filter=today)

        if status == "UNREACHABLE":
            self.logger.warning(
                f"[{name}] device unreachable: "
                f"{device['ip']}:{device.get('port', 4370)} "
                f"(timeout after {self.config.get('device_timeout_seconds', 5)}s "
                f"x {self.config.get('network_probe_retries', 3)} retries)"
            )
            self.status_callback(name, "unreachable", f"{device['ip']} not on network")
            return

        if status != "OK":
            self.logger.error(f"[{name}] fetch failed: {status}")
            self.status_callback(name, "error", status[:80])
            return

        self.logger.info(f"[{name}] fetched {len(attendances)} records")

        erpnext = ErpnextClient(device)
        synced = skipped = errors = 0
        last_err = None

        for att in attendances:
            if self.stop_event.is_set():
                return
            key = f"{device.get('serial', '')}_{att.user_id}_{att.timestamp}"
            if self.dedup.is_synced(key):
                skipped += 1
                continue

            ts_str = att.timestamp.strftime("%Y-%m-%d %H:%M:%S")
            result, err = erpnext.push_punch(att.user_id, ts_str)

            if result == "synced":
                synced += 1
                self.dedup.mark(key)
            elif result == "skipped":
                skipped += 1
                self.dedup.mark(key)
            elif result == "auth_fail":
                self.logger.error(f"[{name}] auth failed: {err}")
                self.status_callback(name, "auth_fail", err or "401/403")
                self.dedup.save()
                return
            else:
                errors += 1
                last_err = err
                self.logger.error(f"[{name}] push error: {err}")

        self.dedup.save()
        self.last_sync_per_device[name] = datetime.now().strftime("%H:%M:%S")
        self.logger.info(
            f"[{name}] sync_cycle: synced={synced} skipped={skipped} errors={errors}"
        )

        if errors > 0:
            self.status_callback(name, "error", f"{errors} errors ({last_err or 'see log'})")
        else:
            self.status_callback(name, "ok", f"{synced} new, {skipped} skipped")


# ============================================
# SETUP WIZARD
# ============================================
class SetupWizard:
    def __init__(self, parent, config=None, on_save=None):
        self.config = (config or DEFAULT_CONFIG).copy()
        self.config.setdefault("devices", [])
        self.on_save = on_save
        self.parent = parent

        self.window = Toplevel(parent) if parent.winfo_exists() else Tk()
        self.window.title("K40 Bridge Setup")
        self.window.geometry("780x640")
        self.window.minsize(680, 540)

        self.device_rows = []
        self._build()

    def _build(self):
        # ── Step 1: ERPNext Connection ──
        f1 = ttk.LabelFrame(self.window, text="Step 1: ERPNext Connection (used by all devices below)")
        f1.pack(fill="x", padx=10, pady=6)

        ttk.Label(f1, text="ERPNext URL:").grid(row=0, column=0, sticky="e", padx=6, pady=3)
        self.url_entry = ttk.Entry(f1, width=58)
        self.url_entry.grid(row=0, column=1, sticky="ew", padx=6, pady=3)

        ttk.Label(f1, text="API Key:").grid(row=1, column=0, sticky="e", padx=6, pady=3)
        self.key_entry = ttk.Entry(f1, width=58)
        self.key_entry.grid(row=1, column=1, sticky="ew", padx=6, pady=3)

        ttk.Label(f1, text="API Secret:").grid(row=2, column=0, sticky="e", padx=6, pady=3)
        self.secret_entry = ttk.Entry(f1, width=58, show="*")
        self.secret_entry.grid(row=2, column=1, sticky="ew", padx=6, pady=3)

        ttk.Button(f1, text="Test Connection", command=self._test).grid(row=3, column=1, sticky="w", padx=6, pady=3)
        self.test_label = ttk.Label(f1, text="")
        self.test_label.grid(row=4, column=1, sticky="w", padx=6, pady=3)

        # ── Step 2: Devices ──
        f2 = ttk.LabelFrame(self.window, text="Step 2: Devices")
        f2.pack(fill="both", expand=True, padx=10, pady=6)

        header = ttk.Frame(f2)
        header.pack(fill="x", padx=6, pady=(4, 2))
        for i, (text, width) in enumerate(
            [("Name", 18), ("IP", 16), ("Port", 8), ("Serial", 24), ("", 4)]
        ):
            ttk.Label(header, text=text, width=width, anchor="w").grid(row=0, column=i, padx=2)

        self.devices_frame = ttk.Frame(f2)
        self.devices_frame.pack(fill="both", expand=True, padx=6)

        ttk.Button(f2, text="+ Add Device", command=lambda: self._add_row()).pack(pady=4)

        # ── Step 3: Sync Frequency ──
        f3 = ttk.LabelFrame(self.window, text="Step 3: Sync Frequency")
        f3.pack(fill="x", padx=10, pady=6)
        ttk.Label(f3, text="Sync every:").grid(row=0, column=0, sticky="e", padx=6, pady=3)
        self.interval_var = StringVar(value="1 day")
        ttk.Combobox(
            f3,
            textvariable=self.interval_var,
            values=[name for name, _ in INTERVAL_OPTIONS],
            state="readonly",
            width=16,
        ).grid(row=0, column=1, sticky="w", padx=6, pady=3)
        ttk.Label(
            f3,
            text="(Use the Force Sync Now button for ad-hoc real-time data.)",
            foreground="gray",
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=6, pady=2)

        # ── Buttons ──
        btns = ttk.Frame(self.window)
        btns.pack(fill="x", padx=10, pady=8)
        ttk.Button(btns, text="Save & Start", command=self._save).pack(side="right", padx=4)
        ttk.Button(btns, text="Cancel", command=self.window.destroy).pack(side="right", padx=4)

        self._populate_from_config()

    def _populate_from_config(self):
        existing = self.config.get("devices", [])
        if existing:
            first = existing[0]
            self.url_entry.insert(0, first.get("erpnext_url", ""))
            self.key_entry.insert(0, first.get("api_key", ""))
            self.secret_entry.insert(0, first.get("api_secret", ""))
            for d in existing:
                self._add_row(d)
            mins = int(self.config.get("sync_interval_minutes", 1440))
            for name, m in INTERVAL_OPTIONS:
                if m == mins:
                    self.interval_var.set(name)
                    break
        else:
            self._add_row()

    def _add_row(self, device=None):
        device = device or {"name": "", "ip": "", "port": 4370, "serial": ""}
        row = ttk.Frame(self.devices_frame)
        row.pack(fill="x", pady=1)

        entries = {}
        for i, (key, width, default) in enumerate(
            [
                ("name", 18, device.get("name", "")),
                ("ip", 16, device.get("ip", "")),
                ("port", 8, str(device.get("port", 4370))),
                ("serial", 24, device.get("serial", "")),
            ]
        ):
            e = ttk.Entry(row, width=width)
            e.insert(0, default)
            e.grid(row=0, column=i, padx=2)
            entries[key] = e

        def remove():
            row.destroy()
            self.device_rows[:] = [r for r in self.device_rows if r["frame"] is not row]

        ttk.Button(row, text="X", width=3, command=remove).grid(row=0, column=4, padx=2)
        entries["frame"] = row
        self.device_rows.append(entries)

    def _test(self):
        url = self.url_entry.get().strip()
        key = self.key_entry.get().strip()
        secret = self.secret_entry.get().strip()
        if not (url and key and secret):
            self.test_label.config(text="● Fill all fields first", foreground="orange")
            return
        self.test_label.config(text="● Testing...", foreground="gray")
        self.window.update_idletasks()
        client = ErpnextClient(
            {"erpnext_url": url, "api_key": key, "api_secret": secret, "serial": ""}
        )
        ok, msg = client.test_connection()
        if ok:
            self.test_label.config(text=f"● Connected ({msg})", foreground="green")
        else:
            self.test_label.config(text=f"● Failed: {msg[:80]}", foreground="red")

    def _save(self):
        url = self.url_entry.get().strip().rstrip("/")
        key = self.key_entry.get().strip()
        secret = self.secret_entry.get().strip()

        if not (url and key and secret):
            messagebox.showerror("Missing fields", "Please fill ERPNext URL, API Key, and API Secret.")
            return

        devices = []
        for r in self.device_rows:
            name = r["name"].get().strip()
            ip = r["ip"].get().strip()
            serial = r["serial"].get().strip()
            try:
                port = int(r["port"].get().strip() or 4370)
            except ValueError:
                port = 4370
            if not (name and ip and serial):
                continue
            devices.append(
                {
                    "name": name,
                    "ip": ip,
                    "port": port,
                    "serial": serial,
                    "erpnext_url": url,
                    "api_key": key,
                    "api_secret": secret,
                    "latitude": 27.7228,
                    "longitude": 85.3211,
                }
            )

        if not devices:
            messagebox.showerror("No devices", "Add at least one device with name, IP, and serial.")
            return

        interval_min = 1440
        for name, mins in INTERVAL_OPTIONS:
            if name == self.interval_var.get():
                interval_min = mins
                break

        self.config["devices"] = devices
        self.config["sync_interval_minutes"] = interval_min
        for k, v in DEFAULT_CONFIG.items():
            self.config.setdefault(k, v)

        save_config(self.config)
        self.window.destroy()
        if self.on_save:
            self.on_save(self.config)


# ============================================
# CONTROL PANEL
# ============================================
class ControlPanel:
    def __init__(self, config):
        self.config = config
        self.root = Tk()
        self.root.title("K40 Bridge")
        self.root.geometry("960x640")
        self.root.minsize(820, 520)

        self.logger = setup_logging(config, gui_callback=self._on_log)
        self.engine = SyncEngine(config, self.logger, self._on_status)
        self._build()
        self.engine.start()
        self._tick()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.tray_icon = None
        self._setup_tray()

    def _build(self):
        top = ttk.Frame(self.root)
        top.pack(fill="x", padx=10, pady=6)

        self.status_label = ttk.Label(top, text="● Running", foreground="green", font=("TkDefaultFont", 10, "bold"))
        self.status_label.pack(side="left")
        self.countdown_label = ttk.Label(top, text="")
        self.countdown_label.pack(side="left", padx=12)

        self.pause_btn = ttk.Button(top, text="Pause", command=self._toggle_pause)
        self.pause_btn.pack(side="right", padx=2)
        ttk.Button(top, text="Edit Config", command=self._edit_config).pack(side="right", padx=2)

        cols = ("name", "ip", "last_sync", "status")
        self.tree = ttk.Treeview(self.root, columns=cols, show="headings", height=10)
        self.tree.heading("name", text="Name")
        self.tree.heading("ip", text="IP : Port")
        self.tree.heading("last_sync", text="Last Sync")
        self.tree.heading("status", text="Status")
        self.tree.column("name", width=180)
        self.tree.column("ip", width=170)
        self.tree.column("last_sync", width=110)
        self.tree.column("status", width=440)
        self.tree.pack(fill="both", expand=True, padx=10, pady=6)

        self._populate_tree()

        btns = ttk.Frame(self.root)
        btns.pack(fill="x", padx=10, pady=4)
        ttk.Button(btns, text="Force Sync All", command=self._force_all).pack(side="left", padx=2)
        ttk.Button(btns, text="Force Sync Selected", command=self._force_selected).pack(side="left", padx=2)

        # Auto-start button (Windows only)
        if IS_WINDOWS:
            self.autostart_btn = ttk.Button(btns, text="…", command=self._toggle_autostart)
            self.autostart_btn.pack(side="left", padx=12)
            self._refresh_autostart_label()

        ttk.Button(btns, text="Open Log Folder", command=self._open_log_folder).pack(side="right", padx=2)

        ttk.Label(self.root, text="Recent log:").pack(anchor="w", padx=10, pady=(6, 0))
        self.log_text = ScrolledText(self.root, height=10, state="disabled", font=("Courier", 9))
        self.log_text.pack(fill="both", expand=True, padx=10, pady=6)

    def _populate_tree(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        for d in self.config.get("devices", []):
            self.tree.insert(
                "",
                "end",
                iid=d["name"],
                values=(d["name"], f"{d['ip']}:{d.get('port', 4370)}", "—", "pending"),
            )

    def _on_status(self, name, state, msg):
        self.root.after(0, lambda: self._update_status(name, state, msg))

    def _update_status(self, name, state, msg):
        if not self.tree.exists(name):
            return
        last_sync = self.engine.last_sync_per_device.get(name, "—")
        device = next((d for d in self.config["devices"] if d["name"] == name), None)
        ip_port = f"{device['ip']}:{device.get('port', 4370)}" if device else ""

        label = {
            "syncing": "⟳ syncing…",
            "ok": f"● OK — {msg or ''}",
            "unreachable": f"● UNREACHABLE — {msg or ''}",
            "error": f"● ERROR — {msg or ''}",
            "auth_fail": f"● AUTH FAIL — {msg or ''}",
            "pending": "pending",
        }.get(state, state)

        self.tree.item(name, values=(name, ip_port, last_sync, label))

    def _on_log(self, line):
        self.root.after(0, lambda: self._append_log(line))

    def _append_log(self, line):
        self.log_text.config(state="normal")
        self.log_text.insert("end", line + "\n")
        # cap to last 300 lines
        lines = int(self.log_text.index("end-1c").split(".")[0])
        if lines > 300:
            self.log_text.delete("1.0", f"{lines - 300}.0")
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def _force_all(self):
        self.logger.info("Force Sync All triggered from GUI")
        self.engine.force_sync()

    def _force_selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Select a device", "Select one or more rows in the table first.")
            return
        self.logger.info(f"Force Sync Selected from GUI: {list(sel)}")
        self.engine.force_sync(list(sel))

    def _toggle_pause(self):
        if self.engine.paused:
            self.engine.resume()
            self.status_label.config(text="● Running", foreground="green")
            self.pause_btn.config(text="Pause")
        else:
            self.engine.pause()
            self.status_label.config(text="● Paused", foreground="orange")
            self.pause_btn.config(text="Resume")

    def _edit_config(self):
        SetupWizard(self.root, config=self.config, on_save=self._on_config_saved)

    def _on_config_saved(self, new_config):
        self.config = new_config
        self.engine.config = new_config
        self._populate_tree()
        self.logger.info("Configuration updated from GUI")

    def _open_log_folder(self):
        if sys.platform == "win32":
            os.startfile(APP_DIR)
        elif sys.platform == "darwin":
            os.system(f'open "{APP_DIR}"')
        else:
            os.system(f'xdg-open "{APP_DIR}"')

    def _refresh_autostart_label(self):
        if not IS_WINDOWS or not hasattr(self, "autostart_btn"):
            return
        if autostart_status():
            self.autostart_btn.config(text="Auto-Start: ON")
        else:
            self.autostart_btn.config(text="Enable Auto-Start")

    def _toggle_autostart(self):
        if autostart_status():
            if not messagebox.askyesno(
                "Disable Auto-Start",
                "Disable auto-start on Windows boot?\n\n"
                "(The bridge will only run when you launch it manually.)",
            ):
                return
            ok, msg = autostart_uninstall()
        else:
            ok, msg = autostart_install()

        if ok:
            messagebox.showinfo("Auto-Start", msg)
            self.logger.info(f"Auto-start changed: {msg}")
        else:
            messagebox.showerror("Auto-Start", msg)
            self.logger.warning(f"Auto-start change failed: {msg}")
        self._refresh_autostart_label()

    def _tick(self):
        if self.engine.next_sync_at and not self.engine.paused:
            remaining = max(0, int(self.engine.next_sync_at - time.time()))
            h, rem = divmod(remaining, 3600)
            m, s = divmod(rem, 60)
            if h:
                txt = f"Next sync in: {h}h {m}m"
            elif m:
                txt = f"Next sync in: {m}m {s}s"
            else:
                txt = f"Next sync in: {s}s"
            self.countdown_label.config(text=txt)
        else:
            self.countdown_label.config(text="")
        self.root.after(1000, self._tick)

    def _setup_tray(self):
        """Create the system tray icon. Bridge keeps running when window is hidden."""
        if not HAS_TRAY:
            return
        try:
            img = Image.new("RGB", (64, 64), color=(40, 100, 200))
            d = ImageDraw.Draw(img)
            d.rectangle((10, 18, 54, 46), outline=(255, 255, 255), width=3)
            d.text((22, 22), "K40", fill=(255, 255, 255))

            menu = pystray.Menu(
                pystray.MenuItem("Show", self._show_window, default=True),
                pystray.MenuItem("Force Sync All", lambda: self.engine.force_sync()),
                pystray.MenuItem("Exit", self._real_exit),
            )
            self.tray_icon = pystray.Icon("k40_bridge", img, "K40 Bridge", menu)
            threading.Thread(target=self.tray_icon.run, daemon=True).start()
        except Exception as e:
            self.logger.warning(f"Tray icon could not start: {e}")
            self.tray_icon = None

    def _show_window(self, *_):
        self.root.after(0, lambda: (self.root.deiconify(), self.root.lift()))

    def _on_close(self):
        """X button = hide to tray, bridge keeps running."""
        if self.tray_icon:
            self.root.withdraw()
            self.logger.info("Window hidden to tray — bridge continues running")
        else:
            # No tray support — fall back to ask-before-exit behavior
            if messagebox.askyesno(
                "Exit K40 Bridge",
                "Closing this window will STOP the sync engine.\n\n"
                "Do you want to exit?",
            ):
                self.engine.stop()
                self.root.destroy()

    def _real_exit(self, *_):
        """Called from tray menu — fully exits the bridge."""
        self.engine.stop()
        if self.tray_icon:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
        self.root.after(0, self.root.destroy)

    def run(self):
        self.root.mainloop()


# ============================================
# MAIN
# ============================================
def main():
    config = load_config()

    if config is None or not config.get("devices"):
        boot = Tk()
        boot.withdraw()
        wizard_completed = {"value": False, "config": None}

        def on_save(c):
            wizard_completed["value"] = True
            wizard_completed["config"] = c
            boot.quit()

        SetupWizard(boot, on_save=on_save)
        boot.mainloop()
        boot.destroy()

        if not wizard_completed["value"]:
            return  # user cancelled
        config = wizard_completed["config"]

    cp = ControlPanel(config)
    cp.run()


if __name__ == "__main__":
    main()
