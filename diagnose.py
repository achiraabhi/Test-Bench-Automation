"""
diagnose.py — DMM connection and communication diagnostic tool.

Run this whenever an instrument is misbehaving.  It works through a
series of tests from lowest level (can we see the port?) to highest
level (can we take a reading?) and prints a clear PASS / FAIL / WARN
for each step, plus the raw SCPI error queue so you know exactly what
the instrument thinks went wrong.

Usage:
    python diagnose.py            # auto-discover and test all known instruments
    python diagnose.py --list     # just list all VISA resources and exit
"""

import argparse
import sys
import time
import logging
from typing import Optional

import pyvisa
import pyvisa.constants as visa_const

# Keep the library's own logger quiet during diagnostics so output is clean.
logging.basicConfig(level=logging.WARNING)

# ── colour helpers ──────────────────────────────────────────────────────────

_USE_COLOUR = sys.stdout.isatty()

def _green(s):  return f"\033[92m{s}\033[0m" if _USE_COLOUR else s
def _red(s):    return f"\033[91m{s}\033[0m" if _USE_COLOUR else s
def _yellow(s): return f"\033[93m{s}\033[0m" if _USE_COLOUR else s
def _bold(s):   return f"\033[1m{s}\033[0m"  if _USE_COLOUR else s

PASS = _green("PASS")
FAIL = _red("FAIL")
WARN = _yellow("WARN")

def _header(title: str) -> None:
    print(f"\n{_bold('=' * 60)}")
    print(f"  {_bold(title)}")
    print(_bold('=' * 60))

def _step(label: str, width: int = 42) -> None:
    print(f"  {label:<{width}}", end="", flush=True)

def _result(tag: str, detail: str = "") -> None:
    suffix = f"  ({detail})" if detail else ""
    print(f"{tag}{suffix}")

# ── SCPI error queue ─────────────────────────────────────────────────────────

def drain_error_queue(res: pyvisa.resources.Resource, term: str = "\n") -> list[str]:
    """
    Read all entries from the SCPI error queue via SYST:ERR?.

    Returns a list of raw error strings, e.g. ['+0,"No error"', '-113,"Undefined header"'].
    Stops when it sees error code 0 (no error) or after 20 reads to avoid infinite loops.
    """
    errors = []
    res.write_termination  = term
    res.read_termination   = term
    for _ in range(20):
        try:
            raw = res.query("SYST:ERR?").strip()
        except Exception:
            break
        errors.append(raw)
        code = raw.split(",")[0].strip()
        if code in ("0", "+0"):
            break
    return errors

def format_error_queue(errors: list[str]) -> str:
    no_error = lambda e: e.split(",")[0].strip() in ("0", "+0")
    real = [e for e in errors if not no_error(e)]
    if not real:
        return _green("queue empty")
    return _red("  |  ".join(real))

# ── per-instrument test suites ───────────────────────────────────────────────

def _test_keysight(res: pyvisa.resources.Resource, resource_name: str) -> int:
    """Run Keysight-specific tests. Returns number of failures."""
    failures = 0
    term = "\n"
    res.read_termination  = term
    res.write_termination = term

    # Clear instrument state
    try:
        res.write("*CLS")
        res.write("*RST")
        time.sleep(0.3)
    except Exception:
        pass

    # --- IDN -----------------------------------------------------------------
    _step("*IDN? response")
    try:
        idn = res.query("*IDN?").strip()
        _result(PASS, idn)
    except Exception as exc:
        _result(FAIL, str(exc)); failures += 1

    # --- Error queue after reset ---------------------------------------------
    _step("Error queue after *RST")
    errs = drain_error_queue(res, term)
    _result(PASS if all(e.split(",")[0].strip() in ("0","+0") for e in errs) else WARN,
            format_error_queue(errs))

    # --- Configure AC voltage ------------------------------------------------
    _step("CONF:VOLT:AC AUTO,DEF")
    try:
        res.write("CONF:VOLT:AC AUTO,DEF")
        time.sleep(0.1)
        errs = drain_error_queue(res, term)
        real_errs = [e for e in errs if e.split(",")[0].strip() not in ("0","+0")]
        if real_errs:
            _result(WARN, format_error_queue(errs)); failures += 1
        else:
            _result(PASS)
    except Exception as exc:
        _result(FAIL, str(exc)); failures += 1

    # --- INIT + FETCH? -------------------------------------------------------
    _step("INIT + FETCH? (single reading)")
    try:
        res.write("INIT")
        raw = res.query("FETCH?").strip()
        val = float(raw)
        _result(PASS, f"{val:.6f} V")
    except Exception as exc:
        _result(FAIL, str(exc)); failures += 1

    # --- Error queue after measurement ---------------------------------------
    _step("Error queue after measurement")
    errs = drain_error_queue(res, term)
    _result(PASS if all(e.split(",")[0].strip() in ("0","+0") for e in errs) else WARN,
            format_error_queue(errs))

    return failures


