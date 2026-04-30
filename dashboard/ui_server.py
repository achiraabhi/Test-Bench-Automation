"""
ui_server.py — Real-time web dashboard for visacom.

Run:
    python ui_server.py                        # http://0.0.0.0:8080
    python ui_server.py --host 127.0.0.1 --port 5000

Then open the printed URL in any browser (local or on the network).
"""

import argparse
import asyncio
import csv
import io
import logging
import sys
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

# Allow importing the visacom package from the sibling instruments/ directory
sys.path.insert(0, str(Path(__file__).parent.parent / "instruments"))

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from visacom import Fluke8845A, HiokiRM3545, KeysightDMM, YokogawaWT310
from visacom.discover import DiscoveredInstrument, discover

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

app = FastAPI(title="visacom Dashboard")

# ── Shared state ───────────────────────────────────────────────────────────────

_discovered:   Dict[str, DiscoveredInstrument] = {}
_instruments:  Dict[str, Any]                  = {}
_measurements: Dict[str, str]                  = {}
_readings:     deque                            = deque(maxlen=500)
_clients:      Set[WebSocket]                   = set()
_running       = False
_interval_s    = 2.0
_measure_task: Optional[asyncio.Task]           = None
_scan_lock     = asyncio.Lock()

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

# ── Helpers ────────────────────────────────────────────────────────────────────

async def _broadcast(msg: dict) -> None:
    dead: Set[WebSocket] = set()
    for ws in set(_clients):
        try:
            await ws.send_json(msg)
        except Exception:
            dead.add(ws)
    _clients.difference_update(dead)


def _instrument_list() -> List[dict]:
    return [
        {
            "label":     label,
            "base":      disc.label,
            "resource":  disc.resource_name,
            "idn":       disc.idn,
            "connected": label in _instruments,
            "measuring": _measurements.get(label, DEFAULT_MEASURE.get(disc.label, "")),
            "options":   MEASURE_OPTIONS.get(disc.label, []),
        }
        for label, disc in _discovered.items()
    ]


def _do_reading(label: str, inst: Any, base: str, mtype: str) -> Optional[dict]:
    """Blocking VISA read — called from asyncio.to_thread."""
    ts = datetime.now().isoformat(timespec="milliseconds")
    try:
        if base == "keysight":
            if mtype == "DC Voltage":
                inst.configure_dc_voltage()
                return {"ts": ts, "label": label, "param": "DC Voltage",
                        "value": inst.read_dc_voltage(), "unit": "V DC"}
            if mtype == "Resistance":
                inst.configure_resistance()
                return {"ts": ts, "label": label, "param": "Resistance",
                        "value": inst.read_resistance(), "unit": "Ω"}
            inst.configure_ac_voltage()
            return {"ts": ts, "label": label, "param": "AC Voltage",
                    "value": inst.read_ac_voltage(), "unit": "V AC"}

        if base == "fluke":
            if mtype == "DC Voltage":
                inst.configure_dc_voltage()
                return {"ts": ts, "label": label, "param": "DC Voltage",
                        "value": inst.read_dc_voltage(), "unit": "V DC"}
            if mtype == "Resistance":
                inst.configure_resistance()
                return {"ts": ts, "label": label, "param": "Resistance",
                        "value": inst.read_resistance(), "unit": "Ω"}
            inst.configure_ac_voltage()
            return {"ts": ts, "label": label, "param": "AC Voltage",
                    "value": inst.read_ac_voltage(), "unit": "V AC"}

        if base == "yokogawa":
            r = inst.read_power()
            return {
                "ts": ts, "label": label, "param": "Power", "multi": True,
                "values": {
                    "Voltage":   {"value": r.voltage_V,    "unit": "V"},
                    "Current":   {"value": r.current_A,    "unit": "A"},
                    "Power":     {"value": r.power_W,      "unit": "W"},
                    "Apparent":  {"value": r.apparent_VA,  "unit": "VA"},
                    "Reactive":  {"value": r.reactive_var, "unit": "var"},
                    "PF":        {"value": r.power_factor, "unit": ""},
                    "Freq":      {"value": r.frequency_Hz, "unit": "Hz"},
                },
            }

        if base == "hioki":
            val = inst.read()
            return {"ts": ts, "label": label, "param": "Resistance",
                    "value": val, "unit": "Ω"}

    except Exception as exc:
        return {"ts": ts, "label": label, "error": str(exc)}

    return None


async def _measure_all() -> None:
    for label, inst in list(_instruments.items()):
        disc = _discovered.get(label)
        if disc is None:
            continue
        mtype = _measurements.get(label, DEFAULT_MEASURE.get(disc.label, ""))
        r = await asyncio.to_thread(_do_reading, label, inst, disc.label, mtype)
        if r:
            _readings.append(r)
            await _broadcast({"type": "reading", "data": r})


async def _measure_loop() -> None:
    global _running
    while _running:
        try:
            await _measure_all()
        except Exception as exc:
            logger.error("Measurement loop error: %s", exc)
        await asyncio.sleep(_interval_s)


