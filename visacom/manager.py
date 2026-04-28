"""
manager.py — InstrumentManager: lifecycle and batch operations for multiple instruments.

Manages a named registry of Instrument instances and provides collective
configure / read / close operations.  Designed to be extended: add new
instrument types simply by registering them with add_instrument().
"""

import csv
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .base import Instrument, InstrumentError

logger = logging.getLogger(__name__)


class InstrumentManager:
    """
    Registry and orchestrator for multiple VISA instruments.

    Usage::

        manager = InstrumentManager()
        manager.add_instrument("keysight", KeysightDMM("USB0::...::INSTR"))
        manager.add_instrument("fluke",    Fluke8845A("ASRL/dev/ttyUSB0::INSTR"))

        manager.configure_all(
            keysight=lambda inst: inst.configure_ac_voltage(),
            fluke=lambda inst:    inst.configure_ac_voltage(),
        )

        for reading in manager.read_loop(interval_s=1.0, count=10):
            print(reading)

        manager.close_all()
    """

    def __init__(self, log_dir: Optional[Path] = None) -> None:
        self._instruments: Dict[str, Instrument] = {}
        self._log_dir: Optional[Path] = log_dir
        self._csv_writer: Optional[csv.DictWriter] = None
        self._csv_file = None

        if log_dir is not None:
            self._setup_csv_logger(log_dir)

    # ------------------------------------------------------------------
    # Registry management
    # ------------------------------------------------------------------

    def add_instrument(self, name: str, instrument: Instrument) -> None:
        """Register an already-connected Instrument under a logical name."""
        if name in self._instruments:
            raise ValueError(f"An instrument named '{name}' is already registered.")
        self._instruments[name] = instrument
        logger.info("Registered instrument '%s' (%s)", name, instrument.resource_name)

    def remove_instrument(self, name: str, close: bool = True) -> None:
        """Unregister and optionally close an instrument."""
        inst = self._instruments.pop(name, None)
        if inst is None:
            raise KeyError(f"No instrument named '{name}'.")
        if close:
            inst.close()
        logger.info("Removed instrument '%s'", name)

    def get(self, name: str) -> Instrument:
        """Return the instrument registered under *name*."""
        try:
            return self._instruments[name]
        except KeyError:
            raise KeyError(f"No instrument named '{name}'.") from None

    @property
    def names(self):
        return list(self._instruments.keys())

    # ------------------------------------------------------------------
    # Batch operations
    # ------------------------------------------------------------------

    def configure_all(self, **configurators: Callable[[Instrument], None]) -> None:
        """
        Run a per-instrument configuration callable.

        Keyword argument names must match registered instrument names.

        Example::

            manager.configure_all(
                keysight=lambda inst: inst.configure_ac_voltage("AUTO"),
                fluke=lambda inst:    inst.configure_ac_voltage("AUTO"),
            )
        """
        for name, fn in configurators.items():
            inst = self.get(name)
            logger.info("Configuring '%s'...", name)
            fn(inst)

    def identify_all(self) -> Dict[str, str]:
        """Send *IDN? to every registered instrument and return the responses."""
        return {name: inst.identify() for name, inst in self._instruments.items()}

    def close_all(self) -> None:
        """Close all instruments and release VISA resources."""
        for name, inst in list(self._instruments.items()):
            try:
                inst.close()
                logger.info("Closed '%s'", name)
            except Exception as exc:
                logger.warning("Error closing '%s': %s", name, exc)
        self._instruments.clear()
        self._close_csv_logger()

    # ------------------------------------------------------------------
    # Measurement loop
    # ------------------------------------------------------------------

    def read_loop(
        self,
        readers: Dict[str, Callable[[Instrument], Any]],
        interval_s: float = 1.0,
        count: Optional[int] = None,
    ):
        """
        Generator that yields timestamped reading dicts at *interval_s* intervals.

        Args:
            readers:    {instrument_name: callable} — same signature as read_all().
            interval_s: Seconds between each reading cycle.
            count:      Stop after this many iterations; None means run forever.

        Yields:
            dict with keys: 'timestamp', plus one key per instrument name.

        Example::

            for row in manager.read_loop(
                readers=dict(
                    keysight=lambda i: i.read_ac_voltage(),
                    fluke=lambda i:    i.read_ac_voltage(),
                ),
                interval_s=2.0,
                count=30,
            ):
                print(row)
        """
        iteration = 0
        while count is None or iteration < count:
            ts = datetime.utcnow().isoformat(timespec="milliseconds") + "Z"
            readings = self.read_all(**readers)
            row = {"timestamp": ts, **readings}

            if self._csv_writer is not None:
                self._csv_writer.writerow(row)
                self._csv_file.flush()

            yield row

            iteration += 1
            if count is None or iteration < count:
                time.sleep(interval_s)

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------

    def __enter__(self) -> "InstrumentManager":
        return self

    def __exit__(self, *_) -> None:
        self.close_all()

    # ------------------------------------------------------------------
    # CSV logging
    # ------------------------------------------------------------------

    def _setup_csv_logger(self, log_dir: Path) -> None:
        log_dir.mkdir(parents=True, exist_ok=True)
        fname = log_dir / f"visacom_{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}.csv"
        logger.info("CSV log: %s", fname)
        # Columns are written lazily on the first write_row call so that we
        # know all instrument names at that point.
        self._pending_csv_path = fname

    def _ensure_csv_ready(self, fieldnames: list) -> None:
        if self._csv_writer is not None:
            return
        path = self._pending_csv_path
        self._csv_file = open(path, "w", newline="", encoding="utf-8")
        self._csv_writer = csv.DictWriter(
            self._csv_file,
            fieldnames=["timestamp"] + fieldnames,
            extrasaction="ignore",
        )
        self._csv_writer.writeheader()

    def read_all(self, **readers: Callable[[Instrument], Any]) -> Dict[str, Any]:
        results: Dict[str, Any] = {}
        for name, fn in readers.items():
            inst = self.get(name)
            try:
                results[name] = fn(inst)
            except (InstrumentError, Exception) as exc:
                logger.error("Read failed for '%s': %s", name, exc)
                results[name] = None

        # Lazy CSV initialisation — now we know the column names.
        if self._log_dir is not None and self._csv_writer is None:
            self._ensure_csv_ready(list(results.keys()))

        return results

    def _close_csv_logger(self) -> None:
        if self._csv_file is not None:
            self._csv_file.close()
            self._csv_file = None
            self._csv_writer = None