def _test_fluke(res: pyvisa.resources.Resource, resource_name: str) -> int:
    """Run Fluke 8845A-specific tests. Returns number of failures."""
    failures = 0
    term = "\r\n"

    # Apply serial framing
    try:
        res.baud_rate    = 9600
        res.data_bits    = 8
        res.stop_bits    = visa_const.StopBits.one
        res.parity       = visa_const.Parity.none
        res.flow_control = visa_const.ControlFlow.none
    except Exception as exc:
        print(f"  {_red('Could not set serial parameters')}: {exc}")
        failures += 1

    res.read_termination  = term
    res.write_termination = term

    # --- IDN -----------------------------------------------------------------
    _step("*IDN? response")
    try:
        idn = res.query("*IDN?").strip()
        _result(PASS, idn)
    except Exception as exc:
        _result(FAIL, str(exc)); failures += 1

    # --- SYST:REM ------------------------------------------------------------
    _step("SYST:REM (enter remote mode)")
    try:
        res.write("SYST:REM")
        time.sleep(0.2)
        _result(PASS)
    except Exception as exc:
        _result(FAIL, str(exc)); failures += 1

    # --- Error queue after remote --------------------------------------------
    _step("Error queue after SYST:REM")
    errs = drain_error_queue(res, term)
    _result(PASS if all(e.split(",")[0].strip() in ("0","+0") for e in errs) else WARN,
            format_error_queue(errs))

    # --- Configure AC voltage ------------------------------------------------
    _step("CONF:VOLT:AC AUTO,DEF")
    try:
        res.write("CONF:VOLT:AC AUTO,DEF")
        time.sleep(0.1)
        errs = drain_error_queue(res, term)
        real_errs = [e for e in errs if e.split(",")[0].strip() not in ("0","+0")]
        if real_errs:
            _result(WARN, format_error_queue(errs)); failures += 1
        else:
            _result(PASS)
    except Exception as exc:
        _result(FAIL, str(exc)); failures += 1

    # --- READ? ---------------------------------------------------------------
    _step("READ? (single reading)")
    try:
        raw = res.query("READ?").strip()
        val = float(raw)
        _result(PASS, f"{val:.6f} V")
    except Exception as exc:
        _result(FAIL, str(exc)); failures += 1

    # --- Error queue after measurement ---------------------------------------
    _step("Error queue after measurement")
    errs = drain_error_queue(res, term)
    _result(PASS if all(e.split(",")[0].strip() in ("0","+0") for e in errs) else WARN,
            format_error_queue(errs))

    # --- Return to local -----------------------------------------------------
    _step("SYST:LOC (return to local)")
    try:
        res.write("SYST:LOC")
        _result(PASS)
    except Exception as exc:
        _result(WARN, str(exc))

    return failures


# ── Yokogawa WT310 tests ─────────────────────────────────────────────────────

