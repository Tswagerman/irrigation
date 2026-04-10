import logging
import os
import time
from datetime import datetime

import httpx
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

# Soil moisture thresholds (%)
MOISTURE_THRESHOLD_LOW_PRIMARY   = 40.0  # triggers watering in morning window
MOISTURE_THRESHOLD_LOW_SECONDARY = 40.0  # triggers watering in afternoon window
MOISTURE_THRESHOLD_HIGH          = 60.0  # stop watering once this is reached

# Watering session
MAX_WATER_DURATION     = 60 * 60  # (1 hour safety cap)

# Time windows — (start_hour, end_hour) in 24h format
WATER_WINDOWS = [
    (6,  10),   # primary — early morning
    (14, 18),   # secondary — afternoon
    #(0, 24),
]

# How often to check conditions
CHECK_INTERVAL_SECONDS = 60

VM_URL    = os.getenv("VM_URL",           "http://localhost:8428")
VALVE_URL = os.getenv("VALVE_DRIVER_URL", "http://localhost:8001")

is_watering: bool = False
watering_start_time: float | None = None


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


def _write_event(fields: dict) -> None:
    """Write a logic event to VictoriaMetrics for Grafana visibility."""
    try:
        field_str = ",".join(f"{k}={v}" for k, v in fields.items())
        line = f"irrigation_logic {field_str} {int(time.time())}000000000"
        httpx.post(f"{VM_URL}/write", content=line, timeout=5)
    except Exception as exc:
        log.warning(f"Failed to write event to VM: {exc}")


def avg_soil_moisture() -> float | None:
    s1 = _query("avg_over_time(ecowitt_sensors_soil_moisture_1[5m])")
    s2 = _query("avg_over_time(ecowitt_sensors_soil_moisture_2[5m])")
    available = [v for v in [s1, s2] if v is not None]
    if not available:
        return None
    return round(sum(available) / len(available), 1)


def max_forecast_rain_prob() -> float | None:
    return _query("max_over_time(weather_forecast_precip_probability[12h])")


def max_forecast_rain_intensity() -> float | None:
    return _query("max_over_time(weather_forecast_precip_intensity[12h])")


def current_rain_intensity() -> float | None:
    return _query("weather_current_precip_intensity")


def current_window() -> tuple[bool, bool, int]:
    """
    Returns (in_window, is_primary, current_hour).
    is_primary is True for the morning window, False for afternoon.
    """
    hour = datetime.now().hour
    for i, (start, end) in enumerate(WATER_WINDOWS):
        if start <= hour < end:
            return True, i == 0, hour
    return False, False, hour


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


def run_check() -> None:
    global is_watering, watering_start_time

    moisture  = avg_soil_moisture()
    rain_now  = current_rain_intensity()
    rain_prob = max_forecast_rain_prob()
    rain_int  = max_forecast_rain_intensity()
    in_window, is_primary, hour = current_window()

    threshold = MOISTURE_THRESHOLD_LOW_PRIMARY if is_primary else MOISTURE_THRESHOLD_LOW_SECONDARY

    if is_watering:
        session_duration = time.time() - watering_start_time
        reasons = []

        if moisture is not None and moisture >= MOISTURE_THRESHOLD_HIGH:
            reasons.append(f"moisture reached {moisture}% (target {MOISTURE_THRESHOLD_HIGH}%)")

        if session_duration >= MAX_WATER_DURATION:
            reasons.append(f"max duration reached ({MAX_WATER_DURATION}s)")

        if reasons:
            reason_str = ", ".join(reasons)
            log.info(f"Stopping irrigation — {reason_str}")
            close_valve()
            is_watering = False
            _write_event({
                "action": 0,
                "moisture": moisture or -1,
                "reason_moisture_high": int(moisture is not None and moisture >= MOISTURE_THRESHOLD_HIGH),
                "reason_max_duration": int(session_duration >= MAX_WATER_DURATION),
                "session_duration_s": round(session_duration),
                "rain_prob": rain_prob or 0,
                "rain_int": rain_int or 0,
                "rain_now": rain_now or 0,
            })
        else:
            log.info(
                f"Watering in progress — "
                f"moisture={moisture}% "
                f"duration={round(session_duration)}s"
            )
            _write_event({
                "action": 1,
                "moisture": moisture or -1,
                "session_duration_s": round(session_duration),
                "rain_prob": rain_prob or 0,
                "rain_int": rain_int or 0,
                "rain_now": rain_now or 0,
            })
        return

    skip_reasons = []
    start_reasons = []

    if not in_window:
        skip_reasons.append(f"outside watering window (hour={hour})")

    if moisture is None:
        skip_reasons.append("no moisture data")
    elif moisture < threshold:
        start_reasons.append(f"moisture {moisture}% below threshold {threshold}%")

    log.info(
        f"Check — hour={hour} window={in_window} primary={is_primary} "
        f"moisture={moisture}% threshold={threshold}% "
        f"rain_now={rain_now} rain_prob={rain_prob} rain_int={rain_int}"
    )

    if start_reasons and not skip_reasons:
        reason_str = ", ".join(start_reasons)
        log.info(f"Starting irrigation — {reason_str}")
        if open_valve():
            is_watering = True
            watering_start_time = time.time()
            _write_event({
                "action": 1,
                "moisture": moisture,
                "threshold": threshold,
                "threshold_high": MOISTURE_THRESHOLD_HIGH,
                "rain_prob": rain_prob or 0,
                "rain_int": rain_int or 0,
                "rain_now": rain_now or 0,
                "triggered": 1,
            })
    else:
        all_reasons = skip_reasons if skip_reasons else [f"moisture ok ({moisture}%)"]
        log.info(f"Skipping irrigation — {', '.join(all_reasons)}")
        _write_event({
            "action": 0,
            "moisture": moisture or -1,
            "threshold": threshold,
            "threshold_high": MOISTURE_THRESHOLD_HIGH,
            "rain_prob": rain_prob or 0,
            "rain_int": rain_int or 0,
            "rain_now": rain_now or 0,
            "triggered": 0,
        })


if __name__ == "__main__":
    log.info("Irrigation logic started")
    log.info(
        f"Config — "
        f"moisture_low_primary={MOISTURE_THRESHOLD_LOW_PRIMARY}% "
        f"moisture_low_secondary={MOISTURE_THRESHOLD_LOW_SECONDARY}% "
        f"moisture_high={MOISTURE_THRESHOLD_HIGH}% "
        f"max_duration={MAX_WATER_DURATION}s "
        f"windows={WATER_WINDOWS}"
    )
    while True:
        try:
            run_check()
        except Exception as exc:
            log.error(f"Unexpected error in run_check: {exc}")
        time.sleep(CHECK_INTERVAL_SECONDS)
