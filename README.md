# Sodas Irrigation System

A smart, reactive irrigation system running on a Raspberry Pi Zero 2W. Collects soil moisture and weather data, makes autonomous watering decisions, and controls a 12V solenoid valve via a relay.

## Architecture
```
Ecowitt GW1200 (sensors) ──→ collector.py (FastAPI) ──→ VictoriaMetrics
Pirate Weather API ────────→ collector.py            ──→ VictoriaMetrics
                                                             ↓
                                                         Grafana
                                                         logic.py ──→ valve_driver.py ──→ Relay ──→ Valve
```

## Hardware

- Raspberry Pi Zero 2W (512MB RAM)
- Ecowitt GW1200 gateway
- 2x Ecowitt WH51 soil moisture sensors
- 12V NC (normally closed) solenoid valve
- Single-channel active-low relay board (BCM pin 18)

**Wiring:** Relay COM + NO contact. Relay energized = valve powered = water flows. Pi crash or power loss = relay off = valve closes. Fail safe.

## Project Structure
```
~/src/
    irrigation/
        valve_driver.py         # GPIO relay control + HTTP server on port 8001
    irrigation_hub/
        collector.py            # FastAPI ingestion on port 8000
        logic.py                # Reactive irrigation brain
        docker-compose.yml      # VictoriaMetrics + Grafana
        .env                    # Secrets — never commit this
        .env.example            # Template for secrets
~/venv/
    irrigation/                 # RPi.GPIO
    irrigation_hub/             # fastapi, uvicorn, httpx, python-dotenv, python-multipart
```

## Setup

### 1. Clone the repo
```bash
git clone git@github.com:Tswagerman/irrigation.git ~/src
```

### 2. Create virtual environments
```bash
python -m venv ~/venv/irrigation_hub
python -m venv ~/venv/irrigation

source ~/venv/irrigation_hub/bin/activate
pip install fastapi uvicorn httpx python-dotenv python-multipart

source ~/venv/irrigation/bin/activate
pip install RPi.GPIO
```

### 3. Configure environment
```bash
cp ~/src/irrigation_hub/.env.example ~/src/irrigation_hub/.env
nano ~/src/irrigation_hub/.env
```

Fill in your Pirate Weather API key and coordinates.

### 4. Start Docker stack
```bash
cd ~/src/irrigation_hub
docker compose up -d
```

VictoriaMetrics: `http://<pi-ip>:8428`
Grafana: `http://<pi-ip>:3000`

### 5. Configure Grafana data source

- Type: **Prometheus**
- URL: `http://victoriametrics:8428`

### 6. Enable systemd services
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now irrigation-collector irrigation-valve irrigation-logic
sudo systemctl enable docker
```

## Services

| Service | Description | Port |
|---|---|---|
| `irrigation-collector` | Receives Ecowitt POSTs, polls weather | 8000 |
| `irrigation-valve` | Controls relay via GPIO | 8001 |
| `irrigation-logic` | Runs irrigation decision loop | — |
| VictoriaMetrics | Time-series database | 8428 |
| Grafana | Dashboard | 3000 |

All services restart automatically on failure and on boot.

## Ecowitt Gateway Configuration

In the Ecowitt gateway web UI under **Weather Services → Customized**:
- Enable: **Yes**
- Protocol: **Ecowitt**
- Server IP: `<pi-ip>`
- Path: `/data/report/`
- Port: `8000`
- Upload interval: `60` seconds

## Irrigation Logic

The logic runs every 60 seconds and evaluates:

1. Is the current time inside a watering window?
2. Is soil moisture below the threshold for this window?
3. Is it not currently raining?
4. Is rain not forecast in the next 12 hours?
5. Has the cooldown period elapsed since the last session?

If all conditions are met the valve opens. It closes when moisture reaches the target, max duration is reached, or rain is detected.

### Tunable thresholds in `logic.py`
```python
MOISTURE_THRESHOLD_LOW_PRIMARY   = 45.0  # % — morning window trigger
MOISTURE_THRESHOLD_LOW_SECONDARY = 35.0  # % — afternoon window trigger
MOISTURE_THRESHOLD_HIGH          = 60.0  # % — stop watering
RAIN_PROB_THRESHOLD              = 0.50  # skip if forecast exceeds this
RAIN_INT_THRESHOLD               = 2.5   # mm/hr forecast
CURRENT_RAIN_THRESHOLD           = 0.5   # mm/hr actual rain
MAX_WATER_DURATION               = 10 * 60  # seconds per session
COOLDOWN_MINUTES                 = 60    # minimum time between sessions
WATER_WINDOWS = [
    (6,  10),   # primary — early morning
    (17, 19),   # secondary — late afternoon
]
CHECK_INTERVAL_SECONDS = 60
```

## Metrics in VictoriaMetrics

### Sensor data (from Ecowitt)
| Metric | Description |
|---|---|
| `ecowitt_sensors_soil_moisture_1` / `_2` | Soil moisture % |
| `ecowitt_sensors_soil_battery_1` / `_2` | Sensor battery voltage |
| `ecowitt_sensors_soil_adc_1` / `_2` | Raw ADC value |
| `ecowitt_sensors_temp_c` / `_f` | Temperature |
| `ecowitt_sensors_humidity` | Humidity % |
| `ecowitt_sensors_pressure_rel` | Pressure inHg |
| `ecowitt_sensors_pressure_hpa` | Pressure hPa |

### Weather (from Pirate Weather)
| Metric | Description |
|---|---|
| `weather_current_precip_intensity` | Current rain mm/hr |
| `weather_current_precip_probability` | Current rain probability |
| `weather_current_temp_c` | Current temperature |
| `weather_current_humidity` | Current humidity % |
| `weather_current_wind_speed` | Wind speed |
| `weather_current_uv_index` | UV index |
| `weather_forecast_precip_probability` | 12h forecast probability |
| `weather_forecast_precip_intensity` | 12h forecast intensity |

### Logic events
| Metric | Description |
|---|---|
| `irrigation_logic_action` | 1 = valve open, 0 = closed |
| `irrigation_logic_triggered` | 1 = watering session started |
| `irrigation_logic_moisture` | Moisture at decision time |
| `irrigation_logic_session_duration_s` | Active session duration |

## Useful Commands
```bash
# View live logs
sudo journalctl -u irrigation-collector -f
sudo journalctl -u irrigation-valve -f
sudo journalctl -u irrigation-logic -f

# Restart a service
sudo systemctl restart irrigation-logic

# Test valve manually
curl -X POST http://localhost:8001/open
curl -X POST http://localhost:8001/close
curl http://localhost:8001/status

# Check all metrics
curl http://localhost:8428/api/v1/label/__name__/values

# Docker
cd ~/src/irrigation_hub
docker compose up -d
docker compose ps
```

## Grafana Dashboard Panels

- **State timeline** — valve open/closed history (`irrigation_logic_action`)
- **Time series** — soil moisture overlaid with valve state
- **Stat** — current moisture, today's watering count, battery voltage
- **Gauge** — battery health (green >1.3V, orange 1.0–1.3V, red <1.0V)
- **Time series** — rain forecast probability and intensity

## Future Improvements

- Pair outdoor WH32 temperature/humidity sensor
- Grafana alerts for low moisture, low battery, collector down
- Pi CPU temperature monitoring
- VPD (Vapour Pressure Deficit) calculation
- Tune thresholds after observing real drying curves with strawberries
