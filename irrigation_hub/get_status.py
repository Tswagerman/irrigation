#!/home/sodas/venv/irrigation_hub/bin/python
import httpx
import sys
from datetime import datetime, timedelta

BASE_URL = "http://localhost:8428"
VM_URL   = BASE_URL

MOISTURE_LOW_PRIMARY   = 55.0
MOISTURE_LOW_SECONDARY = 45.0
MOISTURE_HIGH          = 75.0


def q(promql: str) -> float | None:
    try:
        resp = httpx.get(f"{VM_URL}/api/v1/query", params={"query": promql}, timeout=10)
        resp.raise_for_status()
        result = resp.json().get("data", {}).get("result", [])
        if result:
            return float(result[0]["value"][1])
    except Exception:
        return None
    return None


def fmt(val, suffix="", decimals=1, na="N/A"):
    if val is None:
        return na
    return f"{round(val, decimals)}{suffix}"


def bar(pct, width=20, fill="█", empty="░"):
    if pct is None:
        return "N/A"
    filled = int((pct / 100) * width)
    return fill * filled + empty * (width - filled)


def moisture_status(moisture, threshold):
    if moisture is None:
        return "❓ No data"
    if moisture >= MOISTURE_HIGH:
        return "💧 Saturated"
    if moisture >= threshold:
        return "✅ OK"
    return "🌵 Dry — needs water"


def rain_status(rain_now, rain_prob, rain_int):
    parts = []
    if rain_now and rain_now > 0.01:
        parts.append(f"🌧  Raining now ({rain_now:.2f} mm/h)")
    if rain_prob and rain_prob > 0.3:
        parts.append(f"⛅ {int(rain_prob*100)}% chance of rain in next 12h")
    if rain_int and rain_int > 0.1:
        parts.append(f"🌂 Forecast intensity: {rain_int:.2f} mm/h")
    return parts if parts else ["☀️  No rain expected"]


def main():
    now = datetime.now()
    hour = now.hour

    print()
    print("=" * 52)
    print("  🌱  IRRIGATION SYSTEM STATUS")
    print(f"  {now.strftime('%A %d %b %Y  %H:%M:%S')}")
    print("=" * 52)

    s1 = q("avg_over_time(ecowitt_sensors_soil_moisture_1[5m])")
    s2 = q("avg_over_time(ecowitt_sensors_soil_moisture_2[5m])")
    available = [v for v in [s1, s2] if v is not None]
    avg = round(sum(available) / len(available), 1) if available else None
    threshold = MOISTURE_LOW_PRIMARY if 6 <= hour < 10 else MOISTURE_LOW_SECONDARY

    print()
    print("  🌍 SOIL MOISTURE")
    print(f"  Sensor 1 : {fmt(s1, '%', 1):>7}  {bar(s1)}")
    print(f"  Sensor 2 : {fmt(s2, '%', 1):>7}  {bar(s2)}")
    print(f"  Average  : {fmt(avg, '%', 1):>7}  {bar(avg)}")
    print(f"  Status   : {moisture_status(avg, threshold)}")
    print(f"  Threshold: {threshold}% (high: {MOISTURE_HIGH}%)")

    b1 = q("last_over_time(ecowitt_sensors_soil_battery_1[1h])")
    b2 = q("last_over_time(ecowitt_sensors_soil_battery_2[1h])")
    print()
    print("  🔋 SENSOR BATTERIES")
    print(f"  Sensor 1 : {fmt(b1, 'V', 2)}")
    print(f"  Sensor 2 : {fmt(b2, 'V', 2)}")

    temp      = q("last_over_time(weather_current_temp_c[2h])")
    humidity  = q("last_over_time(weather_current_humidity[2h])")
    wind      = q("last_over_time(weather_current_wind_speed[2h])")
    uv        = q("last_over_time(weather_current_uv_index[2h])")
    cloud     = q("last_over_time(weather_current_cloud_cover[2h])")
    rain_now  = q("last_over_time(weather_current_precip_intensity[2h])")
    rain_prob = q("max_over_time(weather_forecast_precip_probability[12h])")
    rain_int  = q("max_over_time(weather_forecast_precip_intensity[12h])")

    print()
    print("  🌤  WEATHER")
    print(f"  Temp     : {fmt(temp, '°C')}")
    print(f"  Humidity : {fmt(humidity, '%', 0)}")
    print(f"  Wind     : {fmt(wind, ' km/h')}")
    print(f"  UV Index : {fmt(uv, '', 0)}")
    print(f"  Cloud    : {fmt(cloud, '%', 0)}")
    for line in rain_status(rain_now, rain_prob, rain_int):
        print(f"  {line}")

    last_action    = q("last_over_time(irrigation_logic_action[10m])")
    last_triggered = q("last_over_time(irrigation_logic_triggered[10m])")
    last_duration  = q("last_over_time(irrigation_logic_session_duration_s[10m])")
    reason_moisture_high = q("last_over_time(irrigation_logic_reason_moisture_high[10m])")
    reason_max_dur       = q("last_over_time(irrigation_logic_reason_max_duration[10m])")

    print()
    print("  💧 IRRIGATION")

    is_watering = last_action == 1.0 and last_triggered is None
    if is_watering:
        dur = int(last_duration) if last_duration else 0
        print(f"  Status   : 🚿 WATERING  ({timedelta(seconds=dur)} elapsed)")
    else:
        print(f"  Status   : ⏸  Idle")

    reasons = []
    if avg is not None and avg >= threshold and not is_watering:
        reasons.append(f"moisture OK ({avg}% ≥ {threshold}%)")
    if avg is not None and avg < threshold and not is_watering:
        reasons.append(f"moisture low ({avg}% < {threshold}%) but outside window")
    if reason_moisture_high == 1:
        reasons.append("stopped — moisture reached target")
    if reason_max_dur == 1:
        reasons.append("stopped — max duration reached")

    if reasons:
        print(f"  Reason   : {', '.join(reasons)}")

    in_window = any(s <= hour < e for s, e in [(0, 24)])
    print(f"  Window   : {'✅ Active' if in_window else '⏰ Outside window'} (hour={hour})")

    print()
    print("=" * 52)
    print()


if __name__ == "__main__":
    main() 