"""
example2.py — Read 10 AC voltage readings from whatever DMM(s) are connected.

Works with one or both instruments plugged in.  No CSV output — results
are printed to the terminal only.

Run:
    python example2.py
"""

import logging
import sys

from visacom import KeysightDMM, Fluke8845A
from visacom.discover import discover

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)

READINGS = 10


def main() -> None:
    found = discover()

    if not found:
        print("No instruments found. Check connections and try again.")
        sys.exit(1)

    # Connect to whichever instruments were discovered
    instruments = {}

    if "keysight" in found:
        try:
            inst = KeysightDMM(found["keysight"].resource_name, timeout_ms=5_000)
            inst.configure_ac_voltage()
            instruments["Keysight"] = inst
            print(f"Connected: Keysight  [{found['keysight'].resource_name}]")
        except Exception as exc:
            print(f"Keysight connect failed: {exc}")

    if "fluke" in found:
        try:
            inst = Fluke8845A(found["fluke"].resource_name, timeout_ms=10_000)
            inst.configure_ac_voltage()
            instruments["Fluke"] = inst
            print(f"Connected: Fluke     [{found['fluke'].resource_name}]")
        except Exception as exc:
            print(f"Fluke connect failed: {exc}")

    if not instruments:
        print("Could not connect to any instrument.")
        sys.exit(1)

    # Header
    col_width = 20
    header = f"{'#':<5}" + "".join(f"{name:>{col_width}}" for name in instruments)
    print(f"\n{header}")
    print("-" * len(header))

    # 10 readings
    readers = {
        "Keysight": lambda i: i.read_ac_voltage(),
        "Fluke":    lambda i: i.read_ac_voltage(),
    }

    for n in range(1, READINGS + 1):
        row = f"{n:<5}"
        for name, inst in instruments.items():
            try:
                val = readers[name](inst)
                row += f"{val:>{col_width - 3}.6f} V  "
            except Exception as exc:
                row += f"{'ERROR':>{col_width}}"
        print(row)

    # Cleanup
    for inst in instruments.values():
        inst.close()


if __name__ == "__main__":
    main()