# ── REST endpoints ─────────────────────────────────────────────────────────────

@app.get("/api/scan")
async def api_scan():
    global _discovered, _instruments, _running, _measure_task

    async with _scan_lock:
        _running = False
        if _measure_task:
            _measure_task.cancel()
            try:
                await _measure_task
            except asyncio.CancelledError:
                pass
            _measure_task = None

        await _broadcast({"type": "control", "running": False})
        await _broadcast({"type": "status", "level": "info",
                          "message": "Scanning for instruments…"})

        for inst in _instruments.values():
            try:
                inst.close()
            except Exception:
                pass
        _instruments.clear()

        found = await asyncio.to_thread(discover)
        _discovered = found

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
                _instruments[label] = inst
                _measurements.setdefault(label, DEFAULT_MEASURE.get(disc.label, ""))
            except Exception as exc:
                logger.warning("Connect failed [%s]: %s", label, exc)

    state = _instrument_list()
    await _broadcast({"type": "instruments", "data": state})
    n = len(_instruments)
    level = "info" if n else "warn"
    msg = (f"Found {len(found)} instrument(s), {n} connected."
           if found else "No instruments found. Check connections.")
    await _broadcast({"type": "status", "level": level, "message": msg})
    return {"instruments": state}


@app.post("/api/start")
async def api_start():
    global _running, _measure_task
    if not _instruments:
        return {"ok": False, "error": "No instruments connected — run Scan first."}
    if _running:
        return {"ok": True}
    _running = True
    _measure_task = asyncio.create_task(_measure_loop())
    await _broadcast({"type": "control", "running": True})
    return {"ok": True}


@app.post("/api/stop")
async def api_stop():
    global _running, _measure_task
    _running = False
    if _measure_task:
        _measure_task.cancel()
        try:
            await _measure_task
        except asyncio.CancelledError:
            pass
        _measure_task = None
    await _broadcast({"type": "control", "running": False})
    return {"ok": True}


@app.post("/api/interval/{seconds}")
async def api_interval(seconds: float):
    global _interval_s
    _interval_s = max(0.5, min(float(seconds), 60.0))
    await _broadcast({"type": "interval", "value": _interval_s})
    return {"interval": _interval_s}


@app.post("/api/measure/{label}/{mtype}")
async def api_set_measure(label: str, mtype: str):
    if label not in _discovered:
        return {"ok": False, "error": "Unknown instrument"}
    _measurements[label] = mtype
    inst = _instruments.get(label)
    if inst:
        base = _discovered[label].label
        try:
            if base in ("keysight", "fluke"):
                if mtype == "DC Voltage":
                    await asyncio.to_thread(inst.configure_dc_voltage)
                elif mtype == "Resistance":
                    await asyncio.to_thread(inst.configure_resistance)
                else:
                    await asyncio.to_thread(inst.configure_ac_voltage)
        except Exception as exc:
            logger.warning("Reconfigure %s: %s", label, exc)
    await _broadcast({"type": "instruments", "data": _instrument_list()})
    return {"ok": True, "label": label, "measuring": mtype}


@app.get("/api/export")
async def api_export():
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["timestamp", "instrument", "parameter", "value", "unit"])
    for r in list(_readings):
        if "error" in r:
            w.writerow([r["ts"], r["label"], "ERROR", r["error"], ""])
        elif r.get("multi"):
            for param, v in r["values"].items():
                w.writerow([r["ts"], r["label"], param, v["value"], v["unit"]])
        else:
            w.writerow([r["ts"], r["label"], r.get("param", ""),
                        r.get("value", ""), r.get("unit", "")])
    buf.seek(0)
    fname = f"visacom_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={fname}"},
    )


@app.post("/api/clear")
async def api_clear():
    _readings.clear()
    await _broadcast({"type": "cleared"})
    return {"ok": True}


# ── WebSocket ──────────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    _clients.add(ws)
    try:
        await ws.send_json({"type": "instruments", "data": _instrument_list()})
        await ws.send_json({"type": "history",     "data": list(_readings)})
        await ws.send_json({"type": "control",     "running": _running})
        await ws.send_json({"type": "interval",    "value": _interval_s})
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _clients.discard(ws)


# ── Static files ───────────────────────────────────────────────────────────────

STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)


@app.get("/", response_class=HTMLResponse)
async def root():
    p = STATIC_DIR / "index.html"
    return p.read_text(encoding="utf-8") if p.exists() else HTMLResponse(
        "<h1>static/index.html not found</h1>", status_code=404
    )


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="visacom Web Dashboard")
    ap.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    ap.add_argument("--port", type=int, default=8080, help="Port (default: 8080)")
    args = ap.parse_args()
    host_display = "localhost" if args.host == "0.0.0.0" else args.host
    print(f"\n  visacom dashboard  →  http://{host_display}:{args.port}\n")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
