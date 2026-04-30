# visacom

A Python instrumentation framework for controlling lab devices via SCPI over PyVISA, with a native Tkinter desktop dashboard.

Built for Raspberry Pi and desktop Python 3.11 using the pyvisa-py pure-Python backend, with no NI-VISA requirement.

## Repository Structure

```text
visacom/
|-- instruments/        Instrument library: SCPI drivers, examples, diagnostics
|   |-- visacom/        Python package (KeysightDMM, Fluke8845A, YokogawaWT310, HiokiRM3545)
|   |-- example.py      Two-DMM example with CSV logging
|   |-- example2.py     Auto-detect any instrument, print 10 readings
|   |-- diagnose.py     Layer-by-layer connection diagnostic tool
|   |-- requirements.txt
|   `-- README.md       Full instrument and driver documentation
`-- dashboard/          Tkinter desktop dashboard
    |-- ui_server.py    Native desktop application entry point
    |-- requirements.txt
    `-- README.md
```

## Supported Instruments

| Instrument | Type | Interface |
| --- | --- | --- |
| Keysight 344xxA DMM | Digital Multimeter | USB-TMC |
| Fluke 8845A / 8846A | Digital Multimeter | RS-232 via USB adapter |
| Yokogawa WT310 / WT310E | Power Meter | USB-TMC |
| Hioki RM3544 / RM3545 | Resistance Meter | RS-232 / USB virtual COM |

## Quick Start

### Instrument Library

```bash
cd instruments
pip install -r requirements.txt
python example2.py
python diagnose.py
```

### Desktop Dashboard

```bash
cd dashboard
pip install -r requirements.txt
python ui_server.py
```

Click **Scan** to discover instruments and **Start** to stream live readings.

## License

MIT
