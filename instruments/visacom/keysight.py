"""
keysight.py — Driver for Keysight USB DMMs (e.g. 34461A, 34465A, 34470A).

Communication: USB-TMC via PyVISA.
Resource string example: USB0::0x2A8D::0x0301::MY_SERIAL::INSTR
"""

import logging

from .base import Instrument

logger = logging.getLogger(__name__)


class KeysightDMM(Instrument):
    """
    Keysight DMM driver.

    The Keysight 344xxA series uses a triggered measurement workflow:
        INIT  — arm the trigger, start the measurement
        FETCH? — retrieve the last completed reading (non-destructive)

    This avoids the overhead of READ? which does INIT + FETCH? in one
    round-trip but blocks until the measurement completes.  Using them
    separately lets the host perform other work while the ADC settles.
    """

    _TERM_CHAR = "\n"

    def __init__(
        self,
        resource_name: str,
        timeout_ms: int = 5000,
        retries: int = Instrument.DEFAULT_RETRIES,
    ) -> None:
        super().__init__(resource_name, timeout_ms=timeout_ms, retries=retries)

    # ------------------------------------------------------------------
    # Resource setup
    # ------------------------------------------------------------------

    def _configure_resource(self) -> None:
        self._resource.read_termination = self._TERM_CHAR
        self._resource.write_termination = self._TERM_CHAR
        logger.debug("Keysight termination set to %r", self._TERM_CHAR)

    # ------------------------------------------------------------------
    # Measurement configuration
    # ------------------------------------------------------------------

    def configure_ac_voltage(
        self,
        voltage_range: str = "AUTO",
        resolution: str = "DEF",
    ) -> None:
        """
        Configure the DMM for AC voltage measurement.

        Args:
            voltage_range: Measurement range, e.g. '1', '10', '100', '750', or 'AUTO'.
            resolution:    Integration aperture, e.g. '0.001' (1 mV) or 'DEF' for default.
        """
        cmd = f"CONF:VOLT:AC {voltage_range},{resolution}"
        self.write(cmd)
        logger.info("Keysight [%s] configured for AC voltage (%s V, res=%s)",
                    self.resource_name, voltage_range, resolution)

    def configure_dc_voltage(
        self,
        voltage_range: str = "AUTO",
        resolution: str = "DEF",
    ) -> None:
        cmd = f"CONF:VOLT:DC {voltage_range},{resolution}"
        self.write(cmd)
        logger.info("Keysight [%s] configured for DC voltage (%s V, res=%s)",
                    self.resource_name, voltage_range, resolution)

    def configure_resistance(
        self,
        resistance_range: str = "AUTO",
        resolution: str = "DEF",
    ) -> None:
        cmd = f"CONF:RES {resistance_range},{resolution}"
        self.write(cmd)
        logger.info("Keysight [%s] configured for resistance (%s Ω)",
                    self.resource_name, resistance_range)

    # ------------------------------------------------------------------
    # Measurement acquisition
    # ------------------------------------------------------------------

    def _trigger_and_fetch(self) -> float:
        """
        Initiate a single triggered measurement and return the raw float value.

        INIT causes the instrument to wait for a trigger event (the internal
        immediate trigger by default), then make one measurement.  FETCH?
        blocks until the reading is ready and returns it.
        """
        self.write("INIT")
        raw = self.query_with_retry("FETCH?")
        return float(raw)

    def read_ac_voltage(self) -> float:
        """
        Return the AC voltage reading (V RMS) for the currently configured range.

        Prerequisite: call configure_ac_voltage() first.
        """
        value = self._trigger_and_fetch()
        logger.debug("Keysight AC voltage: %.6f V", value)
        return value

    def read_dc_voltage(self) -> float:
        """Return the DC voltage reading (V) for the currently configured range."""
        value = self._trigger_and_fetch()
        logger.debug("Keysight DC voltage: %.6f V", value)
        return value

    def read_resistance(self) -> float:
        """Return the resistance reading (Ω) for the currently configured range."""
        value = self._trigger_and_fetch()
        logger.debug("Keysight resistance: %.6f Ω", value)
        return value

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def set_nplc(self, nplc: float = 1.0) -> None:
        """
        Set integration time in power-line cycles (affects accuracy vs speed).

        Common values: 0.02 (fast), 0.2, 1 (default), 10, 100 (slow/accurate).
        """
        self.write(f"VOLT:NPLC {nplc}")
        logger.debug("Keysight NPLC set to %s", nplc)

    def set_auto_zero(self, enabled: bool = True) -> None:
        state = "ON" if enabled else "OFF"
        self.write(f"ZERO:AUTO {state}")
        logger.debug("Keysight auto-zero: %s", state)
