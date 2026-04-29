# visacom

A clean, extensible Python instrumentation library for controlling lab devices via SCPI commands over PyVISA.

Built for Raspberry Pi (Linux, Debian-based), Python 3.11, using the `pyvisa-py` pure-Python backend — no NI-VISA required.

---

## Supported Instruments

| Instrument | Type | Interface | VISA Resource Example |
|---|---|---|---|
| Keysight 344xxA DMM | Digital Multimeter | USB-TMC | `USB0::0x2A8D::0x0301::MY_SERIAL::INSTR` |
| Fluke 8845A / 8846A | Digital Multimeter | RS-232 via USB adapter | `ASRL/dev/ttyUSB0::INSTR` |
| Yokogawa WT310 / WT310E | Power Meter | USB-TMC | `USB0::0x0B21::0x0025::MY_SERIAL::INSTR` |

---

## Project Structure

```
visacom/
├── visacom/
│   ├── __init__.py      # Public API re-exports
│   ├── base.py          # Instrument abstract base class
│   ├── keysight.py      # KeysightDMM driver
│   ├── fluke.py         # Fluke8845A driver
│   ├── yokogawa.py      # YokogawaWT310 driver
│   ├── manager.py       # InstrumentManager (multi-device orchestration)
│   └── discover.py      # Automatic instrument discovery via *IDN?
├── example.py           # Two-DMM example with CSV logging
├── example2.py          # Auto-detect any instrument, print 10 readings
├── diagnose.py          # Connection and communication diagnostic tool
├── requirements.txt
├── pyproject.toml
└── README.md
```

---

## Requirements

- Python 3.11+
- Raspberry Pi (or any Linux host with USB/serial access)

Python dependencies:

```
pyvisa>=1.13
pyvisa-py>=0.7
pyserial>=3.5
```

---

## Installation

```bash
# Clone the repository
git clone https://github.com/achiraabhi/Test-Bench-Automation.git
cd Test-Bench-Automation

# Create and activate the virtual environment
python3 -m venv .venv
source .venv/bin/activate        # Linux / Raspberry Pi
# .venv\Scripts\activate         # Windows

# Install dependencies
pip install -r requirements.txt
```

---

## Quick Start

No configuration needed. Plug in your instruments and run:

```bash
# Full example with CSV logging (Keysight + Fluke)
python example.py

# Auto-detect any connected instrument, print 10 readings
python example2.py
```

Both scripts automatically scan all VISA resources and identify instruments
by their `*IDN?` response — no hardcoded resource strings required.

---

## Example Scripts

### example.py
Connects to both the Keysight DMM and Fluke 8845A, configures AC voltage,
reads in a loop, and saves results to a timestamped CSV in `logs/`.

Sample output:
```
Timestamp                      Keysight (V_AC)    Fluke (V_AC)
-----------------------------------------------------------------
2026-04-28T10:00:00.000Z           230.012345 V    230.009871 V
2026-04-28T10:00:02.000Z           230.011987 V    230.010234 V
```

### example2.py
Works with any combination of instruments — Keysight, Fluke, and/or Yokogawa WT310.
Prints 10 readings to the terminal, no CSV output.

Sample output (all three connected):
```
Connected : Keysight   [USB0::0x2A8D::0x0301::MY123::INSTR]
Connected : Fluke      [ASRL/dev/ttyUSB0::INSTR]
Connected : Yokogawa   [USB0::0x0B21::0x0025::MY456::INSTR]

#     Keysight              Fluke
---------------------------------------------
1     230.012345 V AC       230.009871 V AC

#        Voltage       Current         Power      Apparent      Reactive      PF        Freq
---------------------------------------------------------------------------------------------
1     230.1230 V     1.2340 A     284.156 W     285.100 VA      12.345 var  0.9980    50.000 Hz
```

### diagnose.py
Run this when an instrument is misbehaving. Steps through every communication
layer from port visibility to a live reading, printing PASS / FAIL / WARN
for each step and dumping the SCPI error queue.

