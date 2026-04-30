"""
base.py — Abstract base class for all VISA-controlled instruments.

Wraps pyvisa.Resource and provides a uniform write/read/query/close
interface that every concrete instrument class builds on top of.
"""

import logging
import time
from abc import ABC, abstractmethod
from typing import Optional

import pyvisa

logger = logging.getLogger(__name__)


class InstrumentError(Exception):
    """Raised when an instrument operation fails after all retries."""


class Instrument(ABC):
    """
    Generic VISA instrument wrapper.

    Subclasses must implement ``_configure_resource`` to apply any
    interface-specific settings (termination chars, baud rate, etc.)
    before the connection is considered ready.
    """

    #: Default number of retries for transient communication failures.
    DEFAULT_RETRIES: int = 3
    #: Seconds to wait between retries.
    RETRY_DELAY: float = 0.5

    def __init__(
        self,
        resource_name: str,
        timeout_ms: int = 5000,
        retries: int = DEFAULT_RETRIES,
    ) -> None:
        self.resource_name = resource_name
        self.timeout_ms = timeout_ms
        self.retries = retries

        self._rm: pyvisa.ResourceManager = pyvisa.ResourceManager("@py")
        self._resource: Optional[pyvisa.resources.Resource] = None

        self._open()

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def _open(self) -> None:
        logger.debug("Opening resource: %s", self.resource_name)
        self._resource = self._rm.open_resource(self.resource_name)
        self._resource.timeout = self.timeout_ms
        self._configure_resource()
        logger.info("Connected: %s", self.resource_name)

    @abstractmethod
    def _configure_resource(self) -> None:
        """Apply interface-specific settings (terminations, serial params, etc.)."""

    def close(self) -> None:
        """Release the VISA resource."""
        if self._resource is not None:
            logger.info("Closing resource: %s", self.resource_name)
            self._resource.close()
            self._resource = None

    def __enter__(self) -> "Instrument":
        return self

    def __exit__(self, *_) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Core I/O
    # ------------------------------------------------------------------

    def write(self, command: str) -> None:
        """Send a SCPI command with no response expected."""
        logger.debug("WRITE [%s] → %r", self.resource_name, command)
        self._resource.write(command)

    def read(self) -> str:
        """Read the next response from the instrument."""
        response = self._resource.read().strip()
        logger.debug("READ  [%s] ← %r", self.resource_name, response)
        return response

    def query(self, command: str) -> str:
        """Send a command and return the response (write + read in one call)."""
        logger.debug("QUERY [%s] → %r", self.resource_name, command)
        response = self._resource.query(command).strip()
        logger.debug("QUERY [%s] ← %r", self.resource_name, response)
        return response

    # ------------------------------------------------------------------
    # Resilient helpers
    # ------------------------------------------------------------------

    def query_with_retry(self, command: str) -> str:
        """
        query() with automatic retry on VisaIOError.

        Useful for slow serial instruments that occasionally miss the
        first transaction after a mode change.
        """
        last_exc: Optional[Exception] = None
        for attempt in range(1, self.retries + 1):
            try:
                return self.query(command)
            except pyvisa.errors.VisaIOError as exc:
                last_exc = exc
                logger.warning(
                    "Query failed (attempt %d/%d) on %s: %s",
                    attempt,
                    self.retries,
                    self.resource_name,
                    exc,
                )
                time.sleep(self.RETRY_DELAY)
        raise InstrumentError(
            f"query '{command}' failed after {self.retries} attempts "
            f"on {self.resource_name}"
        ) from last_exc

    # ------------------------------------------------------------------
    # Identity / diagnostics
    # ------------------------------------------------------------------

    def identify(self) -> str:
        """Return the *IDN? response string."""
        return self.query("*IDN?")

    def reset(self) -> None:
        """Send IEEE 488.2 reset (*RST) and clear status (*CLS)."""
        self.write("*RST")
        self.write("*CLS")

    @property
    def is_open(self) -> bool:
        return self._resource is not None
