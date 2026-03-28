import logging
import os

import httpx
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ── Thresholds — tune these ────────────────────────────────────────────────────
MOISTURE_THRESHOLD      = 25.0   # %
RAIN_PROB_THRESHOLD     = 0.50   # 0.0 – 1.0
RAIN_INT_THRESHOLD      = 2.5    # mm/hr forecast
CURRENT_RAIN_THRESHOLD  = 0.5    # mm/hr actually raining right now

# ── Config ─────────────────────────────────────────────────────────────────────
VM_URL    = os.getenv("VM_URL",           "http://localhost:8428")
VALVE_URL = os.getenv("VALVE_DRIVER_URL", "http://localhost:8001")


# ── VictoriaMetrics query ──────────────────────────────────────────────────────
def _query(promql: str) -> float | None:
    try:
        resp = httpx.get(
            f"{VM_URL}/api/v1/query",
            params={"query": promql},
            timeout=10,
        )
        resp.raise_for_status()
        result = resp.json().get("data", {}).get("result", [])
        if result:
            return float(result[0]["value"][1])
    except Exception as exc:
        log.warning(f"VM query failed [{promql}]: {exc}")
    return None


# ── Data fetchers ──────────────────────────────────────────────────────────────
def avg_soil_moisture() -> float | None:
    """Average of both soil sensors over the last 3 hours."""
    s1 = _query("avg_over_time(ecowitt_sensors_soil_moisture_1[3h])")
    s2 = _query("avg_over_time(ecowitt_sensors_soil_moisture_2[3h])")
    available = [v for v in [s1, s2] if v is not None]
    if not available:
        return None
    return round(sum(available) / len(available), 1)


def max_forecast_rain_prob() -> float | None:
    """Max rain probability across the next 12 hours of forecast."""
    return _query("max_over_time(weather_forecast_precip_probability[12h])")


def max_forecast_rain_intensity() -> float | None:
    """Max rain intensity across the next 12 hours of forecast."""
    return _query("max_over_time(weather_forecast_precip_intensity[12h])")


def current_rain_intensity() -> float | None:
    """Current actual rain intensity from Pirate Weather."""
    return _query("weather_current_precip_intensity")


# ── Decision function ──────────────────────────────────────────────────────────
def check_irrigation_needs() -> bool:
    """
    Returns True if irrigation is recommended.

    Conditions (ALL must be True):
      1. Avg soil moisture < MOISTURE_THRESHOLD
      2. Not actively raining right now
      3. Max forecast rain probability < RAIN_PROB_THRESHOLD
      4. Max forecast rain intensity < RAIN_INT_THRESHOLD

    Returns False if moisture data is unavailable (fail safe).
    """
    moisture    = avg_soil_moisture()
    rain_now    = current_rain_intensity()
    rain_prob   = max_forecast_rain_prob()
    rain_int    = max_forecast_rain_intensity()

    log.info(
        f"moisture={moisture}%  "
        f"rain_now={rain_now}mm/hr  "
        f"rain_prob={rain_prob}  "
        f"rain_int={rain_int}mm/hr"
    )

    if moisture is None:
        log.warning("No soil moisture data — skipping irrigation (safe default)")
        return False

    moisture_low    = moisture  < MOISTURE_THRESHOLD
    not_raining_now = rain_now  is None or rain_now  < CURRENT_RAIN_THRESHOLD
    prob_ok         = rain_prob is None or rain_prob < RAIN_PROB_THRESHOLD
    int_ok          = rain_int  is None or rain_int  < RAIN_INT_THRESHOLD

    decision = moisture_low and not_raining_now and prob_ok and int_ok

    log.info(
        f"moisture_low={moisture_low}  "
        f"not_raining_now={not_raining_now}  "
        f"prob_ok={prob_ok}  "
        f"int_ok={int_ok}  "
        f"→ {'WATER' if decision else 'SKIP'}"
    )

    return decision


# ── Valve control ──────────────────────────────────────────────────────────────
def open_valve() -> bool:
    try:
        r = httpx.post(f"{VALVE_URL}/open", timeout=5)
        r.raise_for_status()
        log.info("Valve opened")
        return True
    except Exception as exc:
        log.error(f"Failed to open valve: {exc}")
        return False


def close_valve() -> bool:
    try:
        r = httpx.post(f"{VALVE_URL}/close", timeout=5)
        r.raise_for_status()
        log.info("Valve closed")
        return True
    except Exception as exc:
        log.error(f"Failed to close valve: {exc}")
        return False


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if check_irrigation_needs():
        log.info("Decision: IRRIGATE")
        open_valve()
    else:
        log.info("Decision: SKIP")