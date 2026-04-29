================================================================================
 visacom
================================================================================

A clean, extensible Python instrumentation library for controlling lab devices
via SCPI commands over PyVISA.

Built for Raspberry Pi (Linux, Debian-based), Python 3.11, using the
pyvisa-py pure-Python backend -- no NI-VISA required.


--------------------------------------------------------------------------------
 SUPPORTED INSTRUMENTS
--------------------------------------------------------------------------------

  Instrument              Type               Interface               VISA Resource Example
  ----------------------  -----------------  ----------------------  ------------------------------------
  Keysight 344xxA DMM     Digital Multimeter USB-TMC                 USB0::0x2A8D::0x0301::MY_SERIAL::INSTR
  Fluke 8845A / 8846A     Digital Multimeter RS-232 via USB adapter  ASRL/dev/ttyUSB0::INSTR
  Yokogawa WT310 / WT310E Power Meter        USB-TMC                 USB0::0x0B21::0x0025::MY_SERIAL::INSTR


--------------------------------------------------------------------------------
 PROJECT STRUCTURE
--------------------------------------------------------------------------------

  visacom/
  |-- visacom/
  |   |-- __init__.py      Public API re-exports
  |   |-- base.py          Instrument abstract base class
  |   |-- keysight.py      KeysightDMM driver
  |   |-- fluke.py         Fluke8845A driver
  |   |-- yokogawa.py      YokogawaWT310 driver
  |   |-- manager.py       InstrumentManager (multi-device orchestration)
  |   +-- discover.py      Automatic instrument discovery via *IDN?
  |-- example.py           Two-DMM example with CSV logging
  |-- example2.py          Auto-detect any instrument, print 10 readings
  |-- diagnose.py          Connection and communication diagnostic tool
  |-- requirements.txt
  |-- pyproject.toml
  +-- README.md


--------------------------------------------------------------------------------
 REQUIREMENTS
--------------------------------------------------------------------------------

  - Python 3.11+
  - Raspberry Pi or any Linux host with USB/serial access

  Python packages (requirements.txt):
    pyvisa    >= 1.13
    pyvisa-py >= 0.7
    pyserial  >= 3.5


--------------------------------------------------------------------------------
 INSTALLATION
--------------------------------------------------------------------------------

  git clone https://github.com/achiraabhi/Test-Bench-Automation.git
  cd Test-Bench-Automation

  python3 -m venv .venv
  source .venv/bin/activate          # Linux / Raspberry Pi
  # .venv\Scripts\activate           # Windows

  pip install -r requirements.txt


--------------------------------------------------------------------------------
 QUICK START
--------------------------------------------------------------------------------

  No configuration needed. Plug in your instruments and run:

    python example.py     # Keysight + Fluke, CSV logging
    python example2.py    # any connected instrument, 10 readings to terminal

  Both scripts auto-discover instruments by *IDN? -- no resource strings to edit.


--------------------------------------------------------------------------------
 EXAMPLE SCRIPTS
--------------------------------------------------------------------------------

  example.py
  ----------
  Connects to the Keysight DMM and Fluke 8845A, configures AC voltage,
  reads in a loop, and saves results to a timestamped CSV in logs/.

  example2.py
  -----------
  Works with any combination: Keysight, Fluke, and/or Yokogawa WT310.
  Prints 10 readings to the terminal. No CSV output.

  diagnose.py
  -----------
  Run this when an instrument misbehaves. Tests every layer from port
  visibility to a live reading, printing PASS / FAIL / WARN for each step
  and dumping the SCPI error queue.

    python diagnose.py                        # test all discovered instruments
    python diagnose.py --list                 # list VISA resources only
    python diagnose.py "ASRL/dev/ttyUSB0::INSTR"  # test one resource


--------------------------------------------------------------------------------
 DEVICE NOTES
--------------------------------------------------------------------------------

  Keysight 344xxA (USB)
  ---------------------
  - Communication : USB-TMC, fast round-trips
  - Measurement   : INIT (arm trigger) then FETCH? (retrieve result)
  - Termination   : \n

  Fluke 8845A (RS-232)
  --------------------
  - Communication : RS-232 via USB adapter, 9600 baud, 8N1
  - SYST:REM sent automatically on connect
  - Measurement   : READ? (atomic arm + measure + return)
  - Termination   : \r\n
  - SYST:LOC sent automatically on close

  Yokogawa WT310 (USB)
  --------------------
  - Communication : USB-TMC
  - Reads 7 quantities atomically: V, I, W, VA, var, PF, Hz
  - Numeric item slots configured automatically on connect
  - Over-range / invalid readings returned as None (instrument sends 9.91E+37)
  - Termination   : \n

  Yokogawa methods:
    read_power()         -- PowerReading dataclass with all 7 quantities
    read_voltage()       -- RMS voltage (V)
    read_current()       -- RMS current (A)
    read_active_power()  -- Active power (W)
    read_power_factor()  -- Power factor (0-1)
    read_frequency()     -- Supply frequency (Hz)


--------------------------------------------------------------------------------
 EXTENDING TO NEW INSTRUMENTS
--------------------------------------------------------------------------------

  1. Create visacom/keithley.py:

       from .base import Instrument

       class Keithley2400(Instrument):
           def _configure_resource(self):
               self._resource.read_termination  = "\n"
               self._resource.write_termination = "\n"

           def configure_dc_current(self):
               self.write("CONF:CURR:DC AUTO,DEF")

           def read_dc_current(self) -> float:
               return float(self.query_with_retry("READ?"))

  2. Add its IDN signature in visacom/discover.py:

       _SIGNATURES = [
           ...
           (["KEITHLEY", "2400"], "keithley"),
       ]

  3. Export it in visacom/__init__.py:

       from .keithley import Keithley2400

  No other files need to change.


--------------------------------------------------------------------------------
 LOGGING
--------------------------------------------------------------------------------

  Console  : INFO level by default
  File     : Full DEBUG output written to visacom_debug.log
  CSV      : One timestamped file per session written to log_dir/
             (enabled by passing log_dir=Path("logs") to InstrumentManager)


--------------------------------------------------------------------------------
 LICENSE
--------------------------------------------------------------------------------

  MIT
