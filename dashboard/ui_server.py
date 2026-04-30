"""
ui_server.py - Tkinter desktop dashboard for visacom.

Run:
    python ui_server.py
"""

import csv
import logging
import queue
import sys
import threading
import time
import tkinter as tk
from collections import deque
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Dict, List, Optional

# Allow importing the visacom package from the sibling instruments/ directory.
sys.path.insert(0, str(Path(__file__).parent.parent / "instruments"))

from visacom import Fluke8845A, HiokiRM3545, KeysightDMM, YokogawaWT310
from visacom.discover import DiscoveredInstrument, discover

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

MEASURE_OPTIONS: Dict[str, List[str]] = {
    "keysight": ["AC Voltage", "DC Voltage", "Resistance"],
    "fluke": ["AC Voltage", "DC Voltage", "Resistance"],
    "yokogawa": ["All Power Quantities"],
    "hioki": ["Resistance"],
}

DEFAULT_MEASURE: Dict[str, str] = {
    "keysight": "AC Voltage",
    "fluke": "AC Voltage",
    "yokogawa": "All Power Quantities",
    "hioki": "Resistance",
}

DISPLAY_NAMES = {
    "keysight": "Keysight DMM",
    "fluke": "Fluke 8845A",
    "yokogawa": "Yokogawa WT310",
    "hioki": "Hioki RM3545",
}

APP_DIR = Path(__file__).parent
LOGO_PATH = APP_DIR / "static" / "noratel-logo.png"
COLORS = {
    "bg": "#0f131a",
    "panel": "#171d26",
    "card": "#1d2633",
    "card2": "#131922",
    "border": "#2d3645",
    "text": "#eef3f8",
    "muted": "#9ba7b6",
    "accent": "#2f9df4",
    "green": "#34c759",
    "yellow": "#f6c343",
    "red": "#ff5c5c",
}


def display_name(label: str) -> str:
    base = label.rsplit("_", 1)[0] if label.rsplit("_", 1)[-1].isdigit() else label
    suffix = ""
    if "_" in label and label.rsplit("_", 1)[-1].isdigit():
        suffix = f" #{label.rsplit('_', 1)[1]}"
    return f"{DISPLAY_NAMES.get(base, label)}{suffix}"


def fmt_num(value: Any) -> str:
    if value is None:
        return "---"
    if not isinstance(value, (int, float)):
        return str(value)
    value = float(value)
    magnitude = abs(value)
    if magnitude == 0:
        return "0.000"
    if magnitude >= 10000:
        return f"{value:.1f}"
    if magnitude >= 100:
        return f"{value:.2f}"
    if magnitude >= 1:
        return f"{value:.4f}"
    if magnitude >= 0.001:
        return f"{value:.6f}"
    return f"{value:.3e}"


def short_time(ts: str) -> str:
    return (ts.split("T")[1] if "T" in ts else ts)[:12]


def configure_for_measurement(inst: Any, base: str, mtype: str) -> None:
    if base == "fluke":
        enter_remote = getattr(inst, "_enter_remote", None)
        if callable(enter_remote):
            enter_remote()

    if base in ("keysight", "fluke"):
        if mtype == "DC Voltage":
            inst.configure_dc_voltage()
        elif mtype == "Resistance":
            inst.configure_resistance()
        else:
            inst.configure_ac_voltage()


def do_reading(label: str, inst: Any, base: str, mtype: str) -> Optional[dict]:
    """Blocking VISA read. Called from the measurement worker thread."""
    ts = datetime.now().isoformat(timespec="milliseconds")
    try:
        if base in ("keysight", "fluke"):
            configure_for_measurement(inst, base, mtype)
            if mtype == "DC Voltage":
                return {
                    "ts": ts,
                    "label": label,
                    "param": "DC Voltage",
                    "value": inst.read_dc_voltage(),
                    "unit": "V DC",
                }
            if mtype == "Resistance":
                return {
                    "ts": ts,
                    "label": label,
                    "param": "Resistance",
                    "value": inst.read_resistance(),
                    "unit": "Ohm",
                }
            return {
                "ts": ts,
                "label": label,
                "param": "AC Voltage",
                "value": inst.read_ac_voltage(),
                "unit": "V AC",
            }

        if base == "yokogawa":
            reading = inst.read_power()
            return {
                "ts": ts,
                "label": label,
                "param": "Power",
                "multi": True,
                "values": {
                    "Voltage": {"value": reading.voltage_V, "unit": "V"},
                    "Current": {"value": reading.current_A, "unit": "A"},
                    "Power": {"value": reading.power_W, "unit": "W"},
                    "Apparent": {"value": reading.apparent_VA, "unit": "VA"},
                    "Reactive": {"value": reading.reactive_var, "unit": "var"},
                    "PF": {"value": reading.power_factor, "unit": ""},
                    "Freq": {"value": reading.frequency_Hz, "unit": "Hz"},
                },
            }

        if base == "hioki":
            return {
                "ts": ts,
                "label": label,
                "param": "Resistance",
                "value": inst.read(),
                "unit": "Ohm",
            }
    except Exception as exc:
        return {"ts": ts, "label": label, "error": str(exc)}

    return None


class VisacomTkApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Autobench")
        self.geometry("1180x740")
        self.minsize(960, 600)

        self.discovered: Dict[str, DiscoveredInstrument] = {}
        self.instruments: Dict[str, Any] = {}
        self.measurements: Dict[str, str] = {}
        self.readings: deque = deque(maxlen=500)
        self.running = False
        self.scanning = False
        self.interval_s = tk.DoubleVar(value=2.0)
        self.interval_value = 2.0
        self.selected_measure = tk.StringVar(value="")

        self.events: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()
        self.measure_thread: Optional[threading.Thread] = None
        self.live_vars: Dict[str, Dict[str, tk.StringVar]] = {}
        self.instrument_locks: Dict[str, threading.RLock] = {}
        self.logo_image: Optional[tk.PhotoImage] = self._load_logo()

        self._configure_style()
        self._build_ui()
        self._set_status("Ready. Click Scan to discover instruments.", "info")
        self.after(100, self._process_events)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _configure_style(self) -> None:
        self.configure(bg=COLORS["bg"])
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", font=("Segoe UI", 10))
        style.configure("TFrame", background=COLORS["bg"])
        style.configure("Header.TFrame", background=COLORS["panel"])
        style.configure("Panel.TFrame", background=COLORS["panel"])
        style.configure("Card.TFrame", background=COLORS["card"])
        style.configure("TLabel", background=COLORS["bg"], foreground=COLORS["text"])
        style.configure("Header.TLabel", background=COLORS["panel"], foreground=COLORS["text"])
        style.configure("Panel.TLabel", background=COLORS["panel"], foreground=COLORS["text"])
        style.configure("Card.TLabel", background=COLORS["card"], foreground=COLORS["text"])
        style.configure("Muted.TLabel", background=COLORS["panel"], foreground=COLORS["muted"])
        style.configure("CardMuted.TLabel", background=COLORS["card"], foreground=COLORS["muted"])
        style.configure("Value.TLabel", background=COLORS["card"], foreground=COLORS["text"], font=("Consolas", 22, "bold"))
        style.configure("TButton", background=COLORS["card2"], foreground=COLORS["text"], bordercolor=COLORS["border"], padding=(11, 6))
        style.map("TButton", background=[("active", COLORS["card"]), ("disabled", COLORS["panel"])])
        style.configure("Accent.TButton", background=COLORS["accent"], foreground="#ffffff", bordercolor=COLORS["accent"])
        style.configure("Green.TButton", background="#173c25", foreground=COLORS["green"], bordercolor="#245f38")
        style.configure("Red.TButton", background="#3b1b1f", foreground=COLORS["red"], bordercolor="#693037")
        style.configure("Ghost.TButton", background=COLORS["panel"], foreground=COLORS["muted"], bordercolor=COLORS["border"])
        style.configure(
            "Treeview",
            background=COLORS["card2"],
            fieldbackground=COLORS["card2"],
            foreground=COLORS["text"],
            bordercolor=COLORS["border"],
            rowheight=30,
        )
        style.configure("Treeview.Heading", background=COLORS["panel"], foreground=COLORS["muted"], relief=tk.FLAT)
        style.map("Treeview", background=[("selected", "#1d5f91")], foreground=[("selected", "#ffffff")])
        style.configure("TCombobox", fieldbackground=COLORS["card2"], background=COLORS["card2"], foreground=COLORS["text"])

    def _load_logo(self) -> Optional[tk.PhotoImage]:
        if not LOGO_PATH.exists():
            return None
        try:
            image = tk.PhotoImage(file=str(LOGO_PATH))
            width = image.width()
            if width > 190:
                image = image.subsample(max(1, width // 170))
            return image
        except tk.TclError:
            return None

    def _build_ui(self) -> None:
        toolbar = ttk.Frame(self, style="Header.TFrame", padding=(16, 10))
        toolbar.pack(fill=tk.X)

        brand = ttk.Frame(toolbar, style="Header.TFrame")
        brand.pack(side=tk.LEFT, padx=(0, 18))
        if self.logo_image is not None:
            ttk.Label(brand, image=self.logo_image, style="Header.TLabel").pack(side=tk.LEFT, padx=(0, 12))
        wordmark = ttk.Frame(brand, style="Header.TFrame")
        wordmark.pack(side=tk.LEFT)
        ttk.Label(wordmark, text="AUTOBENCH", style="Header.TLabel", font=("Segoe UI", 15, "bold")).pack(anchor=tk.W)
        ttk.Label(wordmark, text="Noratel instrument dashboard", style="Header.TLabel", foreground=COLORS["muted"], font=("Segoe UI", 9)).pack(anchor=tk.W)

        self.scan_btn = ttk.Button(toolbar, text="Scan", style="Accent.TButton", command=self.scan)
        self.scan_btn.pack(side=tk.LEFT, padx=(0, 6))
        self.start_btn = ttk.Button(toolbar, text="Start", style="Green.TButton", command=self.start)
        self.start_btn.pack(side=tk.LEFT, padx=6)
        self.stop_btn = ttk.Button(toolbar, text="Stop", style="Red.TButton", command=self.stop)
        self.stop_btn.pack(side=tk.LEFT, padx=6)

        ttk.Label(toolbar, text="Interval", style="Header.TLabel").pack(side=tk.LEFT, padx=(18, 6))
        self.interval_scale = ttk.Scale(
            toolbar,
            from_=0.5,
            to=30.0,
            variable=self.interval_s,
            orient=tk.HORIZONTAL,
            length=130,
            command=lambda _value: self._refresh_interval_label(),
        )
        self.interval_scale.pack(side=tk.LEFT)
        self.interval_label = ttk.Label(toolbar, text="2.0 s", style="Header.TLabel", width=7)
        self.interval_label.pack(side=tk.LEFT, padx=(5, 14))

        ttk.Button(toolbar, text="CSV", command=self.export_csv).pack(side=tk.RIGHT, padx=(6, 0))
        ttk.Button(toolbar, text="Clear", command=self.clear_readings).pack(side=tk.RIGHT)

        body = ttk.Frame(self, padding=(14, 14, 14, 12))
        body.pack(fill=tk.BOTH, expand=True)

        sidebar = ttk.Frame(body, style="Panel.TFrame", padding=14, width=350)
        sidebar.pack(side=tk.LEFT, fill=tk.Y)
        sidebar.pack_propagate(False)

        ttk.Label(sidebar, text="Instruments", style="Panel.TLabel", font=("Segoe UI", 12, "bold")).pack(anchor=tk.W)
        self.instrument_tree = ttk.Treeview(
            sidebar,
            columns=("status", "measure"),
            show="tree headings",
            height=13,
            selectmode="browse",
        )
        self.instrument_tree.heading("#0", text="Device")
        self.instrument_tree.heading("status", text="Status")
        self.instrument_tree.heading("measure", text="Measure")
        self.instrument_tree.column("#0", width=145)
        self.instrument_tree.column("status", width=80, anchor=tk.CENTER)
        self.instrument_tree.column("measure", width=90)
        self.instrument_tree.tag_configure("connected", foreground=COLORS["green"])
        self.instrument_tree.tag_configure("failed", foreground=COLORS["red"])
        self.instrument_tree.pack(fill=tk.BOTH, expand=True, pady=(10, 12))
        self.instrument_tree.bind("<<TreeviewSelect>>", self._on_instrument_select)

        ttk.Label(sidebar, text="Measurement", style="Muted.TLabel").pack(anchor=tk.W)
        self.measure_combo = ttk.Combobox(sidebar, textvariable=self.selected_measure, state="readonly")
        self.measure_combo.pack(fill=tk.X, pady=(4, 8))
        self.measure_combo.bind("<<ComboboxSelected>>", self._on_measure_change)
        self.disconnect_btn = ttk.Button(sidebar, text="Disconnect Selected", style="Red.TButton", command=self.disconnect_selected)
        self.disconnect_btn.pack(fill=tk.X, pady=(0, 12))

        self.detail_text = tk.Text(
            sidebar,
            height=7,
            wrap=tk.WORD,
            bg=COLORS["card2"],
            fg=COLORS["muted"],
            insertbackground=COLORS["text"],
            relief=tk.FLAT,
            padx=8,
            pady=8,
        )
        self.detail_text.pack(fill=tk.X)
        self.detail_text.insert("1.0", "Select an instrument to see IDN and resource details.")
        self.detail_text.configure(state=tk.DISABLED)

        content = ttk.Frame(body, padding=(16, 0, 0, 0))
        content.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        summary = ttk.Frame(content, style="Card.TFrame", padding=14)
        summary.pack(fill=tk.X, pady=(0, 14))
        ttk.Label(summary, text="Live Bench", style="Card.TLabel", font=("Segoe UI", 15, "bold")).pack(side=tk.LEFT)
        self.summary_label = ttk.Label(summary, text="0 connected", style="CardMuted.TLabel")
        self.summary_label.pack(side=tk.RIGHT)

        ttk.Label(content, text="Live Readings", font=("Segoe UI", 12, "bold")).pack(anchor=tk.W)
        self.live_frame = ttk.Frame(content)
        self.live_frame.pack(fill=tk.X, pady=(8, 14))

        log_head = ttk.Frame(content)
        log_head.pack(fill=tk.X)
        ttk.Label(log_head, text="Data Log", font=("Segoe UI", 12, "bold")).pack(side=tk.LEFT)
        self.read_count = ttk.Label(log_head, text="0 readings")
        self.read_count.pack(side=tk.RIGHT)

        self.log_tree = ttk.Treeview(
            content,
            columns=("time", "instrument", "parameter", "value", "unit"),
            show="headings",
        )
        for col, title, width in (
            ("time", "Time", 120),
            ("instrument", "Instrument", 180),
            ("parameter", "Parameter", 120),
            ("value", "Value", 140),
            ("unit", "Unit", 70),
        ):
            self.log_tree.heading(col, text=title)
            self.log_tree.column(col, width=width, anchor=tk.W)
        self.log_tree.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        statusbar = ttk.Frame(self, style="Panel.TFrame", padding=(14, 7))
        statusbar.pack(fill=tk.X)
        self.status_label = ttk.Label(statusbar, text="", style="Muted.TLabel")
        self.status_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.state_label = ttk.Label(statusbar, text="Stopped", style="Muted.TLabel")
        self.state_label.pack(side=tk.RIGHT)
        self._refresh_buttons()

    def scan(self) -> None:
        if self.scanning:
            return
        self.stop()
        self.scanning = True
        self._refresh_buttons()
        self._set_status("Scanning for instruments...", "info")
        threading.Thread(target=self._scan_worker, daemon=True).start()

    def _scan_worker(self) -> None:
        self._wait_for_measure_thread()
        self._close_instruments()
        found = discover()
        connected: Dict[str, Any] = {}
        measurements = dict(self.measurements)

        for label, disc in found.items():
            try:
                if disc.label == "keysight":
                    inst = KeysightDMM(disc.resource_name, timeout_ms=5_000)
                    inst.configure_ac_voltage()
                elif disc.label == "fluke":
                    inst = Fluke8845A(disc.resource_name, timeout_ms=10_000)
                    inst.configure_ac_voltage()
                elif disc.label == "yokogawa":
                    inst = YokogawaWT310(disc.resource_name, timeout_ms=10_000)
                    inst.configure_auto_range()
                elif disc.label == "hioki":
                    inst = HiokiRM3545(disc.resource_name, timeout_ms=15_000)
                    inst.initialize(line_freq=50, speed="MED", auto_range=True)
                    inst.set_continuous(False)
                else:
                    continue
                connected[label] = inst
                measurements.setdefault(label, DEFAULT_MEASURE.get(disc.label, ""))
            except Exception as exc:
                logger.warning("Connect failed [%s]: %s", label, exc)

        self.events.put(("scan_done", found, connected, measurements))

    def start(self) -> None:
        if self.running:
            return
        if not self.instruments:
            self._set_status("No instruments connected. Run Scan first.", "warn")
            return
        self.running = True
        self.stop_event.clear()
        self.measure_thread = threading.Thread(target=self._measure_loop, daemon=True)
        self.measure_thread.start()
        self._set_status("Measurement started.", "info")
        self._refresh_buttons()

    def stop(self) -> None:
        if not self.running:
            self._refresh_buttons()
            return
        self.running = False
        self.stop_event.set()
        self._set_status("Measurement stopped.", "info")
        self._refresh_buttons()

    def disconnect_selected(self) -> None:
        selection = self.instrument_tree.selection()
        if not selection:
            self._set_status("Select an instrument to disconnect.", "warn")
            return
        self.disconnect_instrument(selection[0])

    def disconnect_instrument(self, label: str) -> None:
        if label not in self.instruments:
            self._set_status(f"{display_name(label)} is already disconnected.", "warn")
            return
        was_running = self.running
        self.stop()
        self._wait_for_measure_thread()

        inst = self.instruments.pop(label, None)
        if inst is not None:
            try:
                inst.close()
            except Exception as exc:
                logger.warning("Disconnect failed [%s]: %s", label, exc)

        self._refresh_instruments()
        self._build_live_cards()
        self._set_status(f"Disconnected {display_name(label)}.", "info")
        if was_running and self.instruments:
            self.start()
        self._refresh_buttons()

    def _measure_loop(self) -> None:
        while not self.stop_event.is_set():
            cycle_started = time.monotonic()
            for label, inst in list(self.instruments.items()):
                if self.stop_event.is_set():
                    break
                disc = self.discovered.get(label)
                if disc is None:
                    continue
                mtype = self.measurements.get(label, DEFAULT_MEASURE.get(disc.label, ""))
                lock = self.instrument_locks.setdefault(label, threading.RLock())
                with lock:
                    reading = do_reading(label, inst, disc.label, mtype)
                if reading:
                    self.events.put(("reading", reading))

            elapsed = time.monotonic() - cycle_started
            delay = max(0.1, self.interval_value - elapsed)
            self.stop_event.wait(delay)

    def export_csv(self) -> None:
        if not self.readings:
            self._set_status("No readings to export.", "warn")
            return
        filename = f"visacom_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        path = filedialog.asksaveasfilename(
            title="Export readings",
            defaultextension=".csv",
            initialfile=filename,
            filetypes=(("CSV files", "*.csv"), ("All files", "*.*")),
        )
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["timestamp", "instrument", "parameter", "value", "unit"])
            for reading in list(self.readings):
                self._write_csv_rows(writer, reading)
        self._set_status(f"Exported {len(self.readings)} reading(s) to {path}.", "info")

    def clear_readings(self) -> None:
        self.readings.clear()
        for item in self.log_tree.get_children():
            self.log_tree.delete(item)
        for fields in self.live_vars.values():
            for var in fields.values():
                var.set("---")
        self.read_count.configure(text="0 readings")
        self._set_status("Reading history cleared.", "info")

    def _process_events(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == "scan_done":
                    self._apply_scan_results(event[1], event[2], event[3])
                elif event[0] == "reading":
                    self._append_reading(event[1])
                elif event[0] == "measure_configured":
                    self._set_status(f"{display_name(event[1])} set to {event[2]}.", "info")
        except queue.Empty:
            pass
        self.after(100, self._process_events)

    def _apply_scan_results(
        self,
        found: Dict[str, DiscoveredInstrument],
        connected: Dict[str, Any],
        measurements: Dict[str, str],
    ) -> None:
        self.discovered = found
        self.instruments = connected
        self.measurements = measurements
        self.instrument_locks = {label: threading.RLock() for label in found}
        self.scanning = False
        self._refresh_instruments()
        self._build_live_cards()
        count = len(self.instruments)
        if found:
            self._set_status(f"Found {len(found)} instrument(s), {count} connected.", "info" if count else "warn")
        else:
            self._set_status("No instruments found. Check connections.", "warn")
        self._refresh_buttons()

    def _append_reading(self, reading: dict) -> None:
        self.readings.append(reading)
        self.read_count.configure(text=f"{len(self.readings)} readings")

        if reading.get("error"):
            self._insert_log_row(
                reading["ts"],
                reading["label"],
                "ERROR",
                reading["error"],
                "",
            )
            self._set_status(f"{display_name(reading['label'])}: {reading['error']}", "error")
            return

        if reading.get("multi"):
            fields = self.live_vars.get(reading["label"], {})
            for param, item in reading["values"].items():
                var = fields.get(param)
                if var is not None:
                    var.set(f"{fmt_num(item['value'])} {item['unit']}".strip())
                self._insert_log_row(reading["ts"], reading["label"], param, item["value"], item["unit"])
            ts_var = fields.get("Timestamp")
            if ts_var is not None:
                ts_var.set(short_time(reading["ts"]))
            return

        fields = self.live_vars.get(reading["label"], {})
        if "Parameter" in fields:
            fields["Parameter"].set(reading.get("param", ""))
        if "Value" in fields:
            fields["Value"].set(fmt_num(reading.get("value")))
        if "Unit" in fields:
            fields["Unit"].set(reading.get("unit", ""))
        if "Timestamp" in fields:
            fields["Timestamp"].set(short_time(reading["ts"]))
        self._insert_log_row(
            reading["ts"],
            reading["label"],
            reading.get("param", ""),
            reading.get("value", ""),
            reading.get("unit", ""),
        )

    def _insert_log_row(self, ts: str, label: str, param: str, value: Any, unit: str) -> None:
        self.log_tree.insert(
            "",
            0,
            values=(short_time(ts), display_name(label), param, fmt_num(value), unit),
        )
        rows = self.log_tree.get_children()
        if len(rows) > 300:
            self.log_tree.delete(rows[-1])

    def _refresh_instruments(self) -> None:
        for item in self.instrument_tree.get_children():
            self.instrument_tree.delete(item)
        for label, disc in self.discovered.items():
            connected = "Connected" if label in self.instruments else "Disconnected"
            measure = self.measurements.get(label, DEFAULT_MEASURE.get(disc.label, ""))
            self.instrument_tree.insert(
                "",
                tk.END,
                iid=label,
                text=display_name(label),
                values=(connected, measure),
                tags=("connected" if label in self.instruments else "failed",),
            )
        self.summary_label.configure(text=f"{len(self.instruments)} connected")

    def _build_live_cards(self) -> None:
        for child in self.live_frame.winfo_children():
            child.destroy()
        self.live_vars.clear()

        if not self.instruments:
            ttk.Label(self.live_frame, text="No instruments connected.").pack(anchor=tk.W)
            return

        for column, (label, _inst) in enumerate(self.instruments.items()):
            disc = self.discovered[label]
            card = ttk.Frame(self.live_frame, style="Panel.TFrame", padding=12)
            card.grid(row=0, column=column, sticky="nsew", padx=(0, 10), pady=(0, 8))
            self.live_frame.columnconfigure(column, weight=1)
            card_head = ttk.Frame(card, style="Panel.TFrame")
            card_head.pack(fill=tk.X, pady=(0, 8))
            ttk.Label(card_head, text=display_name(label), style="Panel.TLabel", font=("Segoe UI", 10, "bold")).pack(side=tk.LEFT)
            ttk.Button(
                card_head,
                text="Disconnect",
                style="Ghost.TButton",
                command=lambda item=label: self.disconnect_instrument(item),
            ).pack(side=tk.RIGHT)
            self.live_vars[label] = {}

            if disc.label == "yokogawa":
                for param in ("Voltage", "Current", "Power", "Apparent", "Reactive", "PF", "Freq"):
                    self._add_card_row(card, label, param)
            else:
                self._add_card_row(card, label, "Parameter", initial=self.measurements.get(label, ""))
                self._add_card_row(card, label, "Value", value_style=True)
                self._add_card_row(card, label, "Unit")
            self._add_card_row(card, label, "Timestamp")

    def _add_card_row(self, parent: ttk.Frame, label: str, name: str, initial: str = "---", value_style: bool = False) -> None:
        row = ttk.Frame(parent, style="Panel.TFrame")
        row.pack(fill=tk.X, pady=2)
        ttk.Label(row, text=f"{name}:", style="Muted.TLabel", width=10).pack(side=tk.LEFT)
        var = tk.StringVar(value=initial)
        style = "Value.TLabel" if value_style else "Panel.TLabel"
        ttk.Label(row, textvariable=var, style=style).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.live_vars[label][name] = var

    def _on_instrument_select(self, _event: object = None) -> None:
        selection = self.instrument_tree.selection()
        if not selection:
            return
        label = selection[0]
        disc = self.discovered.get(label)
        if disc is None:
            return
        options = MEASURE_OPTIONS.get(disc.label, [])
        is_connected = label in self.instruments
        self.measure_combo.configure(values=options, state="readonly" if is_connected and len(options) > 1 else "disabled")
        self.selected_measure.set(self.measurements.get(label, DEFAULT_MEASURE.get(disc.label, "")))
        self._set_detail_text(f"IDN: {disc.idn}\n\nResource: {disc.resource_name}")
        self._refresh_buttons()

    def _on_measure_change(self, _event: object = None) -> None:
        selection = self.instrument_tree.selection()
        if not selection:
            return
        label = selection[0]
        mtype = self.selected_measure.get()
        self.measurements[label] = mtype
        self.instrument_tree.set(label, "measure", mtype)
        fields = self.live_vars.get(label, {})
        if "Parameter" in fields:
            fields["Parameter"].set(mtype)

        inst = self.instruments.get(label)
        disc = self.discovered.get(label)
        if inst is None or disc is None:
            return
        self._set_status(f"Changing {display_name(label)} to {mtype}...", "info")
        threading.Thread(target=self._configure_measurement, args=(label, inst, disc.label, mtype), daemon=True).start()

    def _configure_measurement(self, label: str, inst: Any, base: str, mtype: str) -> None:
        try:
            lock = self.instrument_locks.setdefault(label, threading.RLock())
            with lock:
                configure_for_measurement(inst, base, mtype)
            self.events.put(("measure_configured", label, mtype))
        except Exception as exc:
            self.events.put(("reading", {"ts": datetime.now().isoformat(timespec="milliseconds"), "label": label, "error": str(exc)}))

    def _write_csv_rows(self, writer: csv.writer, reading: dict) -> None:
        if "error" in reading:
            writer.writerow([reading["ts"], reading["label"], "ERROR", reading["error"], ""])
        elif reading.get("multi"):
            for param, value in reading["values"].items():
                writer.writerow([reading["ts"], reading["label"], param, value["value"], value["unit"]])
        else:
            writer.writerow([
                reading["ts"],
                reading["label"],
                reading.get("param", ""),
                reading.get("value", ""),
                reading.get("unit", ""),
            ])

    def _refresh_interval_label(self) -> None:
        self.interval_value = float(self.interval_s.get())
        self.interval_label.configure(text=f"{self.interval_value:.1f} s")

    def _refresh_buttons(self) -> None:
        has_instruments = bool(self.instruments)
        selection = self.instrument_tree.selection()
        can_disconnect = bool(selection and selection[0] in self.instruments and not self.scanning)
        self.scan_btn.configure(state=tk.DISABLED if self.scanning else tk.NORMAL)
        self.start_btn.configure(state=tk.NORMAL if has_instruments and not self.running and not self.scanning else tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL if self.running else tk.DISABLED)
        self.disconnect_btn.configure(state=tk.NORMAL if can_disconnect else tk.DISABLED)
        self.state_label.configure(text="Running" if self.running else "Stopped")

    def _set_detail_text(self, text: str) -> None:
        self.detail_text.configure(state=tk.NORMAL)
        self.detail_text.delete("1.0", tk.END)
        self.detail_text.insert("1.0", text)
        self.detail_text.configure(state=tk.DISABLED)

    def _set_status(self, message: str, level: str = "info") -> None:
        colors = {"info": "#388bfd", "warn": "#d29922", "error": "#f85149"}
        self.status_label.configure(text=message, foreground=colors.get(level, "#8b949e"))

    def _close_instruments(self) -> None:
        for inst in list(self.instruments.values()):
            try:
                inst.close()
            except Exception:
                pass
        self.instruments.clear()

    def _wait_for_measure_thread(self) -> None:
        if self.measure_thread and self.measure_thread.is_alive():
            self.measure_thread.join(timeout=5.0)

    def _on_close(self) -> None:
        self.stop()
        self._wait_for_measure_thread()
        self._close_instruments()
        self.destroy()


def main() -> None:
    try:
        VisacomTkApp().mainloop()
    except tk.TclError as exc:
        messagebox.showerror("Autobench", f"Could not start Tkinter: {exc}")


if __name__ == "__main__":
    main()