```bash
python diagnose.py               # test all discovered instruments
python diagnose.py --list        # list available VISA resources only
python diagnose.py "ASRL/dev/ttyUSB0::INSTR"   # test one specific resource
```

---

## Usage in Your Own Code

```python
from visacom import KeysightDMM, Fluke8845A, YokogawaWT310, InstrumentManager
from visacom.discover import discover
from pathlib import Path

found = discover()

# Connect to whichever instruments are available
keysight = KeysightDMM(found["keysight"].resource_name)
fluke    = Fluke8845A(found["fluke"].resource_name)
yokogawa = YokogawaWT310(found["yokogawa"].resource_name)

# Yokogawa — read all power quantities in one call
reading = yokogawa.read_power()
print(f"{reading.voltage_V:.3f} V  {reading.current_A:.4f} A  {reading.power_W:.3f} W")
print(f"PF={reading.power_factor:.4f}  {reading.frequency_Hz:.3f} Hz")

# DMMs — standard AC voltage
with InstrumentManager(log_dir=Path("logs")) as mgr:
    mgr.add_instrument("keysight", keysight)
    mgr.add_instrument("fluke", fluke)

    mgr.configure_all(
        keysight=lambda inst: inst.configure_ac_voltage(),
        fluke=lambda inst:    inst.configure_ac_voltage(),
    )

    for row in mgr.read_loop(
        readers=dict(
            keysight=lambda inst: inst.read_ac_voltage(),
            fluke=lambda inst:    inst.read_ac_voltage(),
        ),
        interval_s=2.0,
        count=10,
    ):
        print(row)
```

---

## Device Notes

### Keysight 344xxA (USB)

- Communication: USB-TMC, fast round-trips.
- Measurement workflow: `INIT` (arm trigger) → `FETCH?` (retrieve result).
- Line termination: `\n`.

### Fluke 8845A (RS-232)

- Communication: RS-232 via USB adapter, 9600 baud, 8N1.
- Must send `SYST:REM` on connect to enter remote-control mode (done automatically).
- Measurement workflow: `READ?` (atomic arm + measure + return).
- Line termination: `\r\n`.
- `SYST:LOC` is sent automatically on close to return front-panel control.

### Yokogawa WT310 (USB)

- Communication: USB-TMC.
- Seven quantities read atomically in one query: V, I, W, VA, var, PF, Hz.
- Numeric item slots are configured automatically on connect.
- Invalid / over-range readings are returned as `None` (instrument sends `9.91E+37`).
- Line termination: `\n`.

| Method | Returns |
|---|---|
| `read_power()` | `PowerReading` dataclass with all 7 quantities |
| `read_voltage()` | RMS voltage (V) |
| `read_current()` | RMS current (A) |
| `read_active_power()` | Active power (W) |
| `read_power_factor()` | Power factor (0–1) |
| `read_frequency()` | Supply frequency (Hz) |

---

## Extending to New Instruments

Add a new driver in three steps:

**1. Create `visacom/keithley.py`:**

```python
from .base import Instrument

class Keithley2400(Instrument):
    def _configure_resource(self):
        self._resource.read_termination  = "\n"
        self._resource.write_termination = "\n"

    def configure_dc_current(self):
        self.write("CONF:CURR:DC AUTO,DEF")

    def read_dc_current(self) -> float:
        return float(self.query_with_retry("READ?"))
```

**2. Add its IDN signature in `visacom/discover.py`:**

```python
_SIGNATURES = [
    ...
    (["KEITHLEY", "2400"], "keithley"),
]
```

**3. Export it in `visacom/__init__.py`:**

```python
from .keithley import Keithley2400
```

No other files need to change.

---

## Logging

- Console: `INFO` level by default.
- File: full `DEBUG` output written to `visacom_debug.log`.
- CSV: one timestamped file per session written to `log_dir/` when a `log_dir` is passed to `InstrumentManager`.

---

## License

MIT
