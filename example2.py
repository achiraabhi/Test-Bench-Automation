"""
example2.py — Read 10 measurements from whatever instruments are connected.

Works with any combination of Keysight DMM, Fluke 8845A, and Yokogawa WT310.
No CSV output — results are printed to the terminal only.

Run:
    python example2.py
"""

import logging
import sys

from visacom import KeysightDMM, Fluke8845A, YokogawaWT310
from visacom.discover import discover

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)

READINGS = 10


def _fmt(value, unit, width=12, decimals=6):
    if value is None:
        return f"{'---':>{width}}      "
    return f"{value:>{width}.{decimals}f} {unit}"


def main() -> None:
    found = discover()

    if not found:
        print("No instruments found. Check connections and try again.")
        sys.exit(1)

    instruments = {}   # name → instrument instance
    readers     = {}   # name → callable that returns a printable string

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
            readers["Yokogawa"] = lambda i: str(i.read_power())
            print(f"Connected : Yokogawa   [{found['yokogawa'].resource_name}]")
            print(f"           IDN: {found['yokogawa'].idn}")
        except Exception as exc:
            print(f"Yokogawa connect failed: {exc}")

    if not instruments:
        print("Could not connect to any instrument.")
        sys.exit(1)

    # ── Print header ──────────────────────────────────────────────────────
    print()
    has_yokogawa = "Yokogawa" in instruments
    has_dmm      = "Keysight" in instruments or "Fluke" in instruments

    if has_dmm:
        dmm_cols = "".join(f"  {name:<20}" for name in instruments if name != "Yokogawa")
        print(f"{'#':<4}  {dmm_cols}")
        print("-" * max(40, len(dmm_cols) + 6))

    if has_yokogawa:
        print(f"{'#':<4}  {'Voltage':>13}  {'Current':>13}  {'Power':>13}  "
              f"{'Apparent':>13}  {'Reactive':>13}  {'PF':>8}  {'Freq':>10}")
        print("-" * 100)

    # ── Readings ─────────────────────────────────────────────────────────
    for n in range(1, READINGS + 1):

        # DMM row (Keysight and/or Fluke on one line)
        dmm_names = [name for name in instruments if name != "Yokogawa"]
        if dmm_names:
            row = f"{n:<4}"
            for name in dmm_names:
                inst = instruments[name]
                try:
                    row += f"  {readers[name](inst):<22}"
                except Exception:
                    row += f"  {'ERROR':<22}"
            print(row)

        # Yokogawa row (separate line with all 7 quantities)
        if "Yokogawa" in instruments:
            try:
                r = instruments["Yokogawa"].read_power()
                print(
                    f"{n if not dmm_names else ' ':<4}  "
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
