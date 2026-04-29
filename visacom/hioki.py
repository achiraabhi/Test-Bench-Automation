"""
hioki.py — Driver for the Hioki RM3544 / RM3545 / RM3545-01 / RM3545-02
            Resistance Meter.

Communication: RS-232C or USB (virtual COM port) via PyVISA ASRL resource.
Resource string examples:
    ASRL/dev/ttyUSB0::INSTR   (Linux / Raspberry Pi)
    ASRL3::INSTR               (Windows COM3)

Key behavioural differences from standard SCPI
-----------------------------------------------
• Two event status registers (ESR0, ESR1) instead of one.
• :TRIGGER:EDGE instead of :TRIGGER:SLOPE.
• :ADJUST instead of :CALIBRATION:ZERO.
• :MEMORY instead of :TRACE.
• :CALCULATE:LIMIT:RESULT? / :JUDGE? instead of :CALCULATE:LIMIT:FAIL?.
• :SAMPLE:RATE FAST|MED|SLOW|SLOW2 instead of numeric aperture.
• :SENSE:SCAN:... instead of :ROUTE:SCAN:...

Communication timing rules (from the manual)
---------------------------------------------
• Wait ≥ 1 ms between successive commands (this driver uses 2 ms).
• :READ? blocks until the measurement completes — allow up to 15 s.
• After *RST or :SYSTEM:RESET wait 1 s before the next command.
• For :FETCH? in continuous mode, wait ≥ 2× the measurement period.

Measurement response special values
------------------------------------
• +9.9000E+37  →  "OL"    (overrange)
• −9.9000E+37  →  "UL"    (underrange)
• +9.9100E+37  →  "ERROR" (measurement error)
• The '+' sign in positive responses is returned as ASCII space (0x20).
  Always strip() before calling float().
"""

import logging
import time
from typing import Optional, Union

import pyvisa.constants as visa_const

from .base import Instrument, InstrumentError

logger = logging.getLogger(__name__)

# ── Special measurement sentinels ────────────────────────────────────────────
OL          = "OL"      # overrange
UL          = "UL"      # underrange
MEAS_ERROR  = "ERROR"   # measurement error

_OL_VALUE   =  9.9000e37
_UL_VALUE   = -9.9000e37
_ERR_VALUE  =  9.9100e37

MeasResult = Union[float, str]   # float  |  "OL"  |  "UL"  |  "ERROR"


