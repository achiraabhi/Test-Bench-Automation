"""
discover.py — Automatic VISA instrument discovery.

Scans all available VISA resources, probes each one with *IDN?, and
matches the response against a registry of known instrument signatures.

USB resources are probed with a \n termination.
Serial (ASRL) resources are probed with 9600 8N1 and \r\n termination,
which covers the Fluke 8845A and most other RS-232 lab instruments.
"""

import logging
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

import pyvisa
import pyvisa.constants as visa_const

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Instrument signature registry
# ---------------------------------------------------------------------------
# Each entry is (keywords_in_idn, instrument_label).
# Keywords are matched case-insensitively against the *IDN? response.
# Add new entries here to support additional instruments.

_SIGNATURES: List[tuple] = [
    # Keysight / Agilent DMMs
    (["KEYSIGHT", "34461"], "keysight"),
    (["KEYSIGHT", "34465"], "keysight"),
    (["KEYSIGHT", "34470"], "keysight"),
    (["AGILENT",  "3446"],  "keysight"),
    # Fluke DMMs
    (["FLUKE", "8845"],     "fluke"),
    (["FLUKE", "8846"],     "fluke"),
    # Yokogawa power meters
    (["YOKOGAWA", "WT310"], "yokogawa"),
    (["YOKOGAWA", "WT310E"],"yokogawa"),
    # Hioki resistance meters
    (["HIOKI", "RM3544"],   "hioki"),
    (["HIOKI", "RM3545"],   "hioki"),
]

_DISCOVERY_TIMEOUT_MS = 2000   # short timeout so hung ports don't stall the scan
_SERIAL_SETTLE_S      = 0.10   # wait after opening a serial port before querying


@dataclass
class DiscoveredInstrument:
    label: str          # matched label from _SIGNATURES, e.g. "keysight"
    resource_name: str  # full VISA resource string
    idn: str            # raw *IDN? response


def _probe_usb(resource_name: str, rm: pyvisa.ResourceManager) -> Optional[str]:
    """Open a USB resource, query *IDN?, return the response or None."""
    try:
        res = rm.open_resource(resource_name)
        res.timeout = _DISCOVERY_TIMEOUT_MS
        res.read_termination  = "\n"
        res.write_termination = "\n"
        idn = res.query("*IDN?").strip()
        res.close()
        return idn
    except Exception as exc:
        logger.debug("USB probe failed [%s]: %s", resource_name, exc)
        return None


def _probe_serial(resource_name: str, rm: pyvisa.ResourceManager) -> Optional[str]:
    """Open a serial resource with 9600 8N1, query *IDN?, return the response or None."""
    try:
        res = rm.open_resource(resource_name)
        res.timeout      = _DISCOVERY_TIMEOUT_MS
        res.baud_rate    = 9600
        res.data_bits    = 8
        res.stop_bits    = visa_const.StopBits.one
        res.parity       = visa_const.Parity.none
        res.flow_control = visa_const.ControlFlow.none
        res.read_termination  = "\r\n"
        res.write_termination = "\r\n"
        time.sleep(_SERIAL_SETTLE_S)
        idn = res.query("*IDN?").strip()
        res.close()
        return idn
    except Exception as exc:
        logger.debug("Serial probe failed [%s]: %s", resource_name, exc)
        return None


def _match_label(idn: str) -> Optional[str]:
    """Return the first matching label for an IDN string, or None."""
    idn_upper = idn.upper()
    for keywords, label in _SIGNATURES:
        if all(kw.upper() in idn_upper for kw in keywords):
            return label
    return None


def discover(visa_backend: str = "@py") -> Dict[str, DiscoveredInstrument]:
    """
    Scan all available VISA resources and return the ones we recognise.

    Returns:
        dict mapping label (e.g. "keysight", "fluke") to DiscoveredInstrument.
        If two devices share the same label the second one is stored under
        "<label>_2", "<label>_3", etc.
    """
    rm = pyvisa.ResourceManager(visa_backend)
    try:
        all_resources = rm.list_resources()
    except Exception as exc:
        logger.error("Could not list VISA resources: %s", exc)
        rm.close()
        return {}

    logger.info("Scanning %d VISA resource(s)...", len(all_resources))

    found: Dict[str, DiscoveredInstrument] = {}

    for rname in all_resources:
        rname_upper = rname.upper()

        if "USB" in rname_upper:
            idn = _probe_usb(rname, rm)
        elif "ASRL" in rname_upper or "COM" in rname_upper:
            idn = _probe_serial(rname, rm)
        else:
            logger.debug("Skipping unsupported resource type: %s", rname)
            continue

        if not idn:
            continue

        label = _match_label(idn)
        if label is None:
            logger.debug("Unrecognised instrument [%s]: %s", rname, idn)
            continue

        # Deduplicate if two devices share the same model label
        unique_label = label
        n = 2
        while unique_label in found:
            unique_label = f"{label}_{n}"
            n += 1

        found[unique_label] = DiscoveredInstrument(
            label=label,
            resource_name=rname,
            idn=idn,
        )
        logger.info("Found %-12s  [%s]  %s", unique_label, rname, idn)

    rm.close()

    if not found:
        logger.warning("No recognised instruments found.")

    return found
