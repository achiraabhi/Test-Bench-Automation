# visacom Dashboard

Real-time web dashboard for the visacom instrument framework.

## Stack

- **Backend** — FastAPI + WebSocket (streaming readings to all connected browsers)
- **Frontend** — Plain HTML / Bootstrap 5 / Chart.js (no build step, single file)

## Run

```bash
pip install -r requirements.txt
python ui_server.py                            # http://0.0.0.0:8080
python ui_server.py --host 127.0.0.1 --port 5000
```

Open `http://localhost:8080` locally, or `http://<raspberry-pi-ip>:8080` from any device on the network.

## Features

- **Auto-discovery** — Scan button probes all VISA resources via `*IDN?`
- **Live readings** — WebSocket pushes every measurement to all open browser tabs instantly
- **Measurement selection** — Per-instrument dropdown to switch AC Voltage / DC Voltage / Resistance
- **History chart** — Rolling line chart of the last 100 readings per instrument
- **Yokogawa panel** — Full 7-quantity power display (V, I, W, VA, var, PF, Hz)
- **Data log** — Scrollable timestamped table of all readings in the session
- **CSV export** — One-click download of all readings from the current session
- **Adjustable interval** — Slider from 0.5 s to 30 s
- **Multi-browser** — Any number of browser tabs can connect simultaneously

## API Endpoints

| Method | Path                          | Description                        |
|--------|-------------------------------|------------------------------------|
| GET    | `/api/scan`                   | Discover and connect all instruments |
| POST   | `/api/start`                  | Start measurement loop             |
| POST   | `/api/stop`                   | Stop measurement loop              |
| POST   | `/api/interval/{seconds}`     | Set measurement interval           |
| POST   | `/api/measure/{label}/{type}` | Change measurement type            |
| GET    | `/api/export`                 | Download readings as CSV           |
| POST   | `/api/clear`                  | Clear reading history              |
| WS     | `/ws`                         | WebSocket — real-time readings     |
