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

    python diagnose.py                             # test all discovered instruments
    python diagnose.py --list                      # list VISA resources only
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

  Yokogawa WT310 / WT310E (USB)
  -----------------------------
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
  to see what the kernel detected on the USB bus.


  STEP 2 -- Create the udev rule file
  ------------------------------------
    sudo nano /etc/udev/rules.d/99-usb-instruments.rules

  nano will open a blank file. Do not close it yet.


  STEP 3 -- Type the rule
  ------------------------
    SUBSYSTEM=="usb", ATTR{idVendor}=="0b21", ATTR{idProduct}=="0039", MODE="0666"

  Replace 0b21 and 0039 with your actual Vendor ID and Product ID.

  What each part means:
    SUBSYSTEM=="usb"          -- match only USB devices
    ATTR{idVendor}=="0b21"    -- match this Vendor ID (Yokogawa)
    ATTR{idProduct}=="0039"   -- match this Product ID (WT310E)
    MODE="0666"               -- give read+write access to all users

  To add a second instrument (e.g. Keysight), add another line below:
    SUBSYSTEM=="usb", ATTR{idVendor}=="2a8d", ATTR{idProduct}=="0301", MODE="0666"


  STEP 4 -- Save and exit nano
  -----------------------------
    CTRL + O   then   Enter   -- saves the file
    CTRL + X                  -- exits nano

  Verify it was saved:
    cat /etc/udev/rules.d/99-usb-instruments.rules


  STEP 5 -- Reload udev rules
  ----------------------------
    sudo udevadm control --reload-rules
    sudo udevadm trigger

  Applies the new rule without a reboot.


  STEP 6 -- Replug the USB device
  --------------------------------
  Unplug and replug the instrument. The rule only fires on a new connection
  event.


  STEP 7 -- Test with Python
  ---------------------------
    python3 -c "import pyvisa; print(pyvisa.ResourceManager('@py').list_resources())"

  Expected output (no ???, no warnings):
    ('USB0::0x0B21::0x0039::MY123456::0::INSTR',)


--------------------------------------------------------------------------------
 3. TEMPORARY QUICK FIX
--------------------------------------------------------------------------------

  If you need to get running immediately:

    sudo chmod -R 777 /dev/bus/usb/

  NOTE: This is NOT permanent. Permissions reset on every reboot or replug.
  Use the udev rule method for a lasting fix.


--------------------------------------------------------------------------------
 4. OPTIONAL -- SAFER METHOD USING A GROUP
--------------------------------------------------------------------------------

  Instead of MODE="0666" (open to everyone), restrict access to the plugdev
  group:

  Rule:
    SUBSYSTEM=="usb", ATTR{idVendor}=="0b21", ATTR{idProduct}=="0039", GROUP="plugdev", MODE="0660"

  Add your user to the group:
    sudo usermod -aG plugdev $USER

  A reboot (or "newgrp plugdev") is required for the group change to take
  effect. Verify membership:
    groups $USER    -- plugdev should appear in the list


--------------------------------------------------------------------------------
 5. FOR RS-232 / SERIAL INSTRUMENTS (Fluke 8845A)
--------------------------------------------------------------------------------

  Serial-over-USB adapters (/dev/ttyUSB0) use a different group -- dialout:

    sudo usermod -aG dialout $USER

  Then reboot, or run:
    newgrp dialout

  Verify:
    ls -l /dev/ttyUSB0
    # crw-rw---- 1 root dialout 188, 0 Apr 28 10:00 /dev/ttyUSB0


--------------------------------------------------------------------------------
 6. SUMMARY TABLE
--------------------------------------------------------------------------------

  Step  Command                                            Purpose
  ----  -------------------------------------------------  --------------------------------
  1     lsusb                                              Find Vendor ID and Product ID
  2     sudo nano /etc/udev/rules.d/99-usb-instruments...  Create rule file
  3     (type rule, save with CTRL+O, exit with CTRL+X)   Add rule
  4     sudo udevadm control --reload-rules                Reload without reboot
  5     sudo udevadm trigger                               Re-evaluate connected devices
  6     (unplug and replug)                                Fire the new rule
  7     python3 -c "... list_resources()"                  Verify no ???
  -     sudo usermod -aG dialout $USER                     Fix serial (/dev/ttyUSBx)
  -     sudo usermod -aG plugdev $USER                     Safer alternative to 0666
  -     sudo chmod -R 777 /dev/bus/usb/                    Temporary fix only


--------------------------------------------------------------------------------
 7. TIPS AND NOTES
--------------------------------------------------------------------------------

  - One rule covers all USB ports. The rule matches by Vendor/Product ID,
    not by which physical USB port is used.

  - Recheck IDs if the device changes. Different firmware versions or
    hardware revisions can have different Product IDs. Confirm with lsusb
    after any firmware update.

  - Raspberry Pi default user is "pi". If $USER does not expand correctly
    after sudo, use your literal username:
      sudo usermod -aG plugdev pi

  - dmesg is your best friend. If the device does not appear in lsusb at
    all, run:  dmesg | tail -30  immediately after plugging in.

  - Check the rule is loaded after reloading:
      udevadm info --query=property --name=/dev/bus/usb/001/004 | grep -i mode

  - Multiple instruments in one file. Add all rules to 99-usb-instruments.rules,
    one line per instrument. No separate file needed per device.

  - This guide applies to ALL USB-TMC instruments, not just the Yokogawa.
    The same steps work for the Keysight DMM -- just swap in its IDs.


--------------------------------------------------------------------------------
 LICENSE
--------------------------------------------------------------------------------

  MIT
