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
  Yokogawa WT310 / WT310E Power Meter        USB-TMC                 USB0::0x0B21::0x0039::MY_SERIAL::INSTR
  Hioki RM3544 / RM3545   Resistance Meter   RS-232 / USB virtual COM ASRL/dev/ttyUSB1::INSTR


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
  |   |-- hioki.py         HiokiRM3545 driver
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
  Works with any combination: Keysight, Fluke, Yokogawa WT310, Hioki RM3545.
  Prints 10 readings to the terminal. No CSV output.

  diagnose.py
  -----------
  Run this when an instrument misbehaves. Tests every layer from port
  visibility to a live reading, printing PASS / FAIL / WARN for each step
  and dumping the SCPI error queue.

    python diagnose.py                             # test all discovered instruments
    python diagnose.py --list                      # list VISA resources only
    python diagnose.py "ASRL/dev/ttyUSB1::INSTR"  # test one resource


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

  Hioki RM3544 / RM3545 (RS-232 / USB virtual COM)
  -------------------------------------------------
  - Communication : 9600 baud, 8N1, no flow control
  - Termination   : CR+LF (\r\n) on both TX and RX
  - Call initialize() once after connecting
  - Non-standard SCPI -- uses Hioki-specific command names (see table below)
  - Special return values: "OL" (overrange), "UL" (underrange), "ERROR"
  - '+' sign in responses is returned as ASCII space -- handled automatically

  Measurement methods:
    read()                -- :READ?             single-shot, blocks up to 15 s
    fetch()               -- :FETCH?            returns last value (continuous mode)
    measure_resistance()  -- :MEASURE:RESISTANCE?  one-liner, no setup needed

  Non-standard commands vs SCPI:
    :SAMPLE:RATE FAST|MED|SLOW|SLOW2   vs  :SENSE:APERTURE <n>
    :ADJUST                            vs  :CALIBRATION:ZERO
    :CALCULATE:LIMIT:RESULT?           vs  :CALCULATE:LIMIT:FAIL?
    :CALCULATE:LIMIT:JUDGE?            vs  :CALCULATE:LIMIT:FAIL?
    :MEMORY                            vs  :TRACE
    :SENSE:SCAN:...                    vs  :ROUTE:SCAN:...
    Two ESRs (:ESR0?, :ESR1?)          vs  Single *ESR?

  Measurement speed:
    FAST   -- 100 ms  (high-speed scanning)
    MED    -- 300 ms  (default, general use)
    SLOW   -- 1 s     (higher accuracy)
    SLOW2  -- 2 s     (best accuracy, RM3545 only)


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


================================================================================
 HOW TO FIX USB PERMISSION ERRORS FOR PyVISA ON LINUX
================================================================================

  Follow this guide if your instrument shows as ??? in PyVISA or you get
  a "Permission denied" error on /dev/bus/usb.


--------------------------------------------------------------------------------
 1. PROBLEM DESCRIPTION
--------------------------------------------------------------------------------

  On Linux, USB devices are owned by root by default. When PyVISA tries to
  open the instrument without elevated privileges it fails with:

    PermissionError: [Errno 13] Permission denied: '/dev/bus/usb/001/005'

  or the resource appears but cannot be used:

    >>> rm.list_resources()
    ('USB0::0x0B21::0x0039::??????::0::INSTR',)   # ??? = no access

  WHY: Linux kernel security blocks unprivileged processes from opening USB
  devices. The fix is a udev rule -- a small config file that tells the kernel
  to relax permissions for a specific Vendor ID / Product ID pair every time
  that device is plugged in.


--------------------------------------------------------------------------------
 2. STEP-BY-STEP SOLUTION
