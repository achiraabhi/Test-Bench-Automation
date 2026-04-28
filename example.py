"""
example.py — Initialize both DMMs, configure AC voltage, read in a loop.

Edit the two RESOURCE strings below to match your actual VISA addresses.

Run:
    python example.py
"""

import logging
import sys
from pathlib import Path

from visacom import InstrumentManager, KeysightDMM, Fluke8845A

# ---------------------------------------------------------------------------
# Logging — show INFO in the terminal, DEBUG in a file
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
# VISA resource strings — adjust to match your hardware
# ---------------------------------------------------------------------------
KEYSIGHT_RESOURCE = "USB0::0x2A8D::0x0301::MY_SERIAL::INSTR"
FLUKE_RESOURCE    = "ASRL/dev/ttyUSB0::INSTR"

# ---------------------------------------------------------------------------
# Measurement parameters
# ---------------------------------------------------------------------------
SAMPLE_COUNT  = 10        # total readings per instrument
INTERVAL_S    = 2.0       # seconds between each reading cycle
LOG_DIR       = Path("logs")   # CSV files land here; None to disable


def main() -> None:
    logger.info("=== visacom example — AC voltage on two DMMs ===")

    # ------------------------------------------------------------------
    # 1. Instantiate instruments
    # ------------------------------------------------------------------
    try:
        keysight = KeysightDMM(KEYSIGHT_RESOURCE, timeout_ms=5_000)
    except Exception as exc:
        logger.error("Could not connect to Keysight: %s", exc)
        sys.exit(1)

    try:
        fluke = Fluke8845A(FLUKE_RESOURCE, timeout_ms=10_000)
    except Exception as exc:
        logger.error("Could not connect to Fluke: %s", exc)
        keysight.close()
        sys.exit(1)

    # ------------------------------------------------------------------
    # 2. Register with manager (manager.close_all() in __exit__ handles teardown)
    # ------------------------------------------------------------------
    with InstrumentManager(log_dir=LOG_DIR) as mgr:
        mgr.add_instrument("keysight", keysight)
        mgr.add_instrument("fluke", fluke)

        # ------------------------------------------------------------------
        # 3. Optional: print *IDN? strings to verify we're talking to the
        #    right instruments before touching any measurement commands.
        # ------------------------------------------------------------------
        identities = mgr.identify_all()
        for name, idn in identities.items():
            logger.info("IDN [%s]: %s", name, idn)

        # ------------------------------------------------------------------
        # 4. Configure both DMMs for AC voltage
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
            keysight_v = row["keysight"]
            fluke_v    = row["fluke"]

            ks_str  = f"{keysight_v:.6f} V" if keysight_v is not None else "ERROR"
            fl_str  = f"{fluke_v:.6f} V"    if fluke_v    is not None else "ERROR"

            print(f"{row['timestamp']:<30} {ks_str:>18} {fl_str:>15}")

        # ------------------------------------------------------------------
        # 6. Return Fluke to local control before closing
        #    (handled automatically inside Fluke8845A.close(), but shown here
        #    explicitly for clarity)
        # ------------------------------------------------------------------
        mgr.get("fluke").set_local()

    logger.info("Done. CSV log saved to %s/", LOG_DIR)


if __name__ == "__main__":
    main()
