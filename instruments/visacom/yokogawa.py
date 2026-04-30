"""
yokogawa.py — Driver for the Yokogawa WT310 / WT310E power meter.

Communication: USB-TMC via PyVISA (primary).
Resource string example: USB0::0x0B21::0x0025::SERIAL::INSTR

The WT310 uses a numeric item system: you assign measurement functions
(voltage, current, power, etc.) to numbered slots, then read all slots
in one query.  This driver pre-configures 7 slots covering the most
common power-quality measurements and returns them as a named dict.
"""

import logging
from dataclasses import dataclass
from typing import Optional

from .base import Instrument

logger = logging.getLogger(__name__)

# The WT310 returns this sentinel when a reading is invalid or out of range
_INVALID = 9.91e37


@dataclass
class PowerReading:
    """One complete measurement snapshot from the WT310."""
    voltage_V:      Optional[float]   # RMS voltage (V)
    current_A:      Optional[float]   # RMS current (A)
    power_W:        Optional[float]   # Active power (W)
    apparent_VA:    Optional[float]   # Apparent power (VA)
    reactive_var:   Optional[float]   # Reactive power (var)
    power_factor:   Optional[float]   # Power factor (dimensionless, 0–1)
    frequency_Hz:   Optional[float]   # Supply frequency (Hz)

    def __str__(self) -> str:
        def fmt(v, unit, width=10, decimals=3):
            if v is None:
                return f"{'---':>{width}}"
            return f"{v:>{width}.{decimals}f} {unit}"

        return (
            f"{fmt(self.voltage_V,    'V  ')}  "
            f"{fmt(self.current_A,    'A  ')}  "
            f"{fmt(self.power_W,      'W  ')}  "
            f"{fmt(self.apparent_VA,  'VA ')}  "
            f"{fmt(self.reactive_var, 'var')}  "
            f"PF={fmt(self.power_factor, '', 6, 4).strip()}  "
            f"{fmt(self.frequency_Hz, 'Hz ', 7, 2)}"
        )


# ── Numeric item slot assignments ────────────────────────────────────────────
# Slot index (1-based) → (SCPI function name, element number)
_ITEMS = {
    1: ("U",      1),   # RMS Voltage
    2: ("I",      1),   # RMS Current
    3: ("P",      1),   # Active Power
    4: ("S",      1),   # Apparent Power
    5: ("Q",      1),   # Reactive Power
    6: ("LAMBDA", 1),   # Power Factor
    7: ("FU",     1),   # Frequency (of voltage)
}


class YokogawaWT310(Instrument):
    """
    Yokogawa WT310 / WT310E single-phase power meter driver.

    All seven standard power-quality quantities (V, I, W, VA, var, PF, Hz)
    are configured on initialisation and read atomically with a single
    :NUMERIC:NORMAL:VALUE? query, minimising measurement skew.
    """

    _TERM_CHAR = "\n"

    def __init__(
        self,
        resource_name: str,
        timeout_ms: int = 10_000,
        retries: int = Instrument.DEFAULT_RETRIES,
    ) -> None:
        super().__init__(resource_name, timeout_ms=timeout_ms, retries=retries)

    # ------------------------------------------------------------------
    # Resource setup
    # ------------------------------------------------------------------

    def _configure_resource(self) -> None:
        self._resource.read_termination  = self._TERM_CHAR
        self._resource.write_termination = self._TERM_CHAR
        logger.debug("Yokogawa WT310 termination set to %r", self._TERM_CHAR)

        # Ensure the numeric output format is ASCII so we can parse it.
        self.write(":NUMERIC:FORMAT ASCII")
        self._configure_numeric_items()

    def _configure_numeric_items(self) -> None:
        """Assign the 7 measurement functions to the numeric output slots."""
        self.write(f":NUMERIC:NORMAL:NUMBER {len(_ITEMS)}")
        for slot, (func, element) in _ITEMS.items():
            self.write(f":NUMERIC:NORMAL:ITEM{slot} {func},{element}")
        logger.debug("Yokogawa numeric items configured: %s", _ITEMS)

    # ------------------------------------------------------------------
    # Range configuration
    # ------------------------------------------------------------------

    def configure_voltage_range(self, voltage_range: float = 0) -> None:
        """
        Set the voltage input range.

        Args:
            voltage_range: Range in volts — one of 15, 30, 75, 150, 300, 600.
                           Pass 0 to enable auto-range (default).
        """
        if voltage_range == 0:
            self.write(":INPUT:ELEMENT1:VOLTAGE:AUTO ON")
            logger.info("Yokogawa voltage range: AUTO")
        else:
            self.write(":INPUT:ELEMENT1:VOLTAGE:AUTO OFF")
            self.write(f":INPUT:ELEMENT1:VOLTAGE:RANGE {voltage_range}")
            logger.info("Yokogawa voltage range: %s V", voltage_range)

    def configure_current_range(self, current_range: float = 0) -> None:
        """
        Set the current input range.

        Args:
            current_range: Range in amps — one of 0.5, 1, 2, 5, 10, 20.
                           Pass 0 to enable auto-range (default).
        """
        if current_range == 0:
            self.write(":INPUT:ELEMENT1:CURRENT:AUTO ON")
            logger.info("Yokogawa current range: AUTO")
        else:
            self.write(":INPUT:ELEMENT1:CURRENT:AUTO OFF")
            self.write(f":INPUT:ELEMENT1:CURRENT:RANGE {current_range}")
            logger.info("Yokogawa current range: %s A", current_range)

    def configure_auto_range(self) -> None:
        """Enable auto-range on both voltage and current inputs."""
        self.configure_voltage_range(0)
        self.configure_current_range(0)

    # ------------------------------------------------------------------
    # Measurement
    # ------------------------------------------------------------------

    @staticmethod
    def _parse(raw: str) -> Optional[float]:
        """Convert one token to float, returning None for invalid readings."""
        try:
            v = float(raw)
            return None if v >= _INVALID else v
        except ValueError:
            return None

    def read_power(self) -> PowerReading:
        """
        Return a PowerReading with all seven measurements taken atomically.

        A value of None means the instrument reported that quantity as
        invalid or out of range (e.g. no load connected, over-range).
        """
        raw = self.query_with_retry(":NUMERIC:NORMAL:VALUE?")
        tokens = [t.strip() for t in raw.split(",")]

        if len(tokens) < len(_ITEMS):
            logger.warning(
                "Yokogawa returned %d tokens, expected %d — raw: %r",
                len(tokens), len(_ITEMS), raw,
            )
            tokens += ["9.91E+37"] * (len(_ITEMS) - len(tokens))

        reading = PowerReading(
            voltage_V    = self._parse(tokens[0]),
            current_A    = self._parse(tokens[1]),
            power_W      = self._parse(tokens[2]),
            apparent_VA  = self._parse(tokens[3]),
            reactive_var = self._parse(tokens[4]),
            power_factor = self._parse(tokens[5]),
            frequency_Hz = self._parse(tokens[6]),
        )
        logger.debug("Yokogawa reading: %s", reading)
        return reading

    # Convenience single-quantity reads (each calls read_power internally)
    def read_voltage(self)      -> Optional[float]: return self.read_power().voltage_V
    def read_current(self)      -> Optional[float]: return self.read_power().current_A
    def read_active_power(self) -> Optional[float]: return self.read_power().power_W
    def read_power_factor(self) -> Optional[float]: return self.read_power().power_factor
    def read_frequency(self)    -> Optional[float]: return self.read_power().frequency_Hz
