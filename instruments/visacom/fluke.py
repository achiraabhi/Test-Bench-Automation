"""
fluke.py — Driver for the Fluke 8845A/8846A precision DMM.

Communication: RS-232 via USB-serial adapter (pyvisa-py ASRL resource).
Resource string example: ASRL/dev/ttyUSB0::INSTR

Key behavioral notes:
  - Must send SYST:REM before issuing any measurement commands.
  - Uses READ? (atomic arm+measure+fetch) rather than INIT/FETCH?.
  - Serial framing: 9600 8N1.
  - Line termination: CR+LF (\r\n).
"""

import logging
import time

import pyvisa.constants as visa_const

from .base import Instrument

logger = logging.getLogger(__name__)


class Fluke8845A(Instrument):
    """
    Fluke 8845A / 8846A DMM driver.

    The 8845A does not support the INIT/FETCH? split; every measurement
    must be retrieved with READ? which performs the full
    arm → settle → digitize → return cycle synchronously.
    """

    # Serial framing
    _BAUD_RATE: int = 9600
    _DATA_BITS: int = 8
    _STOP_BITS = visa_const.StopBits.one
    _PARITY = visa_const.Parity.none
    _FLOW_CONTROL = visa_const.ControlFlow.none

    # Termination
    _READ_TERM: str = "\r\n"
    _WRITE_TERM: str = "\r\n"

    #: Extra delay (s) after entering remote mode — the 8845A needs ~100 ms
    #: to finish processing SYST:REM before it will respond to queries.
    _REMOTE_SETTLE_S: float = 0.15

    def __init__(
        self,
        resource_name: str,
        timeout_ms: int = 10000,
        retries: int = Instrument.DEFAULT_RETRIES,
    ) -> None:
        # Longer default timeout: serial round-trips are slower than USB-TMC
        super().__init__(resource_name, timeout_ms=timeout_ms, retries=retries)

    # ------------------------------------------------------------------
    # Resource setup
    # ------------------------------------------------------------------

    def _configure_resource(self) -> None:
        r = self._resource

        # Serial framing
        r.baud_rate = self._BAUD_RATE
        r.data_bits = self._DATA_BITS
        r.stop_bits = self._STOP_BITS
        r.parity = self._PARITY
        r.flow_control = self._FLOW_CONTROL

        # Termination characters
        r.read_termination = self._READ_TERM
        r.write_termination = self._WRITE_TERM

        logger.debug(
            "Fluke serial configured: %d baud, 8N1, term=%r",
            self._BAUD_RATE,
            self._READ_TERM,
        )

        # Place the instrument in remote-control mode immediately.
        self._enter_remote()

    def _enter_remote(self) -> None:
        """Send SYST:REM and wait for the instrument to be ready."""
        self.write("SYST:REM")
        time.sleep(self._REMOTE_SETTLE_S)
        logger.info("Fluke [%s] entered remote mode", self.resource_name)

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
            voltage_range: '0.1', '1', '10', '100', '750', or 'AUTO'.
            resolution:    Digits of resolution ('DEF', '4.5', '5.5', '6.5').
        """
        cmd = f"CONF:VOLT:AC {voltage_range},{resolution}"
        self.write(cmd)
        logger.info(
            "Fluke [%s] configured for AC voltage (%s V, res=%s)",
            self.resource_name, voltage_range, resolution,
        )

    def configure_dc_voltage(
        self,
        voltage_range: str = "AUTO",
        resolution: str = "DEF",
    ) -> None:
        cmd = f"CONF:VOLT:DC {voltage_range},{resolution}"
        self.write(cmd)
        logger.info(
            "Fluke [%s] configured for DC voltage (%s V, res=%s)",
            self.resource_name, voltage_range, resolution,
        )

    def configure_resistance(
        self,
        resistance_range: str = "AUTO",
        resolution: str = "DEF",
    ) -> None:
        cmd = f"CONF:RES {resistance_range},{resolution}"
        self.write(cmd)
        logger.info(
            "Fluke [%s] configured for resistance (%s Ω)",
            self.resource_name, resistance_range,
        )

    # ------------------------------------------------------------------
    # Measurement acquisition
    # ------------------------------------------------------------------

    def read_ac_voltage(self) -> float:
        """
        Return the AC voltage reading (V RMS).

        READ? is the correct command for the 8845A: it arms the trigger,
        waits for the measurement to complete, and returns the reading in
        a single transaction.  Do NOT use INIT + FETCH? on this instrument.
        """
        raw = self.query_with_retry("READ?")
        value = float(raw)
        logger.debug("Fluke AC voltage: %.6f V", value)
        return value

    def read_dc_voltage(self) -> float:
        """Return the DC voltage reading (V)."""
        raw = self.query_with_retry("READ?")
        value = float(raw)
        logger.debug("Fluke DC voltage: %.6f V", value)
        return value

    def read_resistance(self) -> float:
        """Return the resistance reading (Ω)."""
        raw = self.query_with_retry("READ?")
        value = float(raw)
        logger.debug("Fluke resistance: %.6f Ω", value)
        return value

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def set_local(self) -> None:
        """Return the front-panel to local (manual) control."""
        self.write("SYST:LOC")
        logger.info("Fluke [%s] returned to local mode", self.resource_name)

    def close(self) -> None:
        """Return to local before closing the port."""
        if self._resource is not None:
            try:
                self.set_local()
            except Exception:
                pass  # best-effort; don't block the close
        super().close()
