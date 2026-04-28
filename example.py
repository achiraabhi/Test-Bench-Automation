"""
example.py — Auto-discover both DMMs, configure AC voltage, read in a loop.

No resource strings to edit.  The script scans every available VISA
resource at startup, identifies the Keysight and Fluke by their *IDN?
response, then proceeds automatically.

Run:
    python example.py
"""

import logging
import sys
from pathlib import Path

from visacom import InstrumentManager, KeysightDMM, Fluke8845A
from visacom.discover import discover

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("visacom_debug.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Measurement parameters
# ---------------------------------------------------------------------------
SAMPLE_COUNT = 10       # total readings per instrument
INTERVAL_S   = 2.0      # seconds between each reading cycle
LOG_DIR      = Path("logs")


def main() -> None:
    logger.info("=== visacom example — AC voltage on two DMMs ===")

    # ------------------------------------------------------------------
    # 1. Auto-discover instruments
    # ------------------------------------------------------------------
    logger.info("Scanning for instruments...")
    found = discover()

    if "keysight" not in found:
        logger.error("Keysight DMM not found. Check USB connection and try again.")
        sys.exit(1)

    if "fluke" not in found:
        logger.error("Fluke 8845A not found. Check USB-serial adapter and try again.")
        sys.exit(1)

    keysight_resource = found["keysight"].resource_name
    fluke_resource    = found["fluke"].resource_name

    logger.info("Keysight resource : %s", keysight_resource)
    logger.info("Keysight IDN      : %s", found["keysight"].idn)
    logger.info("Fluke resource    : %s", fluke_resource)
    logger.info("Fluke IDN         : %s", found["fluke"].idn)

    # ------------------------------------------------------------------
    # 2. Connect
    # ------------------------------------------------------------------
    try:
        keysight = KeysightDMM(keysight_resource, timeout_ms=5_000)
    except Exception as exc:
        logger.error("Could not connect to Keysight: %s", exc)
        sys.exit(1)

    try:
        fluke = Fluke8845A(fluke_resource, timeout_ms=10_000)
    except Exception as exc:
        logger.error("Could not connect to Fluke: %s", exc)
        keysight.close()
        sys.exit(1)

    # ------------------------------------------------------------------
    # 3. Hand off to manager
    # ------------------------------------------------------------------
    with InstrumentManager(log_dir=LOG_DIR) as mgr:
        mgr.add_instrument("keysight", keysight)
        mgr.add_instrument("fluke", fluke)

        # ------------------------------------------------------------------
        # 4. Configure both for AC voltage
        # ------------------------------------------------------------------
        mgr.configure_all(
            keysight=lambda inst: inst.configure_ac_voltage(voltage_range="AUTO"),
            fluke=lambda inst:    inst.configure_ac_voltage(voltage_range="AUTO"),
        )

        # ------------------------------------------------------------------
        # 5. Measurement loop
        # ------------------------------------------------------------------
        print(f"\n{'Timestamp':<30} {'Keysight (V_AC)':>18} {'Fluke (V_AC)':>15}")
        print("-" * 65)

        for row in mgr.read_loop(
            readers=dict(
                keysight=lambda inst: inst.read_ac_voltage(),
                fluke=lambda inst:    inst.read_ac_voltage(),
            ),
            interval_s=INTERVAL_S,
            count=SAMPLE_COUNT,
        ):
            ks_v = row["keysight"]
            fl_v = row["fluke"]
            ks_str = f"{ks_v:.6f} V" if ks_v is not None else "ERROR"
            fl_str = f"{fl_v:.6f} V" if fl_v is not None else "ERROR"
            print(f"{row['timestamp']:<30} {ks_str:>18} {fl_str:>15}")

        mgr.get("fluke").set_local()

    logger.info("Done. CSV log saved to %s/", LOG_DIR)


if __name__ == "__main__":
    main()
