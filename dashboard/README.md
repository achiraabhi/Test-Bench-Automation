# visacom Desktop Dashboard

Native Tkinter dashboard for the visacom instrument framework.

## Stack

- Python standard-library Tkinter UI
- Background worker threads for scan and measurement loops
- PyVISA / pyvisa-py for instrument communication

## Run

```bash
pip install -r requirements.txt
python ui_server.py
```

Click **Scan** to discover instruments, **Start** to stream live readings, and **CSV** to export readings from the current session.

## Features

- Auto-discovery of supported VISA instruments via `*IDN?`
- Native desktop controls; no browser or web server required
- Noratel logo branding in the desktop header
- Measurement selection for supported DMM modes
- Per-instrument disconnect and reconnect from the sidebar
- Quick disconnect from each live card
- Live reading cards for connected instruments
- Yokogawa 7-quantity power display
- Scrollable timestamped data log
- CSV export
- Adjustable measurement interval from 0.5 s to 30 s