--------------------------------------------------------------------------------

  STEP 1 -- Find Vendor ID and Product ID with lsusb
  ---------------------------------------------------
  Plug in the instrument, then run:

    lsusb

  Example output:

    Bus 001 Device 001: ID 1d6b:0002 Linux Foundation 2.0 root hub
    Bus 001 Device 004: ID 0b21:0039 Yokogawa Electric Corp. WT310E
    Bus 001 Device 005: ID 2a8d:0301 Keysight Technologies

  - Characters BEFORE the colon = Vendor ID  (e.g. 0b21)
  - Characters AFTER  the colon = Product ID (e.g. 0039)

  If nothing new appears, run:  dmesg | tail -20


  STEP 2 -- Create the udev rule file
  ------------------------------------
    sudo nano /etc/udev/rules.d/99-usb-instruments.rules


  STEP 3 -- Type the rule
  ------------------------
    SUBSYSTEM=="usb", ATTR{idVendor}=="0b21", ATTR{idProduct}=="0039", MODE="0666"

  Add one line per instrument. No separate file needed per device.


  STEP 4 -- Save and exit nano
  -----------------------------
    CTRL + O   then   Enter   -- saves the file
    CTRL + X                  -- exits nano


  STEP 5 -- Reload udev rules
  ----------------------------
    sudo udevadm control --reload-rules
    sudo udevadm trigger


  STEP 6 -- Replug the USB device
  --------------------------------
  Unplug and replug. The rule only fires on a new connection event.


  STEP 7 -- Test with Python
  ---------------------------
    python3 -c "import pyvisa; print(pyvisa.ResourceManager('@py').list_resources())"

  Expected (no ???):
    ('USB0::0x0B21::0x0039::MY123456::0::INSTR',)


--------------------------------------------------------------------------------
 3. TEMPORARY QUICK FIX
--------------------------------------------------------------------------------

    sudo chmod -R 777 /dev/bus/usb/

  NOT permanent -- resets on every reboot or replug.


--------------------------------------------------------------------------------
 4. OPTIONAL -- SAFER METHOD USING A GROUP
--------------------------------------------------------------------------------

  Rule:
    SUBSYSTEM=="usb", ATTR{idVendor}=="0b21", ATTR{idProduct}=="0039", GROUP="plugdev", MODE="0660"

  Add your user:
    sudo usermod -aG plugdev $USER

  Reboot or run:  newgrp plugdev


--------------------------------------------------------------------------------
 5. FOR RS-232 / SERIAL INSTRUMENTS (Fluke 8845A, Hioki RM3545)
--------------------------------------------------------------------------------

  Serial-over-USB adapters (/dev/ttyUSBx) use the dialout group:

    sudo usermod -aG dialout $USER

  Then reboot or:  newgrp dialout

  Verify:
    ls -l /dev/ttyUSB0
    # crw-rw---- 1 root dialout ...


--------------------------------------------------------------------------------
 6. SUMMARY TABLE
--------------------------------------------------------------------------------

  Step  Command                                            Purpose
  ----  -------------------------------------------------  --------------------------------
  1     lsusb                                              Find Vendor ID and Product ID
  2     sudo nano /etc/udev/rules.d/99-usb-instruments...  Create rule file
  3     (type rule, CTRL+O Enter, CTRL+X)                  Add and save rule
  4     sudo udevadm control --reload-rules                Reload without reboot
  5     sudo udevadm trigger                               Re-evaluate connected devices
  6     (unplug and replug)                                Fire the new rule
  7     python3 -c "... list_resources()"                  Verify no ???
  -     sudo usermod -aG dialout $USER                     Fix serial /dev/ttyUSBx
  -     sudo usermod -aG plugdev $USER                     Safer alternative to 0666
  -     sudo chmod -R 777 /dev/bus/usb/                    Temporary fix only


--------------------------------------------------------------------------------
 7. TIPS AND NOTES
--------------------------------------------------------------------------------

  - One rule covers all USB ports (matches by Vendor/Product ID, not port).
  - Recheck IDs after firmware updates -- Product ID may change.
  - Raspberry Pi default user is "pi": sudo usermod -aG plugdev pi
  - dmesg | tail -30 immediately after plugging in shows kernel USB events.
  - Hioki RM3545 uses USB virtual COM -- use dialout group (Section 5),
    not the udev rule.
  - Multiple instruments: one rule per line in 99-usb-instruments.rules.


--------------------------------------------------------------------------------
 LICENSE
--------------------------------------------------------------------------------

  MIT
