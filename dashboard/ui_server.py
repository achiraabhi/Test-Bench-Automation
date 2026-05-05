"""
ui_server.py - Tkinter desktop dashboard for visacom.

Run:
    python ui_server.py

Performance notes (Raspberry Pi optimised):
- All blocking VISA I/O runs in background threads; the mainloop is never blocked.
- Each connected instrument gets its own reader thread so a slow device cannot
  stall readings from the others (replaces the old single sequential _measure_loop).
- Disconnect / close operations are off-loaded to a worker thread so the UI
  stays live during the VISA teardown.
- The UI event queue is drained in small, time-budgeted batches every 50 ms.
- Log-row trimming uses a companion deque of item IDs, avoiding the expensive
  get_children() traversal on every insert.
- The interval slider label is debounced: only updated 150 ms after dragging stops.
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
from typing import Any, Dict, List, Optional, Set, Tuple

# Allow importing the visacom package from the sibling instruments/ directory.
sys.path.insert(0, str(Path(__file__).parent.parent / "instruments"))

from visacom import Fluke8845A, HiokiRM3545, KeysightDMM, YokogawaWT310
from visacom.discover import DiscoveredInstrument, discover

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

MEASURE_OPTIONS: Dict[str, List[str]] = {
    "keysight": ["AC Voltage", "DC Voltage", "Resistance"],
    "fluke":    ["AC Voltage", "DC Voltage", "Resistance"],
    "yokogawa": ["All Power Quantities"],
    "hioki":    ["Resistance"],
}

DEFAULT_MEASURE: Dict[str, str] = {
    "keysight": "AC Voltage",
    "fluke":    "AC Voltage",
    "yokogawa": "All Power Quantities",
    "hioki":    "Resistance",
}

DISPLAY_NAMES = {
    "keysight": "Keysight DMM",
    "fluke":    "Fluke 8845A",
    "yokogawa": "Yokogawa WT310",
    "hioki":    "Hioki RM3545",
}

APP_DIR   = Path(__file__).parent
LOGO_PATH = APP_DIR / "static" / "noratel-logo.png"

COLORS = {
    "bg":     "#0f131a",
    "panel":  "#171d26",
    "card":   "#1d2633",
    "card2":  "#131922",
    "border": "#2d3645",
    "text":   "#eef3f8",
    "muted":  "#9ba7b6",
    "accent": "#2f9df4",
    "green":  "#34c759",
    "yellow": "#f6c343",
    "red":    "#ff5c5c",
}

# ── Timing constants (tuned for Raspberry Pi) ───────────────────────────────
UI_EVENT_POLL_MS    = 50   # how often _process_events is rescheduled (ms)
UI_EVENT_BUDGET_MS  = 15   # max wall-clock ms allowed per _process_events call
MAX_EVENTS_PER_TICK = 20   # hard cap so queue backlog never monopolises the frame
LOG_FLUSH_LIMIT     = 8    # Treeview inserts per tick (each is a Tcl/Tk round-trip)
LOG_ROW_LIMIT       = 300  # max rows kept in the visible log tree
INTERVAL_DEBOUNCE_MS = 150 # slider label is redrawn only after dragging pauses


# ── Pure helper functions (no Tk, safe to call from any thread) ─────────────

def display_name(label: str) -> str:
    base   = label.rsplit("_", 1)[0] if label.rsplit("_", 1)[-1].isdigit() else label
    suffix = f" #{label.rsplit('_', 1)[1]}" if "_" in label and label.rsplit("_", 1)[-1].isdigit() else ""
    return f"{DISPLAY_NAMES.get(base, label)}{suffix}"


def fmt_num(value: Any) -> str:
    if value is None:
        return "---"
    if not isinstance(value, (int, float)):
        return str(value)
    value = float(value)
    mag   = abs(value)
    if mag == 0:        return "0.000"
    if mag >= 10_000:   return f"{value:.1f}"
    if mag >= 100:      return f"{value:.2f}"
    if mag >= 1:        return f"{value:.4f}"
    if mag >= 0.001:    return f"{value:.6f}"
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


def connect_instrument(disc: DiscoveredInstrument, measurement: str = "") -> Any:
    if disc.label == "keysight":
        inst = KeysightDMM(disc.resource_name, timeout_ms=5_000)
        configure_for_measurement(inst, disc.label, measurement or DEFAULT_MEASURE[disc.label])
    elif disc.label == "fluke":
        inst = Fluke8845A(disc.resource_name, timeout_ms=10_000)
        configure_for_measurement(inst, disc.label, measurement or DEFAULT_MEASURE[disc.label])
    elif disc.label == "yokogawa":
        inst = YokogawaWT310(disc.resource_name, timeout_ms=10_000)
        inst.configure_auto_range()
    elif disc.label == "hioki":
        inst = HiokiRM3545(disc.resource_name, timeout_ms=15_000)
        inst.initialize(line_freq=50, speed="MED", auto_range=True)
        inst.set_continuous(False)
    else:
        raise ValueError(f"Unsupported instrument type: {disc.label}")
    return inst


def do_reading(label: str, inst: Any, base: str, mtype: str,
               configure_dmm: bool = True) -> Optional[dict]:
    """Blocking VISA read. Always called from a background thread."""
    ts = datetime.now().isoformat(timespec="milliseconds")
    try:
        if base in ("keysight", "fluke"):
            if configure_dmm:
                configure_for_measurement(inst, base, mtype)
            if mtype == "DC Voltage":
                return {"ts": ts, "label": label, "param": "DC Voltage",
                        "value": inst.read_dc_voltage(), "unit": "V DC"}
            if mtype == "Resistance":
                return {"ts": ts, "label": label, "param": "Resistance",
                        "value": inst.read_resistance(), "unit": "Ohm"}
            return {"ts": ts, "label": label, "param": "AC Voltage",
                    "value": inst.read_ac_voltage(), "unit": "V AC"}

        if base == "yokogawa":
            r = inst.read_power()
            return {
                "ts": ts, "label": label, "param": "Power", "multi": True,
                "values": {
                    "Voltage":  {"value": r.voltage_V,    "unit": "V"},
                    "Current":  {"value": r.current_A,    "unit": "A"},
                    "Power":    {"value": r.power_W,      "unit": "W"},
                    "Apparent": {"value": r.apparent_VA,  "unit": "VA"},
                    "Reactive": {"value": r.reactive_var, "unit": "var"},
                    "PF":       {"value": r.power_factor, "unit": ""},
                    "Freq":     {"value": r.frequency_Hz, "unit": "Hz"},
                },
            }

        if base == "hioki":
            return {"ts": ts, "label": label, "param": "Resistance",
                    "value": inst.read(), "unit": "Ohm"}

    except Exception as exc:
        return {"ts": ts, "label": label, "error": str(exc)}

    return None


# ── Main application class ───────────────────────────────────────────────────

class VisacomTkApp(tk.Tk):

    def __init__(self) -> None:
        super().__init__()
        self.title("Autobench")
        self.geometry("1180x740")
        self.minsize(960, 600)

        # ── Application state ────────────────────────────────────────────────
        self.discovered:  Dict[str, DiscoveredInstrument] = {}
        self.instruments: Dict[str, Any] = {}          # label → instrument obj
        self.measurements: Dict[str, str] = {}         # label → measurement type
        self.readings: deque = deque(maxlen=500)
        self.running  = False
        self.scanning = False
        self.interval_s     = tk.DoubleVar(value=2.0)
        self.interval_value = 2.0                      # float shadow used by threads
        self.selected_measure = tk.StringVar(value="")

        # ── Thread / concurrency primitives ─────────────────────────────────
        self.events: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()

        # Per-instrument reader threads replace the old single _measure_loop.
        # Protected by _threads_lock when adding/removing entries.
        self._reader_threads: Dict[str, threading.Thread] = {}
        self._threads_lock   = threading.Lock()

        # Per-instrument RLocks guard each instrument object during VISA calls.
        self.instrument_locks: Dict[str, threading.RLock] = {}

        # Lock that protects self.instruments dict itself (shared across threads).
        self._instruments_lock = threading.Lock()

        self.configured_measurements: Dict[str, str] = {}

        # ── UI state ─────────────────────────────────────────────────────────
        self.live_vars: Dict[str, Dict[str, tk.StringVar]] = {}
        self.pending_log_rows: List[Tuple[str, str, str, Any, str]] = []
        self.log_row_count = 0

        # Companion deque of Treeview item IDs — lets us trim the oldest row
        # in O(1) without calling get_children() (which traverses the whole tree).
        self._log_iids: deque = deque()

        self.reconnecting: Set[str] = set()
        self.disconnecting: Set[str] = set()
        self.logo_image: Optional[tk.PhotoImage] = self._load_logo()

        # after() handle used to debounce the slider label redraw.
        self._interval_after_id: Optional[str] = None

        self._configure_style()
        self._build_ui()
        self._set_status("Ready. Click Scan to discover instruments.", "info")

        # Start the recurring UI event pump (never blocks — budget-limited).
        self.after(UI_EVENT_POLL_MS, self._process_events)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── Style / UI construction ──────────────────────────────────────────────

    def _configure_style(self) -> None:
        self.configure(bg=COLORS["bg"])
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".", font=("Segoe UI", 10))
        s.configure("TFrame",        background=COLORS["bg"])
        s.configure("Header.TFrame", background=COLORS["panel"])
        s.configure("Panel.TFrame",  background=COLORS["panel"])
        s.configure("Card.TFrame",   background=COLORS["card"])
        s.configure("TLabel",        background=COLORS["bg"],    foreground=COLORS["text"])
        s.configure("Header.TLabel", background=COLORS["panel"], foreground=COLORS["text"])
        s.configure("Panel.TLabel",  background=COLORS["panel"], foreground=COLORS["text"])
        s.configure("Card.TLabel",   background=COLORS["card"],  foreground=COLORS["text"])
        s.configure("Muted.TLabel",      background=COLORS["panel"], foreground=COLORS["muted"])
        s.configure("CardMuted.TLabel",  background=COLORS["card"],  foreground=COLORS["muted"])
        s.configure("Value.TLabel",      background=COLORS["card"],  foreground=COLORS["text"],
                    font=("Consolas", 22, "bold"))
        s.configure("TButton", background=COLORS["card2"], foreground=COLORS["text"],
                    bordercolor=COLORS["border"], padding=(11, 6))
        s.map("TButton", background=[("active", COLORS["card"]), ("disabled", COLORS["panel"])])
        s.configure("Accent.TButton", background=COLORS["accent"],  foreground="#ffffff",
                    bordercolor=COLORS["accent"])
        s.configure("Green.TButton",  background="#173c25",          foreground=COLORS["green"],
                    bordercolor="#245f38")
        s.configure("Red.TButton",    background="#3b1b1f",          foreground=COLORS["red"],
                    bordercolor="#693037")
        s.configure("Ghost.TButton",  background=COLORS["panel"],    foreground=COLORS["muted"],
                    bordercolor=COLORS["border"])
        s.configure("Treeview",
                    background=COLORS["card2"], fieldbackground=COLORS["card2"],
                    foreground=COLORS["text"],  bordercolor=COLORS["border"], rowheight=30)
        s.configure("Treeview.Heading",
                    background=COLORS["panel"], foreground=COLORS["muted"], relief=tk.FLAT)
        s.map("Treeview",
              background=[("selected", "#1d5f91")],
              foreground=[("selected", "#ffffff")])
        s.configure("TCombobox",
                    fieldbackground=COLORS["card2"], background=COLORS["card2"],
                    foreground=COLORS["text"])

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
        # ── Toolbar ──────────────────────────────────────────────────────────
        toolbar = ttk.Frame(self, style="Header.TFrame", padding=(16, 10))
        toolbar.pack(fill=tk.X)

        brand = ttk.Frame(toolbar, style="Header.TFrame")
        brand.pack(side=tk.LEFT, padx=(0, 18))
        if self.logo_image is not None:
            ttk.Label(brand, image=self.logo_image, style="Header.TLabel").pack(
                side=tk.LEFT, padx=(0, 12))
        wordmark = ttk.Frame(brand, style="Header.TFrame")
        wordmark.pack(side=tk.LEFT)
        ttk.Label(wordmark, text="AUTOBENCH", style="Header.TLabel",
                  font=("Segoe UI", 15, "bold")).pack(anchor=tk.W)
        ttk.Label(wordmark, text="Noratel instrument dashboard", style="Header.TLabel",
                  foreground=COLORS["muted"], font=("Segoe UI", 9)).pack(anchor=tk.W)

        self.scan_btn  = ttk.Button(toolbar, text="Scan",  style="Accent.TButton", command=self.scan)
        self.start_btn = ttk.Button(toolbar, text="Start", style="Green.TButton",  command=self.start)
        self.stop_btn  = ttk.Button(toolbar, text="Stop",  style="Red.TButton",    command=self.stop)
        self.scan_btn.pack(side=tk.LEFT, padx=(0, 6))
        self.start_btn.pack(side=tk.LEFT, padx=6)
        self.stop_btn.pack(side=tk.LEFT, padx=6)

        ttk.Label(toolbar, text="Interval", style="Header.TLabel").pack(
            side=tk.LEFT, padx=(18, 6))
        # command fires on every pixel of drag — we debounce the label redraw
        self.interval_scale = ttk.Scale(
            toolbar, from_=0.5, to=30.0, variable=self.interval_s,
            orient=tk.HORIZONTAL, length=130,
            command=self._debounce_interval_label,
        )
        self.interval_scale.pack(side=tk.LEFT)
        self.interval_label = ttk.Label(toolbar, text="2.0 s", style="Header.TLabel", width=7)
        self.interval_label.pack(side=tk.LEFT, padx=(5, 14))

        ttk.Button(toolbar, text="CSV",   command=self.export_csv).pack(side=tk.RIGHT, padx=(6, 0))
        ttk.Button(toolbar, text="Clear", command=self.clear_readings).pack(side=tk.RIGHT)

        # ── Body ─────────────────────────────────────────────────────────────
        body = ttk.Frame(self, padding=(14, 14, 14, 12))
        body.pack(fill=tk.BOTH, expand=True)

        # Sidebar
        sidebar = ttk.Frame(body, style="Panel.TFrame", padding=14, width=350)
        sidebar.pack(side=tk.LEFT, fill=tk.Y)
        sidebar.pack_propagate(False)

        ttk.Label(sidebar, text="Instruments", style="Panel.TLabel",
                  font=("Segoe UI", 12, "bold")).pack(anchor=tk.W)

        self.instrument_tree = ttk.Treeview(
            sidebar, columns=("status", "measure"),
            show="tree headings", height=13, selectmode="browse",
        )
        self.instrument_tree.heading("#0",      text="Device")
        self.instrument_tree.heading("status",  text="Status")
        self.instrument_tree.heading("measure", text="Measure")
        self.instrument_tree.column("#0",      width=145)
        self.instrument_tree.column("status",  width=80, anchor=tk.CENTER)
        self.instrument_tree.column("measure", width=90)
        self.instrument_tree.tag_configure("connected", foreground=COLORS["green"])
        self.instrument_tree.tag_configure("failed",    foreground=COLORS["red"])
        self.instrument_tree.pack(fill=tk.BOTH, expand=True, pady=(10, 12))
        self.instrument_tree.bind("<<TreeviewSelect>>", self._on_instrument_select)

        ttk.Label(sidebar, text="Measurement", style="Muted.TLabel").pack(anchor=tk.W)
        self.measure_combo = ttk.Combobox(sidebar, textvariable=self.selected_measure,
                                          state="readonly")
        self.measure_combo.pack(fill=tk.X, pady=(4, 8))
        self.measure_combo.bind("<<ComboboxSelected>>", self._on_measure_change)

        self.disconnect_btn = ttk.Button(sidebar, text="Disconnect Selected",
                                         style="Red.TButton",   command=self.disconnect_selected)
        self.reconnect_btn  = ttk.Button(sidebar, text="Reconnect Selected",
                                         style="Green.TButton", command=self.reconnect_selected)
        self.disconnect_btn.pack(fill=tk.X, pady=(0, 8))
        self.reconnect_btn.pack(fill=tk.X,  pady=(0, 12))

        self.detail_text = tk.Text(
            sidebar, height=7, wrap=tk.WORD, bg=COLORS["card2"], fg=COLORS["muted"],
            insertbackground=COLORS["text"], relief=tk.FLAT, padx=8, pady=8,
        )
        self.detail_text.pack(fill=tk.X)
        self.detail_text.insert("1.0", "Select an instrument to see IDN and resource details.")
        self.detail_text.configure(state=tk.DISABLED)

        # Main content area
        content = ttk.Frame(body, padding=(16, 0, 0, 0))
        content.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        summary = ttk.Frame(content, style="Card.TFrame", padding=14)
        summary.pack(fill=tk.X, pady=(0, 14))
        ttk.Label(summary, text="Live Bench", style="Card.TLabel",
                  font=("Segoe UI", 15, "bold")).pack(side=tk.LEFT)
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
            ("time",       "Time",       120),
            ("instrument", "Instrument", 180),
            ("parameter",  "Parameter",  120),
            ("value",      "Value",      140),
            ("unit",       "Unit",        70),
        ):
            self.log_tree.heading(col, text=title)
            self.log_tree.column(col, width=width, anchor=tk.W)
        self.log_tree.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        # Status bar
        statusbar = ttk.Frame(self, style="Panel.TFrame", padding=(14, 7))
        statusbar.pack(fill=tk.X)
        self.status_label = ttk.Label(statusbar, text="", style="Muted.TLabel")
        self.status_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.state_label = ttk.Label(statusbar, text="Stopped", style="Muted.TLabel")
        self.state_label.pack(side=tk.RIGHT)
        self._refresh_buttons()

    # ── Interval slider (debounced) ──────────────────────────────────────────

    def _debounce_interval_label(self, _: str = "") -> None:
        # Update the backing float immediately (reader threads read this value).
        self.interval_value = float(self.interval_s.get())
        # Cancel any scheduled label redraw and reschedule INTERVAL_DEBOUNCE_MS later.
        # This means the label widget is only reconfigured once per drag gesture.
        if self._interval_after_id is not None:
            self.after_cancel(self._interval_after_id)
        self._interval_after_id = self.after(INTERVAL_DEBOUNCE_MS, self._apply_interval_label)

    def _apply_interval_label(self) -> None:
        self._interval_after_id = None
        self.interval_label.configure(text=f"{self.interval_value:.1f} s")

    # ── Scan ─────────────────────────────────────────────────────────────────

    def scan(self) -> None:
        if self.scanning:
            return
        self.stop()
        self.scanning = True
        self._refresh_buttons()
        self._set_status("Scanning for instruments...", "info")
        threading.Thread(target=self._scan_worker, daemon=True).start()

    def _scan_worker(self) -> None:
        # Join all reader threads before closing instruments (runs in background).
        self._join_all_readers(timeout=5.0)
        self._close_instruments()

        found = discover()
        connected: Dict[str, Any] = {}
        measurements = dict(self.measurements)

        for label, disc in found.items():
            try:
                measurement = measurements.setdefault(label, DEFAULT_MEASURE.get(disc.label, ""))
                connected[label] = connect_instrument(disc, measurement)
            except Exception as exc:
                logger.warning("Connect failed [%s]: %s", label, exc)

        self.events.put(("scan_done", found, connected, measurements))

    # ── Start / Stop ─────────────────────────────────────────────────────────

    def start(self) -> None:
        if self.running:
            return
        with self._instruments_lock:
            has = bool(self.instruments)
        if not has:
            self._set_status("No instruments connected. Run Scan first.", "warn")
            return
        self.running = True
        self.stop_event.clear()
        # Spawn one reader thread per instrument so they run concurrently.
        with self._instruments_lock:
            labels = list(self.instruments.keys())
        for label in labels:
            self._start_reader(label)
        self._set_status("Measurement started.", "info")
        self._refresh_buttons()

    def stop(self) -> None:
        if not self.running:
            self._refresh_buttons()
            return
        self.running = False
        self.stop_event.set()
        # Reader threads notice stop_event on their next sleep and exit cleanly.
        # We do NOT join them here — that would block the main thread.
        self._set_status("Measurement stopped.", "info")
        self._refresh_buttons()

    # ── Per-instrument reader threads ────────────────────────────────────────

    def _start_reader(self, label: str) -> None:
        """Spawn a background reader thread for one instrument."""
        t = threading.Thread(
            target=self._instrument_reader,
            args=(label,),
            name=f"reader-{label}",
            daemon=True,
        )
        with self._threads_lock:
            self._reader_threads[label] = t
        t.start()

    def _instrument_reader(self, label: str) -> None:
        """
        Background loop: reads one instrument at the configured interval until
        stop_event fires or the instrument is removed from self.instruments.
        Runs entirely off the main thread — safe to block on slow VISA calls.
        """
        while not self.stop_event.is_set():
            cycle_start = time.monotonic()

            # Check instrument still connected (atomic under GIL for dict lookup).
            with self._instruments_lock:
                inst = self.instruments.get(label)
                disc = self.discovered.get(label)

            if inst is None or disc is None:
                break  # Disconnected while we were sleeping — exit gracefully.

            mtype = self.measurements.get(label, DEFAULT_MEASURE.get(disc.label, ""))
            lock  = self.instrument_locks.get(label)
            if lock is None:
                break

            with lock:
                # Reconfigure if the user changed the measurement type.
                if (disc.label in ("keysight", "fluke")
                        and self.configured_measurements.get(label) != mtype):
                    configure_for_measurement(inst, disc.label, mtype)
                    self.configured_measurements[label] = mtype
                reading = do_reading(label, inst, disc.label, mtype, configure_dmm=False)

            if reading:
                self.events.put(("reading", reading))

            # Sleep for the remainder of the interval; stop_event wakes us early.
            elapsed = time.monotonic() - cycle_start
            self.stop_event.wait(max(0.05, self.interval_value - elapsed))

    def _join_all_readers(self, timeout: float = 3.0) -> None:
        """Wait for every reader thread to finish. Safe to call from any thread."""
        with self._threads_lock:
            threads = list(self._reader_threads.values())
            self._reader_threads.clear()
        for t in threads:
            if t.is_alive():
                t.join(timeout=timeout)

    # ── Disconnect / Reconnect ───────────────────────────────────────────────

    def disconnect_selected(self) -> None:
        sel = self.instrument_tree.selection()
        if not sel:
            self._set_status("Select an instrument to disconnect.", "warn")
            return
        self.disconnect_instrument(sel[0])

    def reconnect_selected(self) -> None:
        sel = self.instrument_tree.selection()
        if not sel:
            self._set_status("Select a disconnected instrument to reconnect.", "warn")
            return
        self.reconnect_instrument(sel[0])

    def disconnect_instrument(self, label: str) -> None:
        with self._instruments_lock:
            if label not in self.instruments:
                self._set_status(f"{display_name(label)} is already disconnected.", "warn")
                return
            if label in self.disconnecting:
                return
            self.disconnecting.add(label)
            # Pop immediately so the reader thread exits on its next iteration.
            inst = self.instruments.pop(label)

        self._set_status(f"Disconnecting {display_name(label)}...", "info")
        self._refresh_buttons()
        # All blocking work (thread join + VISA close) runs in the background.
        threading.Thread(
            target=self._disconnect_worker,
            args=(label, inst),
            daemon=True,
        ).start()

    def _disconnect_worker(self, label: str, inst: Any) -> None:
        """Background: waits for the reader to finish then closes the instrument."""
        with self._threads_lock:
            t = self._reader_threads.pop(label, None)
        if t and t.is_alive():
            t.join(timeout=5.0)

        try:
            inst.close()
        except Exception as exc:
            logger.warning("Disconnect failed [%s]: %s", label, exc)

        self.disconnecting.discard(label)
        self.events.put(("disconnect_done", label))

    def _apply_disconnect_result(self, label: str) -> None:
        """Called on the main thread after the disconnect worker finishes."""
        self._refresh_instruments()
        self._build_live_cards()
        self._set_status(f"Disconnected {display_name(label)}.", "info")
        self._refresh_buttons()

    def reconnect_instrument(self, label: str) -> None:
        with self._instruments_lock:
            already = label in self.instruments
        if already:
            self._set_status(f"{display_name(label)} is already connected.", "warn")
            return
        if label not in self.discovered:
            self._set_status("Run Scan before reconnecting this instrument.", "warn")
            return
        if label in self.reconnecting:
            self._set_status(f"{display_name(label)} is already reconnecting.", "warn")
            return
        self.reconnecting.add(label)
        self._set_status(f"Reconnecting {display_name(label)}...", "info")
        self._refresh_buttons()
        threading.Thread(target=self._reconnect_worker, args=(label,), daemon=True).start()

    def _reconnect_worker(self, label: str) -> None:
        disc = self.discovered[label]
        measurement = self.measurements.setdefault(label, DEFAULT_MEASURE.get(disc.label, ""))
        try:
            inst = connect_instrument(disc, measurement)
            self.events.put(("reconnect_done", label, inst, None))
        except Exception as exc:
            self.events.put(("reconnect_done", label, None, str(exc)))

    # ── UI event pump ────────────────────────────────────────────────────────

    def _process_events(self) -> None:
        """
        Drains the inter-thread event queue in a time-budgeted loop.
        Runs every UI_EVENT_POLL_MS ms on the main thread via after().
        Never blocks — stays within UI_EVENT_BUDGET_MS to keep frames smooth.
        """
        started   = time.monotonic()
        processed = 0
        try:
            while (processed < MAX_EVENTS_PER_TICK
                   and (time.monotonic() - started) * 1000 < UI_EVENT_BUDGET_MS):
                event = self.events.get_nowait()
                kind  = event[0]
                if kind == "scan_done":
                    self._apply_scan_results(event[1], event[2], event[3])
                elif kind == "reading":
                    self._append_reading(event[1])
                elif kind == "measure_configured":
                    self._set_status(f"{display_name(event[1])} set to {event[2]}.", "info")
                elif kind == "reconnect_done":
                    self._apply_reconnect_result(event[1], event[2], event[3])
                elif kind == "disconnect_done":
                    self._apply_disconnect_result(event[1])
                processed += 1
        except queue.Empty:
            pass

        # Flush pending log rows only if budget time remains (avoids frame drops).
        remaining_ms = UI_EVENT_BUDGET_MS - (time.monotonic() - started) * 1000
        if remaining_ms > 2:
            self._flush_log_rows()

        self.after(UI_EVENT_POLL_MS, self._process_events)

    # ── Scan / reconnect result handlers ────────────────────────────────────

    def _apply_scan_results(
        self,
        found:        Dict[str, DiscoveredInstrument],
        connected:    Dict[str, Any],
        measurements: Dict[str, str],
    ) -> None:
        with self._instruments_lock:
            self.discovered  = found
            self.instruments = connected
        self.measurements = measurements
        self.instrument_locks = {label: threading.RLock() for label in found}
        self.configured_measurements = {
            label: measurements.get(label, DEFAULT_MEASURE.get(disc.label, ""))
            for label, disc in found.items()
            if label in connected
        }
        self.scanning = False
        self._refresh_instruments()
        self._build_live_cards()
        count = len(connected)
        if found:
            self._set_status(
                f"Found {len(found)} instrument(s), {count} connected.",
                "info" if count else "warn",
            )
        else:
            self._set_status("No instruments found. Check connections.", "warn")
        self._refresh_buttons()

    def _apply_reconnect_result(self, label: str, inst: Any, error: Optional[str]) -> None:
        self.reconnecting.discard(label)
        if error:
            self._set_status(f"Reconnect failed for {display_name(label)}: {error}", "error")
            self._refresh_buttons()
            return
        with self._instruments_lock:
            self.instruments[label] = inst
        disc = self.discovered.get(label)
        if disc is not None:
            self.configured_measurements[label] = self.measurements.get(
                label, DEFAULT_MEASURE.get(disc.label, ""))
        self.instrument_locks.setdefault(label, threading.RLock())
        self._refresh_instruments()
        self._build_live_cards()
        # If measurement is already running, start a reader for the new instrument.
        if self.running:
            self._start_reader(label)
        self._set_status(f"Reconnected {display_name(label)}.", "info")
        self._refresh_buttons()

    # ── Reading / log handling ───────────────────────────────────────────────

    def _append_reading(self, reading: dict) -> None:
        self.readings.append(reading)
        self.read_count.configure(text=f"{len(self.readings)} readings")

        if reading.get("error"):
            self._insert_log_row(reading["ts"], reading["label"], "ERROR",
                                 reading["error"], "")
            self._set_status(f"{display_name(reading['label'])}: {reading['error']}", "error")
            return

        if reading.get("multi"):
            fields = self.live_vars.get(reading["label"], {})
            for param, item in reading["values"].items():
                var = fields.get(param)
                if var is not None:
                    var.set(f"{fmt_num(item['value'])} {item['unit']}".strip())
                self._queue_log_row(reading["ts"], reading["label"],
                                    param, item["value"], item["unit"])
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

        self._queue_log_row(
            reading["ts"], reading["label"],
            reading.get("param", ""), reading.get("value", ""), reading.get("unit", ""),
        )

    def _queue_log_row(self, ts: str, label: str, param: str,
                       value: Any, unit: str) -> None:
        self.pending_log_rows.append((ts, label, param, value, unit))

    def _flush_log_rows(self) -> None:
        """Insert up to LOG_FLUSH_LIMIT pending rows — called inside the budget window."""
        if not self.pending_log_rows:
            return
        rows = self.pending_log_rows[:LOG_FLUSH_LIMIT]
        del self.pending_log_rows[:LOG_FLUSH_LIMIT]
        for ts, label, param, value, unit in rows:
            self._insert_log_row(ts, label, param, value, unit)

    def _insert_log_row(self, ts: str, label: str, param: str,
                        value: Any, unit: str) -> None:
        iid = self.log_tree.insert(
            "", 0,
            values=(short_time(ts), display_name(label), param, fmt_num(value), unit),
        )
        # Track IDs so we can trim in O(1) without get_children().
        self._log_iids.appendleft(iid)
        self.log_row_count += 1

        # Remove oldest rows one-by-one until we're back within the limit.
        while self.log_row_count > LOG_ROW_LIMIT:
            oldest = self._log_iids.pop()
            try:
                self.log_tree.delete(oldest)
            except tk.TclError:
                pass  # Already removed by clear_readings
            self.log_row_count -= 1

    # ── Export / Clear ───────────────────────────────────────────────────────

    def export_csv(self) -> None:
        if not self.readings:
            self._set_status("No readings to export.", "warn")
            return
        filename = f"visacom_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        path = filedialog.asksaveasfilename(
            title="Export readings", defaultextension=".csv",
            initialfile=filename,
            filetypes=(("CSV files", "*.csv"), ("All files", "*.*")),
        )
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["timestamp", "instrument", "parameter", "value", "unit"])
            for reading in list(self.readings):
                self._write_csv_rows(writer, reading)
        self._set_status(f"Exported {len(self.readings)} reading(s) to {path}.", "info")

    def clear_readings(self) -> None:
        self.readings.clear()
        for item in self.log_tree.get_children():
            self.log_tree.delete(item)
        self._log_iids.clear()
        self.pending_log_rows.clear()
        self.log_row_count = 0
        for fields in self.live_vars.values():
            for var in fields.values():
                var.set("---")
        self.read_count.configure(text="0 readings")
        self._set_status("Reading history cleared.", "info")

    # ── Instrument tree / live cards ─────────────────────────────────────────

    def _refresh_instruments(self) -> None:
        for item in self.instrument_tree.get_children():
            self.instrument_tree.delete(item)
        with self._instruments_lock:
            snap = dict(self.instruments)
        for label, disc in self.discovered.items():
            connected = label in snap
            measure   = self.measurements.get(label, DEFAULT_MEASURE.get(disc.label, ""))
            self.instrument_tree.insert(
                "", tk.END, iid=label, text=display_name(label),
                values=("Connected" if connected else "Disconnected", measure),
                tags=("connected" if connected else "failed",),
            )
        self.summary_label.configure(text=f"{len(snap)} connected")

    def _build_live_cards(self) -> None:
        for child in self.live_frame.winfo_children():
            child.destroy()
        self.live_vars.clear()

        with self._instruments_lock:
            snap = dict(self.instruments)

        if not snap:
            ttk.Label(self.live_frame, text="No instruments connected.").pack(anchor=tk.W)
            return

        for column, (label, _inst) in enumerate(snap.items()):
            disc = self.discovered[label]
            card = ttk.Frame(self.live_frame, style="Panel.TFrame", padding=12)
            card.grid(row=0, column=column, sticky="nsew", padx=(0, 10), pady=(0, 8))
            self.live_frame.columnconfigure(column, weight=1)

            card_head = ttk.Frame(card, style="Panel.TFrame")
            card_head.pack(fill=tk.X, pady=(0, 8))
            ttk.Label(card_head, text=display_name(label), style="Panel.TLabel",
                      font=("Segoe UI", 10, "bold")).pack(side=tk.LEFT)
            ttk.Button(
                card_head, text="Disconnect", style="Ghost.TButton",
                command=lambda lbl=label: self.disconnect_instrument(lbl),
            ).pack(side=tk.RIGHT)

            self.live_vars[label] = {}
            if disc.label == "yokogawa":
                for param in ("Voltage", "Current", "Power", "Apparent",
                              "Reactive", "PF", "Freq"):
                    self._add_card_row(card, label, param)
            else:
                self._add_card_row(card, label, "Parameter",
                                   initial=self.measurements.get(label, ""))
                self._add_card_row(card, label, "Value", value_style=True)
                self._add_card_row(card, label, "Unit")
            self._add_card_row(card, label, "Timestamp")

    def _add_card_row(self, parent: ttk.Frame, label: str, name: str,
                      initial: str = "---", value_style: bool = False) -> None:
        row = ttk.Frame(parent, style="Panel.TFrame")
        row.pack(fill=tk.X, pady=2)
        ttk.Label(row, text=f"{name}:", style="Muted.TLabel", width=10).pack(side=tk.LEFT)
        var   = tk.StringVar(value=initial)
        style = "Value.TLabel" if value_style else "Panel.TLabel"
        ttk.Label(row, textvariable=var, style=style).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.live_vars[label][name] = var

    # ── Sidebar interactions ─────────────────────────────────────────────────

    def _on_instrument_select(self, _event: object = None) -> None:
        sel = self.instrument_tree.selection()
        if not sel:
            return
        label = sel[0]
        disc  = self.discovered.get(label)
        if disc is None:
            return
        options      = MEASURE_OPTIONS.get(disc.label, [])
        is_connected = label in self.instruments
        self.measure_combo.configure(
            values=options,
            state="readonly" if is_connected and len(options) > 1 else "disabled",
        )
        self.selected_measure.set(
            self.measurements.get(label, DEFAULT_MEASURE.get(disc.label, "")))
        self._set_detail_text(f"IDN: {disc.idn}\n\nResource: {disc.resource_name}")
        self._refresh_buttons()

    def _on_measure_change(self, _event: object = None) -> None:
        sel = self.instrument_tree.selection()
        if not sel:
            return
        label = sel[0]
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
        threading.Thread(
            target=self._configure_measurement,
            args=(label, inst, disc.label, mtype),
            daemon=True,
        ).start()

    def _configure_measurement(self, label: str, inst: Any,
                                base: str, mtype: str) -> None:
        try:
            lock = self.instrument_locks.setdefault(label, threading.RLock())
            with lock:
                configure_for_measurement(inst, base, mtype)
                self.configured_measurements[label] = mtype
            self.events.put(("measure_configured", label, mtype))
        except Exception as exc:
            self.events.put(("reading", {
                "ts":    datetime.now().isoformat(timespec="milliseconds"),
                "label": label,
                "error": str(exc),
            }))

    # ── CSV helper ───────────────────────────────────────────────────────────

    def _write_csv_rows(self, writer: "csv.writer", reading: dict) -> None:
        if "error" in reading:
            writer.writerow([reading["ts"], reading["label"], "ERROR",
                             reading["error"], ""])
        elif reading.get("multi"):
            for param, item in reading["values"].items():
                writer.writerow([reading["ts"], reading["label"],
                                 param, item["value"], item["unit"]])
        else:
            writer.writerow([
                reading["ts"], reading["label"],
                reading.get("param", ""), reading.get("value", ""),
                reading.get("unit", ""),
            ])

    # ── Misc helpers ─────────────────────────────────────────────────────────

    def _refresh_buttons(self) -> None:
        with self._instruments_lock:
            has_instruments = bool(self.instruments)
        sel            = self.instrument_tree.selection()
        can_disconnect = bool(
            sel
            and sel[0] in self.instruments
            and not self.scanning
            and sel[0] not in self.disconnecting
        )
        self.scan_btn.configure(
            state=tk.DISABLED if self.scanning else tk.NORMAL)
        self.start_btn.configure(
            state=tk.NORMAL if has_instruments and not self.running and not self.scanning
            else tk.DISABLED)
        self.stop_btn.configure(
            state=tk.NORMAL if self.running else tk.DISABLED)
        self.disconnect_btn.configure(
            state=tk.NORMAL if can_disconnect else tk.DISABLED)
        self.state_label.configure(text="Running" if self.running else "Stopped")

    def _set_detail_text(self, text: str) -> None:
        self.detail_text.configure(state=tk.NORMAL)
        self.detail_text.delete("1.0", tk.END)
        self.detail_text.insert("1.0", text)
        self.detail_text.configure(state=tk.DISABLED)

    def _set_status(self, message: str, level: str = "info") -> None:
        colors = {"info": "#388bfd", "warn": "#d29922", "error": "#f85149"}
        self.status_label.configure(
            text=message, foreground=colors.get(level, "#8b949e"))

    def _close_instruments(self) -> None:
        with self._instruments_lock:
            snap = dict(self.instruments)
            self.instruments.clear()
        self.configured_measurements.clear()
        for inst in snap.values():
            try:
                inst.close()
            except Exception:
                pass

    # ── Window close ─────────────────────────────────────────────────────────

    def _on_close(self) -> None:
        """Hide the window immediately, then clean up VISA connections in background."""
        self.running = False
        self.stop_event.set()      # tells all reader threads to exit
        self.withdraw()            # hide window instantly — feels responsive
        threading.Thread(target=self._shutdown_worker, daemon=True).start()

    def _shutdown_worker(self) -> None:
        self._join_all_readers(timeout=3.0)
        self._close_instruments()
        # Schedule destroy() back on the main thread so mainloop() can exit cleanly.
        self.after(0, self.destroy)


# ── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    try:
        VisacomTkApp().mainloop()
    except tk.TclError as exc:
        messagebox.showerror("Autobench", f"Could not start Tkinter: {exc}")


if __name__ == "__main__":
    main()
