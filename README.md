# visacom

A Python instrumentation framework for controlling lab devices via SCPI over PyVISA, with a real-time web dashboard.

Built for Raspberry Pi (Linux, Debian-based), Python 3.11, using the pyvisa-py pure-Python backend — no NI-VISA required.

---

## Repository Structure

```
visacom/
├── instruments/        Instrument library — SCPI drivers, examples, diagnostics
│   ├── visacom/        Python package (KeysightDMM, Fluke8845A, YokogawaWT310, HiokiRM3545)
│   ├── example.py      Two-DMM example with CSV logging
│   ├── example2.py     Auto-detect any instrument, print 10 readings
│   ├── diagnose.py     Layer-by-layer connection diagnostic tool
│   ├── requirements.txt
│   └── README.md       Full instrument and driver documentation
└── dashboard/          Web dashboard — real-time browser UI
    ├── ui_server.py    FastAPI backend with WebSocket streaming
    ├── static/
    │   └── index.html  Frontend (Bootstrap 5 + Chart.js, no build step)
    ├── requirements.txt
    └── README.md
```

---

## Supported Instruments

| Instrument            | Type              | Interface                   |
|-----------------------|-------------------|-----------------------------|
| Keysight 344xxA DMM   | Digital Multimeter | USB-TMC                    |
| Fluke 8845A / 8846A   | Digital Multimeter | RS-232 via USB adapter     |
| Yokogawa WT310 / WT310E | Power Meter      | USB-TMC                    |
| Hioki RM3544 / RM3545 | Resistance Meter  | RS-232 / USB virtual COM   |

---

## Quick Start

### Instrument Library

```bash
cd instruments
pip install -r requirements.txt
python example2.py        # auto-detect any connected instrument
python diagnose.py        # run diagnostics on all found instruments
```

### Web Dashboard

```bash
cd dashboard
pip install -r requirements.txt
python ui_server.py       # opens at http://localhost:8080
```

Then open `http://<pi-ip>:8080` in any browser. Click **Scan** to discover instruments and **Start** to stream live readings.

---

## License

MIT
