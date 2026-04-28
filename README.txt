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

  Instrument                Interface              VISA Resource Example
  ------------------------  ---------------------  ------------------------------------
  Keysight 344xxA DMM       USB-TMC                USB0::0x2A8D::0x0301::MY_SERIAL::INSTR
  Fluke 8845A / 8846A DMM   RS-232 via USB adapter ASRL/dev/ttyUSB0::INSTR


--------------------------------------------------------------------------------
 PROJECT STRUCTURE
--------------------------------------------------------------------------------

  visacom/
  |-- visacom/
  |   |-- __init__.py      Public API re-exports
  |   |-- base.py          Instrument abstract base class
  |   |-- keysight.py      KeysightDMM driver
  |   |-- fluke.py         Fluke8845A driver
  |   +-- manager.py       InstrumentManager (multi-device orchestration)
  |-- example.py           Working two-DMM example script
  |-- requirements.txt
  |-- README.md
  +-- README.txt


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

  # Clone the repository
  git clone <your-repo-url>
  cd visacom

  # Create and activate the virtual environment
  python3 -m venv .venv
  source .venv/bin/activate          # Linux / Raspberry Pi
  # .venv\Scripts\activate           # Windows

  # Install dependencies
  pip install -r requirements.txt


--------------------------------------------------------------------------------
 QUICK START
--------------------------------------------------------------------------------

  Edit the resource strings at the top of example.py:

    KEYSIGHT_RESOURCE = "USB0::0x2A8D::0x0301::MY_SERIAL::INSTR"
    FLUKE_RESOURCE    = "ASRL/dev/ttyUSB0::INSTR"

  Then run:

    python example.py

  Sample output:

    Timestamp                      Keysight (V_AC)    Fluke (V_AC)
    -----------------------------------------------------------------
    2026-04-28T10:00:00.000Z           230.012345 V    230.009871 V
    2026-04-28T10:00:02.000Z           230.011987 V    230.010234 V

  CSV logs are written to the logs/ directory automatically.


--------------------------------------------------------------------------------
 USAGE IN YOUR OWN CODE
--------------------------------------------------------------------------------

  from visacom import KeysightDMM, Fluke8845A, InstrumentManager
  from pathlib import Path

  keysight = KeysightDMM("USB0::0x2A8D::0x0301::MY_SERIAL::INSTR")
  fluke    = Fluke8845A("ASRL/dev/ttyUSB0::INSTR")

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

  InstrumentManager closes all instruments and flushes the CSV log
  automatically on exit via the context manager.


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
  - Must send SYST:REM on connect to enter remote mode (done automatically)
  - Measurement   : READ? (atomic arm + measure + return)
  - Termination   : \r\n
  - SYST:LOC is sent automatically on close to return front-panel control


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

  2. Export it in visacom/__init__.py:

       from .keithley import Keithley2400

  3. Register it with the manager:

       mgr.add_instrument("keithley", Keithley2400("GPIB0::24::INSTR"))

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