def _test_yokogawa(res: pyvisa.resources.Resource, resource_name: str) -> int:
    """Run Yokogawa WT310-specific tests. Returns number of failures."""
    failures = 0
    term = "\n"
    res.read_termination  = term
    res.write_termination = term

    # --- IDN -----------------------------------------------------------------
    _step("*IDN? response")
    try:
        idn = res.query("*IDN?").strip()
        _result(PASS, idn)
    except Exception as exc:
        _result(FAIL, str(exc)); failures += 1

    # --- Set ASCII numeric format ---------------------------------------------
    _step(":NUMERIC:FORMAT ASCII")
    try:
        res.write(":NUMERIC:FORMAT ASCII")
        time.sleep(0.1)
        _result(PASS)
    except Exception as exc:
        _result(FAIL, str(exc)); failures += 1

    # --- Configure 3 numeric items -------------------------------------------
    _step(":NUMERIC:NORMAL:NUMBER 3  (V, I, P)")
    try:
        res.write(":NUMERIC:NORMAL:NUMBER 3")
        res.write(":NUMERIC:NORMAL:ITEM1 U,1")
        res.write(":NUMERIC:NORMAL:ITEM2 I,1")
        res.write(":NUMERIC:NORMAL:ITEM3 P,1")
        time.sleep(0.1)
        errs = drain_error_queue(res, term)
        real_errs = [e for e in errs if e.split(",")[0].strip() not in ("0","+0")]
        if real_errs:
            _result(WARN, format_error_queue(errs)); failures += 1
        else:
            _result(PASS)
    except Exception as exc:
        _result(FAIL, str(exc)); failures += 1

    # --- Auto range ----------------------------------------------------------
    _step(":INPUT voltage + current AUTO range")
    try:
        res.write(":INPUT:ELEMENT1:VOLTAGE:AUTO ON")
        res.write(":INPUT:ELEMENT1:CURRENT:AUTO ON")
        _result(PASS)
    except Exception as exc:
        _result(WARN, str(exc))

    # --- Read values ---------------------------------------------------------
    _step(":NUMERIC:NORMAL:VALUE? (V, I, P)")
    try:
        raw = res.query(":NUMERIC:NORMAL:VALUE?").strip()
        tokens = [t.strip() for t in raw.split(",")]
        if len(tokens) >= 3:
            v, i, p = float(tokens[0]), float(tokens[1]), float(tokens[2])
            _result(PASS, f"{v:.3f} V  {i:.4f} A  {p:.3f} W")
        else:
            _result(WARN, f"only {len(tokens)} token(s) returned: {raw}")
    except Exception as exc:
        _result(FAIL, str(exc)); failures += 1

    # --- Error queue after measurement ---------------------------------------
    _step("Error queue after measurement")
    errs = drain_error_queue(res, term)
    _result(PASS if all(e.split(",")[0].strip() in ("0","+0") for e in errs) else WARN,
            format_error_queue(errs))

    return failures


# ── generic probe (for unrecognised instruments) ─────────────────────────────

def _test_generic(res: pyvisa.resources.Resource, resource_name: str) -> int:
    failures = 0
    _step("*IDN? response")
    try:
        for term in ("\n", "\r\n"):
            res.read_termination  = term
            res.write_termination = term
            try:
                idn = res.query("*IDN?").strip()
                if idn:
                    _result(PASS, idn)
                    return 0
            except Exception:
                continue
        _result(FAIL, "no response on \\n or \\r\\n"); failures += 1
    except Exception as exc:
        _result(FAIL, str(exc)); failures += 1
    return failures


# ── discovery + dispatch ─────────────────────────────────────────────────────

_INSTRUMENT_TESTS = {
    "keysight": _test_keysight,
    "fluke":    _test_fluke,
    "yokogawa": _test_yokogawa,
}

_SIGNATURES = [
    (["KEYSIGHT"],  "keysight"),
    (["AGILENT"],   "keysight"),
    (["FLUKE"],     "fluke"),
    (["YOKOGAWA"],  "yokogawa"),
]

def _identify(idn: str) -> str:
    upper = idn.upper()
    for keywords, label in _SIGNATURES:
        if all(k in upper for k in keywords):
            return label
    return "unknown"