class HiokiRM3545(Instrument):
    """
    Hioki RM3544 / RM3545 / RM3545-01 / RM3545-02 Resistance Meter driver.

    Measurement modes
    -----------------
    Continuous (default at power-on)::

        meter.set_continuous(True)
        meter.set_trigger_source("IMM")
        while True:
            time.sleep(0.4)          # ≥ 2× MED period
            val = meter.fetch()

    Single-shot (recommended for PC control)::

        meter.set_continuous(False)
        meter.set_trigger_source("IMM")
        val = meter.read()           # blocks until complete

    One-liner (no prior setup needed)::

        val = meter.measure_resistance()
    """

    # ── Serial framing ───────────────────────────────────────────────────
    _BAUD_RATE:   int = 9600
    _DATA_BITS:   int = 8
    _STOP_BITS        = visa_const.StopBits.one
    _PARITY           = visa_const.Parity.none
    _FLOW_CONTROL     = visa_const.ControlFlow.none
    _WRITE_TERM:  str = "\r\n"
    _READ_TERM:   str = "\r\n"

    # ── Timing ───────────────────────────────────────────────────────────
    _CMD_DELAY_S:     float = 0.002    # ≥1 ms between commands (+ margin)
    _RESET_DELAY_S:   float = 1.0      # wait after *RST / :SYSTEM:RESET
    _READ_TIMEOUT_MS: int   = 15_000   # :READ? can block up to ~10 s (SLOW2)

    # ── Valid parameter sets ─────────────────────────────────────────────
    _VALID_SPEEDS   = {"FAST", "MED", "SLOW", "SLOW2"}
    _VALID_SOURCES  = {"IMM", "EXT", "BUS"}
    _VALID_MODES    = {"ABS", "REL", "PERC"}
    _VALID_FREQS    = {50, 60}

    def __init__(
        self,
        resource_name: str,
        timeout_ms: int = 10_000,
        retries: int = Instrument.DEFAULT_RETRIES,
    ) -> None:
        # Longer default timeout — serial + slow measurements
        super().__init__(resource_name, timeout_ms=timeout_ms, retries=retries)

    # ── Resource setup ────────────────────────────────────────────────────

    def _configure_resource(self) -> None:
        r = self._resource
        r.baud_rate    = self._BAUD_RATE
        r.data_bits    = self._DATA_BITS
        r.stop_bits    = self._STOP_BITS
        r.parity       = self._PARITY
        r.flow_control = self._FLOW_CONTROL
        r.read_termination  = self._READ_TERM
        r.write_termination = self._WRITE_TERM
        logger.debug(
            "Hioki serial configured: %d baud, 8N1, term=CR+LF",
            self._BAUD_RATE,
        )
        # Explicitly turn header off so every response is plain data.
        # The instrument power-on default is already OFF, but be explicit.
        time.sleep(0.1)
        try:
            self.write(":SYSTEM:HEADER OFF")
            time.sleep(self._CMD_DELAY_S)
        except Exception:
            pass  # if instrument is mid-measurement, best-effort only

    # ── Recommended initialization sequence ──────────────────────────────

    def initialize(
        self,
        line_freq: int = 50,
        speed: str = "MED",
        auto_range: bool = True,
    ) -> None:
        """
        Full initialization: factory-reset then apply recommended settings.

        Call this once after connecting.  *RST does NOT reset communication
        settings (baud rate, termination) so the serial link stays intact.

        Args:
            line_freq:  Mains frequency — 50 or 60 Hz.  Match your supply.
            speed:      Measurement speed — FAST, MED, SLOW, or SLOW2.
            auto_range: True to enable auto-range.
        """
        self.write("*RST")
        time.sleep(self._RESET_DELAY_S)
        self.write(":SYSTEM:HEADER OFF")
        self.write(f":SYSTEM:LFREQUENCY {line_freq}")
        self.write(":INITIATE:CONTINUOUS OFF")
        self.write(":TRIGGER:SOURCE IMM")
        if auto_range:
            self.write(":SENSE:RESISTANCE:RANGE:AUTO ON")
        self.write(f":SAMPLE:RATE {speed.upper()}")
        self.write("*CLS")
        logger.info(
            "Hioki [%s] initialized — %d Hz, speed=%s, auto-range=%s",
            self.resource_name, line_freq, speed, auto_range,
        )

    # ── Measurement value parsing ─────────────────────────────────────────

    @staticmethod
    def _parse(raw: str) -> MeasResult:
        """
        Parse one measurement token from the instrument.

        The Hioki returns ASCII space (0x20) in place of '+' in positive
        values — strip() converts " 1.06E+03" to "1.06E+03" before float().
        """
        s = raw.strip()
        try:
            val = float(s)
        except ValueError:
            logger.warning("Hioki: unparseable response %r", raw)
            return MEAS_ERROR

        if val >= _OL_VALUE:
            return OL
        elif val <= _UL_VALUE:
            return UL
        elif val >= _ERR_VALUE:
            return MEAS_ERROR
        return val

    # ── Reading measured values ───────────────────────────────────────────

    def fetch(self) -> MeasResult:
        """
        Return the last completed reading without triggering (:FETCH?).

        Use in continuous measurement mode.  Ensure you wait ≥ 2× the
        measurement period between calls so the value has been refreshed.
        """
        raw = self.query_with_retry(":FETCH?")
        value = self._parse(raw)
        logger.debug("Hioki FETCH: %s Ω", value)
        return value

    def read(self) -> MeasResult:
        """
        Trigger one measurement and block until the result is ready (:READ?).

        The instrument must be in :INITIATE:CONTINUOUS OFF mode.
        A 15-second read timeout accommodates even SLOW2 measurements.
        """
        orig = self._resource.timeout
        self._resource.timeout = self._READ_TIMEOUT_MS
        try:
            raw = self.query_with_retry(":READ?")
        finally:
            self._resource.timeout = orig
        value = self._parse(raw)
        logger.debug("Hioki READ: %s Ω", value)
        return value

    def measure_resistance(self) -> MeasResult:
        """
        Convenience one-shot: configure for resistance, trigger, return result.

        Equivalent to :MEASURE:RESISTANCE? — no prior setup needed.
        """
        orig = self._resource.timeout
        self._resource.timeout = self._READ_TIMEOUT_MS
        try:
            raw = self.query_with_retry(":MEASURE:RESISTANCE?")
        finally:
            self._resource.timeout = orig
        return self._parse(raw)

    def abort(self) -> None:
        """Abort the current measurement and return the instrument to idle."""
        self.write(":ABORT")
        time.sleep(self._CMD_DELAY_S)

    # ── Range ─────────────────────────────────────────────────────────────

    def set_range(self, ohms: Union[float, str]) -> None:
        """
        Set measurement range.

        Args:
            ohms: One of 100, 1000, 10E3, 100E3, 1E6, 10E6, 100E6, 200E6
                  (RM3545 only for 200E6).  Pass 'AUTO' to enable auto-range.
        """
        if str(ohms).upper() == "AUTO":
            self.write(":SENSE:RESISTANCE:RANGE:AUTO ON")
        else:
            self.write(":SENSE:RESISTANCE:RANGE:AUTO OFF")
            self.write(f":SENSE:RESISTANCE:RANGE {ohms}")
        time.sleep(self._CMD_DELAY_S)
        logger.info("Hioki range: %s Ω", ohms)

    def get_range(self) -> float:
        """Return the current measurement range in ohms."""
        return float(self.query(":SENSE:RESISTANCE:RANGE?").strip())

    def set_auto_range(self, enable: bool) -> None:
        """Enable or disable auto-range."""
        self.write(f":SENSE:RESISTANCE:RANGE:AUTO {'ON' if enable else 'OFF'}")
        time.sleep(self._CMD_DELAY_S)

    # ── Measurement speed ─────────────────────────────────────────────────

    def set_speed(self, speed: str) -> None:
        """
        Set measurement speed / integration time.

        Args:
            speed:
                FAST  — 100 ms per measurement (fastest, lowest accuracy)
                MED   — 300 ms per measurement (default)
                SLOW  — 1 s  per measurement
                SLOW2 — 2 s  per measurement (best accuracy, RM3545 only)
        """
        speed = speed.upper()
        if speed not in self._VALID_SPEEDS:
            raise ValueError(f"Speed must be one of {self._VALID_SPEEDS}, got {speed!r}.")
        self.write(f":SAMPLE:RATE {speed}")
        time.sleep(self._CMD_DELAY_S)
        logger.debug("Hioki speed: %s", speed)

    def get_speed(self) -> str:
        """Return the current measurement speed string."""
        return self.query(":SAMPLE:RATE?").strip()

    # ── Wire mode (RM3545 only) ───────────────────────────────────────────

    def set_wire_mode(self, wires: int) -> None:
        """
        Set 2-wire or 4-wire measurement.

        Args:
            wires: 2 or 4.
        """
        if wires not in (2, 4):
            raise ValueError(f"Wire mode must be 2 or 4, got {wires}.")
        self.write(f":SENSE:WIRE {wires}")
        time.sleep(self._CMD_DELAY_S)

    # ── Triggering ────────────────────────────────────────────────────────

    def set_continuous(self, enable: bool) -> None:
        """
        Enable or disable continuous measurement mode.

        ON  — free-running; use fetch() to read the latest value.
        OFF — single-shot; use read() to trigger and retrieve one reading.
        """
        self.write(f":INITIATE:CONTINUOUS {'ON' if enable else 'OFF'}")
        time.sleep(self._CMD_DELAY_S)

    def initiate(self) -> None:
        """
        Move from idle to trigger-wait state (:INITIATE:IMMEDIATE).

        Only needed in single-shot mode when you want to pre-arm the trigger
        before issuing *TRG or waiting for an EXT trigger.
        """
        self.write(":INITIATE:IMMEDIATE")
        time.sleep(self._CMD_DELAY_S)

    def set_trigger_source(self, source: str) -> None:
        """
        Set the trigger source.

        Args:
            source:
                IMM — internal timer (default)
                EXT — external TRIG signal or front-panel [ENTER] key
                BUS — *TRG software trigger only
        """
        source = source.upper()
        if source not in self._VALID_SOURCES:
            raise ValueError(f"Trigger source must be one of {self._VALID_SOURCES}.")
        self.write(f":TRIGGER:SOURCE {source}")
        time.sleep(self._CMD_DELAY_S)

    def trigger(self) -> None:
        """Send software trigger (*TRG).  Only valid in BUS trigger mode."""
        self.write("*TRG")
        time.sleep(self._CMD_DELAY_S)

    # ── Zero adjustment ───────────────────────────────────────────────────

    def zero(self) -> None:
        """
        Execute zero adjustment (:ADJUST).

        Short the measurement probes before calling this.  The instrument
        measures the residual resistance and stores it as an offset.
        """
        self.write(":ADJUST")
        time.sleep(0.5)  # adjustment takes a moment
        logger.info("Hioki [%s] zero adjustment executed", self.resource_name)

    def clear_zero(self) -> None:
        """Clear the stored zero adjustment value."""
        self.write(":ADJUST:CLEAR")
        time.sleep(self._CMD_DELAY_S)

    def set_zero_enable(self, enable: bool) -> None:
        """Enable or disable application of the stored zero value."""
        self.write(f":ADJUST:ENABLE {'ON' if enable else 'OFF'}")
        time.sleep(self._CMD_DELAY_S)

    # ── Offset Voltage Correction (OVC) ──────────────────────────────────

    def set_ovc(self, enable: bool) -> None:
        """
        Enable Offset Voltage Correction.

        OVC eliminates thermo-EMF (Seebeck effect) errors in low-resistance
        measurements by reversing the measurement current and averaging.
        """
        self.write(f":SENSE:RESISTANCE:OVC {'ON' if enable else 'OFF'}")
        time.sleep(self._CMD_DELAY_S)

    # ── Averaging ─────────────────────────────────────────────────────────

    def set_averaging(self, count: int) -> None:
        """
        Enable averaging over multiple readings.

        Args:
            count: Number of samples to average — 2 to 100.
        """
        if not 2 <= count <= 100:
            raise ValueError(f"Averaging count must be 2–100, got {count}.")
        self.write(":CALCULATE:AVERAGE:STATE ON")
        self.write(f":CALCULATE:AVERAGE:COUNT {count}")
        time.sleep(self._CMD_DELAY_S)
        logger.debug("Hioki averaging: %d samples", count)

    def disable_averaging(self) -> None:
        """Disable averaging."""
        self.write(":CALCULATE:AVERAGE:STATE OFF")
        time.sleep(self._CMD_DELAY_S)

    # ── Comparator (limit test) ───────────────────────────────────────────

    def configure_limits(
        self,
        upper: float,
        lower: float,
        mode: str = "ABS",
        reference: Optional[float] = None,
        percent: Optional[float] = None,
    ) -> None:
        """
        Configure the limit comparator.

        Args:
            upper:     Upper limit (ABS) or maximum deviation (REL/PERC).
            lower:     Lower limit or minimum deviation.
            mode:      ABS  — absolute upper/lower values.
                       REL  — reference ± absolute deviation.
                       PERC — reference ± percentage.
            reference: Reference value for REL and PERC modes.
            percent:   Percentage tolerance for PERC mode.
        """
        mode = mode.upper()
        if mode not in self._VALID_MODES:
            raise ValueError(f"Limit mode must be one of {self._VALID_MODES}.")
        self.write(f":CALCULATE:LIMIT:MODE {mode}")
        self.write(f":CALCULATE:LIMIT:UPPER {upper}")
        self.write(f":CALCULATE:LIMIT:LOWER {lower}")
        if reference is not None:
            self.write(f":CALCULATE:LIMIT:REFERENCE {reference}")
        if percent is not None:
            self.write(f":CALCULATE:LIMIT:PERCENT {percent}")
        time.sleep(self._CMD_DELAY_S)
        logger.info(
            "Hioki limits: mode=%s  upper=%s  lower=%s", mode, upper, lower
        )

    def enable_comparator(self, enable: bool) -> None:
        """Enable or disable the limit comparator."""
        self.write(f":CALCULATE:LIMIT:STATE {'ON' if enable else 'OFF'}")
        time.sleep(self._CMD_DELAY_S)

    def get_result(self) -> str:
        """
        Return the raw comparator result.

        Returns: 'IN', 'HI', or 'LO'.
        """
        return self.query(":CALCULATE:LIMIT:RESULT?").strip()

    def get_judgment(self) -> str:
        """
        Return the pass/fail judgment.

        Returns: 'PASS' or 'FAIL'.
        """
        return self.query(":CALCULATE:LIMIT:JUDGE?").strip()

    def set_beeper(self, condition: str, hi_beep: int = 1, lo_beep: int = 0) -> None:
        """
        Configure the comparator beeper.

        Args:
            condition: 'IN' (beep when in-tolerance), 'OUT' (beep when out),
                       or 'OFF'.
            hi_beep:   1 = beep on HI, 0 = silent.
            lo_beep:   1 = beep on LO, 0 = silent.
        """
        self.write(f":CALCULATE:LIMIT:BEEPER {condition.upper()},{hi_beep},{lo_beep}")
        time.sleep(self._CMD_DELAY_S)

    # ── Statistics (RM3545 only) ──────────────────────────────────────────

    def enable_statistics(self, enable: bool) -> None:
        """Enable or disable statistics accumulation."""
        self.write(f":CALCULATE:STATISTICS:STATE {'ON' if enable else 'OFF'}")

    def clear_statistics(self) -> None:
        """Clear all accumulated statistics."""
        self.write(":CALCULATE:STATISTICS:CLEAR")

    def get_statistics(self) -> dict:
        """
        Query all statistics in one call.

        Returns a dict with keys: count, mean, maximum, minimum,
        deviation, cp.  Values are float or 'OL'/'UL'/'ERROR'.
        """
        return {
            "count":     int(self.query(":CALCULATE:STATISTICS:NUMBER?").strip()),
            "mean":      self._parse(self.query(":CALCULATE:STATISTICS:MEAN?")),
            "maximum":   self._parse(self.query(":CALCULATE:STATISTICS:MAXIMUM?")),
            "minimum":   self._parse(self.query(":CALCULATE:STATISTICS:MINIMUM?")),
            "deviation": self._parse(self.query(":CALCULATE:STATISTICS:DEVIATION?")),
            "cp":        self._parse(self.query(":CALCULATE:STATISTICS:CP?")),
        }

    # ── BIN sorting (RM3545 only) ─────────────────────────────────────────

    def configure_bin(
        self,
        bin_num: int,
        upper: float,
        lower: float,
        mode: str = "ABS",
        reference: Optional[float] = None,
        percent: Optional[float] = None,
        enable: bool = True,
    ) -> None:
        """
        Configure one BIN sort boundary.

        Args:
            bin_num:   BIN number 1–10.
            upper:     Upper boundary.
            lower:     Lower boundary.
            mode:      ABS, REL, or PERC.
            reference: Reference value (REL/PERC modes).
            percent:   Percentage (PERC mode).
            enable:    Whether to activate this BIN after configuring.
        """
        if not 1 <= bin_num <= 10:
            raise ValueError(f"BIN number must be 1–10, got {bin_num}.")
        mode = mode.upper()
        self.write(f":CALCULATE:BIN:MODE {mode},{bin_num}")
        self.write(f":CALCULATE:BIN:UPPER {bin_num},{upper}")
        self.write(f":CALCULATE:BIN:LOWER {bin_num},{lower}")
        if reference is not None:
            self.write(f":CALCULATE:BIN:REFERENCE {bin_num},{reference}")
        if percent is not None:
            self.write(f":CALCULATE:BIN:PERCENT {bin_num},{percent}")
        self.write(f":CALCULATE:BIN:ENABLE {bin_num},{'ON' if enable else 'OFF'}")
        time.sleep(self._CMD_DELAY_S)

    def enable_bin(self, enable: bool) -> None:
        """Enable or disable the BIN sorting function."""
        self.write(f":CALCULATE:BIN:STATE {'ON' if enable else 'OFF'}")

    def get_bin_result(self) -> str:
        """Return the BIN sort result: 'BIN1'–'BIN10' or 'OUT'."""
        return self.query(":CALCULATE:BIN:RESULT?").strip()

    # ── Scaling ───────────────────────────────────────────────────────────

    def configure_scaling(
        self,
        factor_a: float = 1.0,
        offset_b: float = 0.0,
        unit: str = "",
    ) -> None:
        """
        Configure linear scaling: display = A × reading + B.

        Args:
            factor_a: Multiplier A.
            offset_b: Offset B.
            unit:     Display unit label (max 8 characters).
        """
        self.write(f":CALCULATE:SCALING:PARAMETERA {factor_a}")
        self.write(f":CALCULATE:SCALING:PARAMETERB {offset_b}")
        if unit:
            self.write(f':CALCULATE:SCALING:UNIT "{unit[:8]}"')
        self.write(":CALCULATE:SCALING:STATE ON")
        time.sleep(self._CMD_DELAY_S)

    def disable_scaling(self) -> None:
        """Disable scaling."""
        self.write(":CALCULATE:SCALING:STATE OFF")

    # ── Hold ─────────────────────────────────────────────────────────────

    def set_auto_hold(self, enable: bool) -> None:
        """Enable auto-hold (display freezes when reading stabilises)."""
        self.write(f":SENSE:HOLD:AUTO {'ON' if enable else 'OFF'}")

    def release_hold(self) -> None:
        """Release a held display."""
        self.write(":SENSE:HOLD:OFF")

    # ── Memory (RM3545 only) ──────────────────────────────────────────────

    def enable_memory(self, enable: bool) -> None:
        """Enable or disable internal memory recording."""
        self.write(f":MEMORY:STATE {'ON' if enable else 'OFF'}")

    def clear_memory(self) -> None:
        """Erase all readings stored in internal memory."""
        self.write(":MEMORY:CLEAR")

    def get_memory_count(self) -> int:
        """Return the number of readings currently stored in memory."""
        return int(self.query(":MEMORY:COUNT?").strip())

    def get_memory_data(self) -> list:
        """
        Retrieve all stored readings from internal memory.

        Returns a list of float / 'OL' / 'UL' / 'ERROR' values.
        """
        raw = self.query(":MEMORY:DATA?")
        return [self._parse(v) for v in raw.split(",")]

    # ── Scanning / multiplexer (RM3545 only) ──────────────────────────────

    def configure_scan(
        self,
        mode: str = "AUTO",
        channels: Optional[list] = None,
    ) -> None:
        """
        Configure the scan function.

        Args:
            mode:     AUTO (scan all enabled channels continuously) or
                      STEP (advance one channel per trigger event).
            channels: List of channel numbers to enable.  None = no change.
        """
        self.write(f":SENSE:SCAN:MODE {mode.upper()}")
        if channels is not None:
            for ch in channels:
                self.write(f":SENSE:CH:STATE ON,{ch}")
        time.sleep(self._CMD_DELAY_S)

    def enable_scan(self, enable: bool) -> None:
        """Enable or disable the scan function."""
        self.write(f":SENSE:SCAN:STATE {'ON' if enable else 'OFF'}")

    def reset_scan(self) -> None:
        """Reset the scan pointer back to channel 1."""
        self.write(":SENSE:SCAN:RESET")

    def get_scan_data(self) -> list:
        """
        Return all channel measurements from the last completed scan.

        Returns a list of float / 'OL' / 'UL' / 'ERROR' values.
        """
        raw = self.query(":SENSE:SCAN:DATA?")
        return [self._parse(v) for v in raw.split(",")]

    def select_channel(self, channel: int) -> None:
        """Set the active measurement channel."""
        self.write(f":SENSE:CH {channel}")

    # ── System ────────────────────────────────────────────────────────────

    def set_line_frequency(self, freq: int) -> None:
        """
        Set the mains line frequency for noise rejection.

        Args:
            freq: 50 or 60.  Must match your local supply frequency.
        """
        if freq not in self._VALID_FREQS:
            raise ValueError(f"Line frequency must be 50 or 60, got {freq}.")
        self.write(f":SYSTEM:LFREQUENCY {freq}")
        time.sleep(self._CMD_DELAY_S)

    def local(self) -> None:
        """Return front-panel to local (manual) control."""
        self.write(":SYSTEM:LOCAL")
        logger.info("Hioki [%s] returned to local mode", self.resource_name)

    def lock_keys(self, lock: bool) -> None:
        """Lock or unlock the front-panel key input."""
        self.write(f":SYSTEM:KLOCK {'ON' if lock else 'OFF'}")

    def save_panel(self, slot: int) -> None:
        """Save current settings to panel memory slot 1–5."""
        if not 1 <= slot <= 5:
            raise ValueError(f"Panel slot must be 1–5, got {slot}.")
        self.write(f":SYSTEM:PANEL:SAVE {slot}")

    def load_panel(self, slot: int) -> None:
        """Recall settings from panel memory slot 1–5."""
        if not 1 <= slot <= 5:
            raise ValueError(f"Panel slot must be 1–5, got {slot}.")
        self.write(f":SYSTEM:PANEL:LOAD {slot}")

    def calibrate(self) -> None:
        """Execute self-calibration.  Takes approximately 1 second."""
        self.write(":SYSTEM:CALIBRATION")
        time.sleep(1.0)

    def reset(self) -> None:
        """
        Factory reset (*RST) then restore header-off.

        Communication settings (baud rate, termination) are NOT reset.
        Waits 1 second for the instrument to finish resetting.
        """
        self.write("*RST")
        time.sleep(self._RESET_DELAY_S)
        self.write(":SYSTEM:HEADER OFF")
        logger.info("Hioki [%s] reset complete", self.resource_name)

    def clear_status(self) -> None:
        """Clear all status registers (*CLS)."""
        self.write("*CLS")

    # ── Status and error checking ─────────────────────────────────────────

    def check_errors(self) -> None:
        """
        Query the status registers and raise InstrumentError if any error
        bits are set.

        Checks:
          *ESR? — IEEE 488.2 standard event register (CME, EXE, DDE, QYE)
          *STB? — Status byte (ESB summary bit)
          :ESR0? — Hioki device-specific event register 0
          :ESR1? — Hioki device-specific event register 1

        Call after a configuration sequence to verify all commands were
        accepted.
        """
        esr  = int(self.query("*ESR?").strip())
        stb  = int(self.query("*STB?").strip())
        esr0 = int(self.query(":ESR0?").strip())
        esr1 = int(self.query(":ESR1?").strip())

        errors = []

        ieee_bits = {
            5: "Command error (CME) — unrecognised command header",
            4: "Execution error (EXE) — invalid parameter or out-of-range",
            3: "Device-specific error (DDE)",
            2: "Query error (QYE) — query interrupted or unterminated",
        }
        for bit, meaning in ieee_bits.items():
            if esr & (1 << bit):
                errors.append(meaning)

        if stb & (1 << 1):
            errors.append(f"Device event register 1 set (ESR1=0x{esr1:02X})")
        if stb & (1 << 0):
            errors.append(f"Device event register 0 set (ESR0=0x{esr0:02X})")

        if errors:
            raise InstrumentError(
                f"Hioki [{self.resource_name}] errors: {'; '.join(errors)}"
            )

    # ── Close override ────────────────────────────────────────────────────

    def close(self) -> None:
        """Return to local control before closing the serial port."""
        if self._resource is not None:
            try:
                self.local()
            except Exception:
                pass  # best-effort; never block close()
        super().close()
