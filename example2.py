"""
example2.py — Read 10 measurements from whatever instruments are connected.

Works with any combination of:
    Keysight 344xxA DMM  → AC voltage
    Fluke 8845A          → AC voltage
    Yokogawa WT310       → V, I, W, VA, var, PF, Hz
    Hioki RM3545         → Resistance (Ω)

No CSV output — results are printed to the terminal only.

Run:
    python example2.py
"""

import logging
import sys
import time

from visacom import KeysightDMM, Fluke8845A, YokogawaWT310, HiokiRM3545
from visacom.discover import discover

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)

READINGS = 10


def _fmt(value, unit, width=12, decimals=6):
    if value is None or value in ("OL", "UL", "ERROR"):
        tag = value if value is not None else "---"
        return f"{tag:>{width}}      "
    return f"{value:>{width}.{decimals}f} {unit}"


def main() -> None:
    found = discover()

    if not found:
        print("No instruments found. Check connections and try again.")
        sys.exit(1)

    instruments = {}   # display name → instrument instance
    readers     = {}   # display name → callable → printable string

    # ── Keysight DMM ─────────────────────────────────────────────────────
    if "keysight" in found:
        try:
            inst = KeysightDMM(found["keysight"].resource_name, timeout_ms=5_000)
            inst.configure_ac_voltage()
            instruments["Keysight"] = inst
            readers["Keysight"] = lambda i: _fmt(i.read_ac_voltage(), "V AC")
            print(f"Connected : Keysight   [{found['keysight'].resource_name}]")
            print(f"           IDN: {found['keysight'].idn}")
        except Exception as exc:
            print(f"Keysight connect failed: {exc}")

    # ── Fluke 8845A ───────────────────────────────────────────────────────
    if "fluke" in found:
        try:
            inst = Fluke8845A(found["fluke"].resource_name, timeout_ms=10_000)
            inst.configure_ac_voltage()
            instruments["Fluke"] = inst
            readers["Fluke"] = lambda i: _fmt(i.read_ac_voltage(), "V AC")
            print(f"Connected : Fluke      [{found['fluke'].resource_name}]")
            print(f"           IDN: {found['fluke'].idn}")
        except Exception as exc:
            print(f"Fluke connect failed: {exc}")

    # ── Yokogawa WT310 ────────────────────────────────────────────────────
    if "yokogawa" in found:
        try:
            inst = YokogawaWT310(found["yokogawa"].resource_name, timeout_ms=10_000)
            inst.configure_auto_range()
            instruments["Yokogawa"] = inst
            print(f"Connected : Yokogawa   [{found['yokogawa'].resource_name}]")
            print(f"           IDN: {found['yokogawa'].idn}")
        except Exception as exc:
            print(f"Yokogawa connect failed: {exc}")

    # ── Hioki RM3545 ──────────────────────────────────────────────────────
    if "hioki" in found:
        try:
            inst = HiokiRM3545(found["hioki"].resource_name, timeout_ms=15_000)
            inst.initialize(line_freq=50, speed="MED", auto_range=True)
            inst.set_continuous(False)
            instruments["Hioki"] = inst
            readers["Hioki"] = lambda i: _fmt(i.read(), "Ω  ", 14, 6)
            print(f"Connected : Hioki      [{found['hioki'].resource_name}]")
            print(f"           IDN: {found['hioki'].idn}")
        except Exception as exc:
            print(f"Hioki connect failed: {exc}")

    if not instruments:
        print("Could not connect to any instrument.")
        sys.exit(1)

    # ── Print headers ─────────────────────────────────────────────────────
    print()

    # Instruments with a single-value reader (DMMs + Hioki) share one table
    scalar_names = [n for n in instruments if n != "Yokogawa"]
    has_yokogawa = "Yokogawa" in instruments

    if scalar_names:
        col_w  = 24
        header = f"{'#':<4}" + "".join(f"  {n:<{col_w}}" for n in scalar_names)
        print(header)
        print("-" * len(header))

    if has_yokogawa:
        print(
            f"\n{'#':<4}  {'[Yokogawa]':<10}"
            f"{'Voltage':>13}  {'Current':>13}  {'Power':>13}  "
            f"{'Apparent':>13}  {'Reactive':>13}  {'PF':>8}  {'Freq':>10}"
        )
        print("-" * 106)

    # ── Readings ─────────────────────────────────────────────────────────
    for n in range(1, READINGS + 1):

        # Scalar instruments (DMMs + Hioki) — one combined row
        if scalar_names:
            row = f"{n:<4}"
            for name in scalar_names:
                inst = instruments[name]
                try:
                    row += f"  {readers[name](inst):<{col_w}}"
                except Exception:
                    row += f"  {'ERROR':<{col_w}}"
            print(row)

        # Yokogawa — separate row with all 7 power quantities
        if has_yokogawa:
            try:
                r = instruments["Yokogawa"].read_power()
                print(
                    f"{n if not scalar_names else ' ':<4}  {'':10}"
                    f"{_fmt(r.voltage_V,    'V  ', 10, 4)}"
                    f"{_fmt(r.current_A,    'A  ', 10, 4)}"
                    f"{_fmt(r.power_W,      'W  ', 10, 3)}"
                    f"{_fmt(r.apparent_VA,  'VA ', 10, 3)}"
                    f"{_fmt(r.reactive_var, 'var', 10, 3)}"
                    f"{_fmt(r.power_factor, '   ',  7, 4)}"
                    f"{_fmt(r.frequency_Hz, 'Hz',   8, 3)}"
                )
            except Exception as exc:
                print(f"  Yokogawa ERROR: {exc}")

    # ── Cleanup ───────────────────────────────────────────────────────────
    print()
    for inst in instruments.values():
        inst.close()


if __name__ == "__main__":
    main()
