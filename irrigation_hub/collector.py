import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request

load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

VM_WRITE_URL = os.getenv("VM_URL", "http://localhost:8428") + "/write"

PW_KEY = os.getenv("PIRATE_WEATHER_API_KEY", "")
PW_LAT = os.getenv("PIRATE_WEATHER_LAT", "55.6761")
PW_LON = os.getenv("PIRATE_WEATHER_LON", "12.5683")
PW_URL = f"https://api.pirateweather.net/forecast/{PW_KEY}/{PW_LAT},{PW_LON}"


def _lp(measurement: str, fields: dict, ts_seconds: int) -> str:
    """Build a single InfluxDB line protocol string for VictoriaMetrics."""
    field_str = ",".join(f"{k}={v}" for k, v in fields.items())
    return f"{measurement} {field_str} {ts_seconds}000000000"


async def _vm_write(lines: list[str]) -> None:
    """POST line protocol records to VictoriaMetrics."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(VM_WRITE_URL, content="\n".join(lines))
        resp.raise_for_status()


async def poll_weather() -> None:
    """Fetch Pirate Weather once per hour and store the next 12 hours."""
    while True:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    PW_URL,
                    params={"exclude": "minutely,daily,alerts,flags"},
                )
                resp.raise_for_status()
                data = resp.json()

            lines = []
            now = data.get("currently", {})
            now_ts = int(now.get("time", time.time()))
            current_fields = {
                "precip_intensity":   float(now.get("precipIntensity",  0)),
                "precip_probability": float(now.get("precipProbability", 0)),
                "temp_c":             round((float(now.get("temperature", 32)) - 32) * 5 / 9, 2),
                "humidity":           round(float(now.get("humidity", 0)) * 100, 1),
                "wind_speed":         float(now.get("windSpeed",  0)),
                "uv_index":           float(now.get("uvIndex",    0)),
                "cloud_cover":        round(float(now.get("cloudCover", 0)) * 100, 1),
            }
            lines.append(_lp("weather_current", current_fields, now_ts))

            hourly = data.get("hourly", {}).get("data", [])[:12]
            for hour in hourly:
                forecast_fields = {
                    "precip_probability": float(hour.get("precipProbability", 0)),
                    "precip_intensity":   float(hour.get("precipIntensity",   0)),
                    "temp_c":             round((float(hour.get("temperature", 32)) - 32) * 5 / 9, 2),
                    "wind_speed":         float(hour.get("windSpeed", 0)),
                }
                lines.append(_lp("weather_forecast", forecast_fields, int(hour["time"])))

            
            await _vm_write(lines)
            log.info(f"Weather: wrote current conditions + {len(hourly)} forecast points")

        except Exception as exc:
            log.warning(f"Weather poll failed: {exc}")

        await asyncio.sleep(3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(poll_weather())
    yield
    task.cancel()


app = FastAPI(lifespan=lifespan, redirect_slashes=False)


@app.post("/data/report")
@app.post("/data/report/")
async def ecowitt_report(request: Request):
    """
    Parse Ecowitt form POST and write to VictoriaMetrics.

    Ecowitt sends application/x-www-form-urlencoded.
    All values arrive as strings — we cast defensively.
    """
    form = await request.form()
    log.info(f"Raw payload: {dict(form)}")

    def _f(key: str) -> float | None:
        try:
            return float(form[key])
        except (KeyError, ValueError, TypeError):
            return None

    fields = {}

    if (v := _f("soilmoisture1")) is not None:
        fields["soil_moisture_1"] = v
    if (v := _f("soilmoisture2")) is not None:
        fields["soil_moisture_2"] = v
    if (v := _f("tempinf")) is not None:
        fields["temp_f"] = v
        fields["temp_c"] = round((v - 32) * 5 / 9, 2)
    if (v := _f("humidityin")) is not None:
        fields["humidity"] = v
    if (v := _f("baromrelin")) is not None:
        fields["pressure_rel"] = v
        fields["pressure_hpa"] = round(v * 33.8639, 1)
    if (v := _f("soilbatt1")) is not None:
        fields["soil_battery_1"] = v
    if (v := _f("soilbatt2")) is not None:
        fields["soil_battery_2"] = v
    if (v := _f("soilad1")) is not None:
        fields["soil_adc_1"] = v
    if (v := _f("soilad2")) is not None:
        fields["soil_adc_2"] = v

    if not fields:
        log.warning("Ecowitt POST contained no recognised fields")
        return {"status": "ok", "written": 0}

    try:
        now = int(time.time())
        await _vm_write([_lp("ecowitt_sensors", fields, now)])
        log.info(f"Sensor write: {fields}")
        return {"status": "ok", "written": len(fields)}
    except Exception as exc:
        log.error(f"VM write failed: {exc}")
        return {"status": "error", "detail": str(exc)}


@app.get("/healthz")
def health():
    return {"status": "ok"}