def _probe_resource(rname: str, rm: pyvisa.ResourceManager) -> Optional[tuple]:
    """
    Attempt to open a resource and get its IDN.
    Returns (resource_object, idn_string) or None on failure.
    """
    rname_upper = rname.upper()
    timeout = 3000

    try:
        res = rm.open_resource(rname)
        res.timeout = timeout

        if "ASRL" in rname_upper or "COM" in rname_upper:
            res.baud_rate    = 9600
            res.data_bits    = 8
            res.stop_bits    = visa_const.StopBits.one
            res.parity       = visa_const.Parity.none
            res.flow_control = visa_const.ControlFlow.none
            time.sleep(0.1)

        for term in ("\n", "\r\n"):
            res.read_termination  = term
            res.write_termination = term
            try:
                idn = res.query("*IDN?").strip()
                if idn:
                    return res, idn
            except Exception:
                continue

        res.close()
        return None

    except Exception:
        return None


def run_diagnostics(target_resources: Optional[list] = None) -> None:
    rm = pyvisa.ResourceManager("@py")

    # ── 1. List all resources ─────────────────────────────────────────────
    _header("VISA Resource Scan")
    try:
        all_resources = rm.list_resources()
    except Exception as exc:
        print(f"  {_red('Could not list VISA resources')}: {exc}")
        return

    if not all_resources:
        print(f"  {_yellow('No VISA resources found.')}")
        print("  Check: USB cable, USB-serial adapter driver, user is in 'dialout' group (Linux).")
        return

    for r in all_resources:
        print(f"  {r}")

    resources_to_test = target_resources if target_resources else list(all_resources)

    # ── 2. Test each resource ─────────────────────────────────────────────
    total_failures = 0
    tested = 0

    for rname in resources_to_test:
        rname_upper = rname.upper()

        # Skip resource types we can't meaningfully test
        if not any(t in rname_upper for t in ("USB", "ASRL", "COM", "GPIB")):
            continue

        _header(f"Testing: {rname}")

        probe = _probe_resource(rname, rm)
        if probe is None:
            _step("Open resource + *IDN?")
            _result(FAIL, "could not open or no IDN response")
            total_failures += 1
            continue

        res, idn = probe
        label = _identify(idn)
        print(f"  Identified as : {_bold(label.upper())}  ({idn})")

        res.timeout = 10_000   # give real tests more time

        test_fn = _INSTRUMENT_TESTS.get(label, _test_generic)
        failures = test_fn(res, rname)
        total_failures += failures
        tested += 1

        try:
            res.close()
        except Exception:
            pass

    # ── 3. Summary ────────────────────────────────────────────────────────
    _header("Summary")
    if tested == 0:
        print(f"  {_yellow('No testable instruments found.')}")
    elif total_failures == 0:
        print(f"  {_green(f'All tests passed across {tested} instrument(s).')}")
    else:
        print(f"  {_red(f'{total_failures} failure(s) across {tested} instrument(s).')}")
        print()
        print("  Common causes:")
        print("    FAIL on open resource  → wrong resource string, device not powered on")
        print("    FAIL on *IDN?          → wrong baud rate / termination, cable fault")
        print("    FAIL on CONF:VOLT:AC   → command syntax not supported by this model")
        print("    FAIL on INIT/FETCH?    → use READ? instead, or check trigger state")
        print("    FAIL on READ?          → SYST:REM not sent, or measurement timeout")
        print("    WARN on error queue    → check the SCPI error code in the output above")
        print()
        print("  Full debug log: visacom_debug.log")

    rm.close()


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Diagnose VISA instrument communication errors."
    )
    parser.add_argument(
        "--list", action="store_true",
        help="List all available VISA resources and exit."
    )
    parser.add_argument(
        "resources", nargs="*",
        help="Optional: specific VISA resource string(s) to test. "
             "If omitted, all discovered resources are tested."
    )
    args = parser.parse_args()

    if args.list:
        rm = pyvisa.ResourceManager("@py")
        _header("Available VISA Resources")
        try:
            resources = rm.list_resources()
            if resources:
                for r in resources:
                    print(f"  {r}")
            else:
                print(f"  {_yellow('None found.')}")
        except Exception as exc:
            print(f"  {_red('Error')}: {exc}")
        rm.close()
        return

    run_diagnostics(args.resources if args.resources else None)


if __name__ == "__main__":
    main()
